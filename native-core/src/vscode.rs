//! VS Code integration port of backend/vscode_integration.py from v1.0.0.
//!
//! This module is NOT connected to the production GUI yet. It implements the
//! same physical-model picker, custom Agent files, merge, preview, backups,
//! ownership ledger and non-destructive rollback, with paths injectable for
//! isolated differential tests. Never replace v1.0.0 without parity tests.

use chrono::Local;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use tempfile::NamedTempFile;

pub fn locations() -> (PathBuf, PathBuf) {
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let appdata = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join("AppData").join("Roaming"));
    (
        appdata
            .join("Code")
            .join("User")
            .join("chatLanguageModels.json"),
        home.join(".copilot").join("agents"),
    )
}

/// Parse the subset of JSONC understood by the historical Python manager:
/// // and /* */ comments outside JSON strings and trailing commas.
pub fn read_jsonc(src: &str) -> Result<Value, String> {
    let bytes = src.trim_start_matches('\u{feff}').as_bytes();
    let mut i = 0;
    let mut quoted = false;
    let mut comments_removed = Vec::with_capacity(bytes.len());
    while i < bytes.len() {
        let b = bytes[i];
        if quoted {
            comments_removed.push(b);
            if b == b'\\' && i + 1 < bytes.len() {
                i += 1;
                comments_removed.push(bytes[i]);
            } else if b == b'"' {
                quoted = false;
            }
        } else if b == b'"' {
            quoted = true;
            comments_removed.push(b);
        } else if b == b'/' && bytes.get(i + 1) == Some(&b'/') {
            i += 2;
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
            // Python's parser also skips the terminating newline of comments.
        } else if b == b'/' && bytes.get(i + 1) == Some(&b'*') {
            i += 2;
            let mut found = false;
            while i + 1 < bytes.len() {
                if bytes[i] == b'*' && bytes[i + 1] == b'/' {
                    i += 2;
                    found = true;
                    break;
                }
                i += 1;
            }
            if !found {
                return Err("VS Code 設定含有未結束的註解。".into());
            }
            continue;
        } else {
            comments_removed.push(b);
        }
        i += 1;
    }
    let mut trailing_removed = Vec::with_capacity(comments_removed.len());
    i = 0;
    quoted = false;
    while i < comments_removed.len() {
        let b = comments_removed[i];
        if quoted {
            trailing_removed.push(b);
            if b == b'\\' && i + 1 < comments_removed.len() {
                i += 1;
                trailing_removed.push(comments_removed[i]);
            } else if b == b'"' {
                quoted = false;
            }
        } else if b == b'"' {
            quoted = true;
            trailing_removed.push(b);
        } else if b == b','
            && comments_removed[i + 1..]
                .iter()
                .copied()
                .find(|b| !b.is_ascii_whitespace())
                .map(|c| c == b']' || c == b'}')
                .unwrap_or(false)
        {
            // Drop trailing comma.
        } else {
            trailing_removed.push(b);
        }
        i += 1;
    }
    serde_json::from_slice::<Value>(&trailing_removed).map_err(|e| e.to_string())
}

fn unsigned(value: &Value, default: u64) -> u64 {
    value
        .as_u64()
        .or_else(|| value.as_f64().map(|v| v.max(0.) as u64))
        .unwrap_or(default)
}

pub fn model_entries(config: &Value) -> Value {
    let port = unsigned(&config["api_port"], 8080);
    let url = format!("http://127.0.0.1:{port}/v1/chat/completions");
    let max_profile = config["profiles"]
        .as_array()
        .map(|v| {
            v.iter()
                .map(|p| unsigned(&p["max_tokens"], 4096))
                .max()
                .unwrap_or(4096)
        })
        .unwrap_or(4096);
    let mut entries = vec![];
    for model in config["models"].as_array().into_iter().flatten() {
        let context = unsigned(&model["context"], 8192);
        let output = if model.get("output_percent").is_some() {
            crate::request::model_output_limit(model).unwrap_or((context / 4).max(1) as i64) as u64
        } else {
            max_profile.min((context / 4).max(1))
        };
        let input = context.saturating_sub(output).max(1);
        let capability = model.get("reasoning_capability");
        let thinking = if capability.is_some() {
            model["reasoning_capability"] == "toggle" || model["reasoning_capability"] == "always"
        } else {
            model["reasoning_supported"].as_bool().unwrap_or(false)
        };
        entries.push(json!({
            "id":model["id"],"name":model["name"],"url":url,
            "toolCalling":model.get("tool_calling").cloned().unwrap_or(json!(true)),
            "vision":model["vision"].as_bool().unwrap_or(false)
                && model["mmproj"].as_str().map(|s| !s.is_empty()).unwrap_or(false),
            "thinking":thinking,"streaming":true,"contextWindow":context,
            "maxOutputTokens":output,"maxInputTokens":input
        }));
    }
    Value::Array(entries)
}

pub fn agent_display_name(profile: &Value) -> &str {
    profile["name"]
        .as_str()
        .filter(|v| !v.is_empty())
        .or_else(|| profile["agent_name"].as_str().filter(|v| !v.is_empty()))
        .or_else(|| profile["id"].as_str())
        .unwrap_or("")
}

fn with_commas(value: u64) -> String {
    let digits = value.to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    out
}

pub fn preview_at(config: &Value, models_file: &Path, agents_dir: &Path) -> Value {
    let entries = model_entries(config);
    let rows = entries
        .as_array()
        .expect("model_entries always returns a JSON array");
    let profiles = config["profiles"].as_array().cloned().unwrap_or_default();
    let modern_allocation = config["models"]
        .as_array()
        .is_some_and(|models| models.iter().any(|m| m.get("output_percent").is_some()));
    let allocation_note = if modern_allocation {
        "輸入／輸出額度依模型 Context 與輸出比例換算，所有 Agent 共用；輸出包含思考與回答，客戶端仍可降低上限。"
    } else {
        "VS Code 輸出預留最多為 Context 的四分之一；不修改 Profile 上限。"
    };
    let input_warning = if modern_allocation
        && rows
            .iter()
            .any(|m| unsigned(&m["maxInputTokens"], 1) < 4096)
    {
        "\n注意：部分模型輸入額度少於 4K，Agent 工具、指令與歷史可能超限。增加 Context 或降低輸出比例可增加輸入空間；此提醒門檻不保證 Agent 可運行。"
    } else {
        ""
    };
    let limits = rows
        .iter()
        .map(|m| {
            format!(
                "{}: input {} / output {} tokens",
                m["name"].as_str().unwrap_or(""),
                with_commas(unsigned(&m["maxInputTokens"], 1)),
                with_commas(unsigned(&m["maxOutputTokens"], 1))
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    let port = unsigned(&config["api_port"], 8080);
    let profile_rows = profiles
        .iter()
        .map(|p| {
            json!({
                "id":p["id"],"name":p["name"],"agent_name":agent_display_name(p),
                "agent_sync_mode":p.get("agent_sync_mode").cloned().unwrap_or(json!("preserve")),
                "max_tokens":p["max_tokens"]
            })
        })
        .collect::<Vec<_>>();
    let tokens = rows
        .iter()
        .map(|m| {
            json!({
                "id":m["id"],"name":m["name"],"context":m["contextWindow"],
                "input":m["maxInputTokens"],"output":m["maxOutputTokens"]
            })
        })
        .collect::<Vec<_>>();
    json!({
        "ok":true,"model_count":rows.len(),"agent_count":profiles.len(),
        "token_limits":tokens,
        "api_url":format!("http://127.0.0.1:{port}/v1/chat/completions"),
        "models_file":models_file.to_string_lossy(),"agents_dir":agents_dir.to_string_lossy(),
        "default_model_id":config["default_model_id"],"profiles":profile_rows,
        "summary":format!("將在 VS Code 登錄 {} 個實體模型，並處理 {} 個 Agent。預設同步模式名稱與 AMIEBL 模式標記並保留 VS Code 手動修改；設為完整管理的 Agent 才會由 GUI 覆寫。原始設定會先備份；完成後需要重新載入 VS Code 視窗。\n{}{}\n{}",
            rows.len(), profiles.len(), allocation_note, input_warning, limits),
        "message":"VS Code 模型清單只會顯示實體模型；使用模式由 .agent.md 中的 AMIEBL profile 標記選擇。Agent 不固定 customendpoint model，請在 Agent 視窗的模型選擇器選取本機模型。"
    })
}

/// Locate YAML header boundaries without discarding CRLF or comments.
/// Returns the header range plus offset immediately after the closing '---'.
fn yaml_header(source: &str) -> Option<(std::ops::Range<usize>, usize)> {
    let regex =
        Regex::new(r"(?s)\A---[ \t]*\r?\n(?P<header>.*?)\r?\n---(?P<after>\r?\n|\z)").ok()?;
    let cap = regex.captures(source)?;
    let header = cap.name("header")?;
    let after = cap.name("after")?;
    Some((header.start()..header.end(), after.start()))
}

fn regexp(src: &str) -> Regex {
    Regex::new(src).expect("constant regex must compile")
}

pub fn update_frontmatter(source: &str, model_name: Option<&str>) -> Result<String, String> {
    let source = source.trim_start_matches('\u{feff}');
    if !source.starts_with("---") {
        return Err("agent 檔案缺少有效的 YAML 標頭，為保留現有內容已停止更新。".into());
    }
    let (header_range, delim_end) =
        yaml_header(source).ok_or("agent 檔案標頭格式不正確，已停止更新。")?;
    let mut header = source[header_range.clone()].to_owned();
    if regexp(r"(?m)^model:\s*$").is_match(&header) {
        return Err("agent 使用多行 model 設定；請先在 VS Code 將它改為單行再連接。".into());
    }
    if let Some(name) = model_name {
        let line = format!("model: {}", serde_json::to_string(name).unwrap());
        if regexp(r"(?m)^model:").is_match(&header) {
            header = regexp(r"(?m)^model:.*$")
                .replace_all(&header, line.as_str())
                .to_string();
        } else {
            header.push('\n');
            header.push_str(&line);
        }
    } else {
        header = regexp(r"(?m)^model:.*(?:\r?\n|$)")
            .replace_all(&header, "")
            .to_string();
    }
    // v1.0 update_frontmatter normalizes existing headers to LF.
    Ok(format!(
        "---\n{}\n---{}",
        header.replace("\r\n", "\n"),
        &source[delim_end..]
    ))
}

pub fn sync_agent_name(source: &str, name: &str) -> Result<String, String> {
    let (header_range, _) =
        yaml_header(source).ok_or("既有 Agent 缺少有效 YAML 標頭，已停止同步。")?;
    let header = &source[header_range.clone()];
    if regexp(r"(?m)^name:[ \t]*(?:[|>].*|)\r?$").is_match(header) {
        return Err("既有 Agent 名稱使用多行 YAML，請先改為單行再同步。".into());
    }
    let line = format!("name: {}", serde_json::to_string(name).unwrap());
    let updated = if regexp(r"(?m)^name:").is_match(header) {
        regexp(r"(?m)^name:[^\r\n]*")
            .replace_all(header, line.as_str())
            .to_string()
    } else {
        let newline = if source.contains("\r\n") {
            "\r\n"
        } else {
            "\n"
        };
        format!("{line}{newline}{header}")
    };
    Ok(format!(
        "{}{}{}",
        &source[..header_range.start],
        updated,
        &source[header_range.end..]
    ))
}

pub fn preserve_agent_profile_marker(
    source: &str,
    profile_id: &str,
    name: Option<&str>,
) -> Result<String, String> {
    let mut content = source.trim_start_matches('\u{feff}').to_owned();
    if let Some(name) = name {
        content = sync_agent_name(&content, name)?;
    }
    let marker = format!("AMIEBL_PROFILE:{profile_id}");
    let old = regexp(r"(?mi)^[ \t]*AMIEBL_PROFILE[ \t]*:[ \t]*[A-Za-z0-9_-]+[ \t]*(?P<cr>\r?)$");
    if old.is_match(&content) {
        return Ok(old
            .replace(&content, |caps: &regex::Captures| {
                format!(
                    "{}{}",
                    marker,
                    caps.name("cr").map(|v| v.as_str()).unwrap_or("")
                )
            })
            .to_string());
    }
    let (_, delim_end) = yaml_header(&content)
        .ok_or("既有 VS Code Agent 缺少有效 YAML 標頭；為避免覆蓋手動設定，已停止同步。")?;
    let newline = if content.contains("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    Ok(format!(
        "{}{}{}{}{}",
        &content[..delim_end],
        newline,
        newline,
        marker,
        &content[delim_end..]
    ))
}

pub fn render_agent(profile: &Value) -> String {
    let tools = profile["agent_tools"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|v| serde_json::to_string(v).unwrap())
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    let instructions = profile["agent_instructions"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or("Answer the user's request clearly and use the available tools only as needed.");
    format!("---\nname: {}\ndescription: {}\ntools: [{}]\nagents: []\nuser-invocable: true\ndisable-model-invocation: true\n---\n\nAMIEBL_PROFILE:{}\n\n{}\n",
        serde_json::to_string(agent_display_name(profile)).unwrap(),
        serde_json::to_string(profile["agent_description"].as_str().unwrap_or("")).unwrap(),
        tools,
        profile["id"].as_str().unwrap_or(""),
        instructions.trim())
}

fn atomic_write_bytes(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("無法取得檔案父資料夾。")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temp = NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    temp.write_all(bytes).map_err(|e| e.to_string())?;
    temp.as_file().sync_all().map_err(|e| e.to_string())?;
    temp.persist(path).map_err(|e| e.error.to_string())?;
    Ok(())
}

fn pretty_python_json(value: &Value) -> Result<String, String> {
    let mut buffer = vec![];
    let formatter = serde_json::ser::PrettyFormatter::with_indent(b"    ");
    let mut serializer = serde_json::Serializer::with_formatter(&mut buffer, formatter);
    serde::Serialize::serialize(value, &mut serializer).map_err(|e| e.to_string())?;
    String::from_utf8(buffer).map_err(|e| e.to_string())
}

/// Writes only after all profile agents and providers are fully validated.
/// Backs up affected existing files, restores their exact bytes on failure.
pub fn apply_at(
    config: &Value,
    data_dir: &Path,
    models_file: &Path,
    agents_dir: &Path,
) -> Result<Value, String> {
    let original = match fs::read(models_file) {
        Ok(bytes) => String::from_utf8(bytes).map_err(|e| e.to_string())?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => "[]".into(),
        Err(e) => return Err(e.to_string()),
    };
    let mut providers = read_jsonc(&original)?;
    let list = providers
        .as_array_mut()
        .ok_or("VS Code 模型設定不是陣列，已停止更新以保留原始內容。")?;
    let position = list
        .iter()
        .position(|p| p["vendor"] == "customendpoint" && p["name"] == "llama.cpp");
    let index = match position {
        Some(i) => i,
        None => {
            list.push(json!({"name":"llama.cpp","vendor":"customendpoint","apiType":"chat-completions","apiKey":"local"}));
            list.len() - 1
        }
    };
    let provider = list[index]
        .as_object_mut()
        .ok_or("VS Code provider 格式不正確。")?;
    provider.insert("apiType".into(), json!("chat-completions"));
    let entries = model_entries(config);
    let owned_file = data_dir.join("vscode-owned-models.json");
    let previously_owned: HashSet<String> = if owned_file.exists() {
        let text = fs::read_to_string(&owned_file).map_err(|e| e.to_string())?;
        serde_json::from_str::<Vec<String>>(&text)
            .map_err(|e| e.to_string())?
            .into_iter()
            .collect()
    } else {
        HashSet::new()
    };
    let owned: HashSet<String> = entries
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|m| m["id"].as_str().map(str::to_owned))
        .collect();
    let legacy_aliases: HashSet<String> = config["models"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|m| {
            config["profiles"]
                .as_array()
                .into_iter()
                .flatten()
                .map(move |p| {
                    format!(
                        "{}::{}",
                        m["id"].as_str().unwrap_or(""),
                        p["id"].as_str().unwrap_or("")
                    )
                })
        })
        .collect();
    let mut merged = provider
        .get("models")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    merged.retain(|m| {
        let id = m["id"].as_str().unwrap_or("");
        !owned.contains(id) && !previously_owned.contains(id) && !legacy_aliases.contains(id)
    });
    merged.extend(entries.as_array().unwrap().iter().cloned());
    provider.insert("models".into(), Value::Array(merged));

    let has_default = config["models"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|m| m["id"] == config["default_model_id"]);
    if !has_default {
        return Err("請先指定有效的預設模型。".into());
    }
    let mut outputs = vec![(
        models_file.to_path_buf(),
        format!("{}\n", pretty_python_json(&providers)?),
    )];

    for profile in config["profiles"].as_array().into_iter().flatten() {
        let id = profile["id"].as_str().unwrap_or("");
        if id.is_empty()
            || !id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err("模式 ID 只能含英文字母、數字、連字號與底線。".into());
        }
        let prefix = if ["quick-chat", "coding", "deep-coding"].contains(&id) {
            "local-"
        } else {
            "lmm-"
        };
        let path = agents_dir.join(format!("{prefix}{id}.agent.md"));
        let source = if path.exists()
            && profile["agent_sync_mode"].as_str().unwrap_or("preserve") == "preserve"
        {
            let raw = fs::read_to_string(&path).map_err(|e| e.to_string())?;
            preserve_agent_profile_marker(&raw, id, Some(agent_display_name(profile)))?
        } else {
            render_agent(profile)
        };
        outputs.push((path, source));
    }

    let stamp = Local::now().format("%Y%m%d-%H%M%S-%6f").to_string();
    let backup_dir = data_dir.join("backups").join(format!("{stamp}-vscode"));
    fs::create_dir_all(&backup_dir).map_err(|e| e.to_string())?;
    let mut backups = Vec::new();
    let mut originals: Vec<(PathBuf, Option<Vec<u8>>)> = Vec::new();
    for (path, _) in &outputs {
        let before = match fs::read(path) {
            Ok(bytes) => Some(bytes),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(e.to_string()),
        };
        if let Some(ref bytes) = before {
            let file_name = path.file_name().ok_or("無效的設定檔名。")?;
            let backup = backup_dir.join(file_name);
            fs::write(&backup, bytes).map_err(|e| e.to_string())?;
            backups.push(backup.to_string_lossy().to_string());
        }
        originals.push((path.to_path_buf(), before));
    }
    originals.push((owned_file.clone(), fs::read(&owned_file).ok()));

    let attempt = (|| -> Result<(), String> {
        for (path, body) in &outputs {
            atomic_write_bytes(path, body.as_bytes())?;
        }
        let mut ids = owned.into_iter().collect::<Vec<_>>();
        ids.sort();
        atomic_write_bytes(
            &owned_file,
            format!("{}\n", pretty_python_json(&json!(ids))?).as_bytes(),
        )?;
        Ok(())
    })();
    if let Err(error) = attempt {
        let mut failures = Vec::new();
        for (path, previous) in originals {
            let result = match previous {
                Some(bytes) => atomic_write_bytes(&path, &bytes),
                None => match fs::remove_file(&path) {
                    Ok(()) => Ok(()),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                    Err(e) => Err(e.to_string()),
                },
            };
            if let Err(e) = result {
                failures.push(format!("{}: {e}", path.display()));
            }
        }
        if !failures.is_empty() {
            return Err(format!(
                "{error}; 回復原始設定時又發生錯誤：{}",
                failures.join("; ")
            ));
        }
        return Err(error);
    }
    Ok(json!({
        "ok":true,
        "backups":backups,
        "model_count":entries.as_array().unwrap().len(),
        "agent_count":outputs.len()-1,
        "agents":outputs.iter().skip(1).map(|(p,_)| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
        "requires_reload":true,
        "message":"已備份並更新 VS Code 設定。模型選單只保留實體模型；預設同步模式同步模式名稱與 AMIEBL_PROFILE 標記並保留既有 Agent 的 tools、prompt 與其他手動設定。只有設為「由 AMIEBL 完整管理」的 Agent 會被 GUI 覆寫。請重新載入 VS Code。"
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Value {
        json!({
            "api_port":8080,"default_model_id":"qwen",
            "models":[{"id":"qwen","name":"Qwen","context":65536,"vision":true,
                       "mmproj":"mmproj.gguf"}],
            "profiles":[{"id":"coding","name":"Coding","max_tokens":8192,
                         "agent_tools":["execute","read","edit"],
                         "agent_instructions":"Write code.",
                         "agent_description":"Coding","agent_sync_mode":"preserve"}]
        })
    }

    #[test]
    fn jsonc_comments_preserve_quoted_url_credentials_and_trailing_comma() {
        let val = read_jsonc(
            "[/*comment*/{\"url\":\"http://localhost\", \"secret\":\"a/*b*/,]\\\"\",},]",
        )
        .unwrap();
        assert_eq!(val[0]["url"], "http://localhost");
        assert_eq!(val[0]["secret"], "a/*b*/,]\"");
        assert!(read_jsonc("[ /* unterminated ").is_err());
    }

    #[test]
    fn model_picker_keeps_context_room_and_detected_reasoning() {
        let mut config = fixture();
        config["models"][0]["reasoning_capability"] = json!("unknown");
        config["models"][0]["reasoning_supported"] = json!(true);
        assert_eq!(model_entries(&config)[0]["thinking"], false);
        config["models"][0]["reasoning_capability"] = json!("toggle");
        assert_eq!(model_entries(&config)[0]["thinking"], true);
        config["profiles"][0]["max_tokens"] = json!(1048576);
        assert_eq!(model_entries(&config)[0]["maxInputTokens"], 49152);
        assert_eq!(model_entries(&config)[0]["maxOutputTokens"], 16384);
        config["models"][0]["output_percent"] = json!(50);
        config["profiles"][0]["max_tokens"] = json!(1);
        assert_eq!(model_entries(&config)[0]["maxInputTokens"], 32768);
        assert_eq!(model_entries(&config)[0]["maxOutputTokens"], 32768);
    }

    #[test]
    fn preserve_existing_agent_name_crlf_tools_and_instructions() {
        let input = "---\r\nname: Quick Chat\r\ntools: [execute, custom/tool]\r\n---\r\n\r\nAMIEBL_PROFILE:coding\r\n\r\nKeep my instructions.\r\n";
        let out = preserve_agent_profile_marker(input, "coding", Some("全功能模式")).unwrap();
        assert_eq!(
            out,
            input.replace("name: Quick Chat", "name: \"全功能模式\"")
        );
    }

    #[test]
    fn rejects_multiline_agent_name_without_writing() {
        let source = "---\nname: |\n  My custom name\ntools: [read]\n---\nMy prompt.\n";
        assert!(preserve_agent_profile_marker(source, "coding", Some("Coding")).is_err());
    }

    #[test]
    fn apply_is_non_destructive_and_creates_backups() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("chatLanguageModels.json");
        let agents = root.path().join("agents");
        fs::create_dir_all(&agents).unwrap();
        let original = r#"[{"name":"Other","vendor":"other"},{"name":"llama.cpp","vendor":"customendpoint","apiKey":"retain-me","models":[{"id":"unrelated"}]}]"#;
        fs::write(&file, original).unwrap();
        let existing = agents.join("local-coding.agent.md");
        let agent = "---\nname: Local Coding\ntools: [custom/tool]\n---\n\nKeep my instructions.\n";
        fs::write(&existing, agent).unwrap();
        let before = fixture();
        let result = apply_at(&before, root.path(), &file, &agents).unwrap();
        let now: Value = serde_json::from_str(&fs::read_to_string(&file).unwrap()).unwrap();
        assert_eq!(now[0]["name"], "Other");
        assert_eq!(now[1]["apiKey"], "retain-me");
        assert_eq!(now[1]["models"][0]["id"], "unrelated");
        assert_eq!(now[1]["models"][1]["id"], "qwen");
        assert!(fs::read_to_string(&existing)
            .unwrap()
            .contains("Keep my instructions."));
        assert!(fs::read_to_string(&existing)
            .unwrap()
            .contains("custom/tool"));
        assert_eq!(result["backups"].as_array().unwrap().len(), 2);
        assert_eq!(before, fixture());
    }
}
