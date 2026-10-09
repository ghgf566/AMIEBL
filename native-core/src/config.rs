//! v1.0.0 configuration compatibility (first native migration unit).
//!
//! JSON values are intentionally retained rather than being converted to
//! rigid Rust structs: the v1.0 Python backend preserves unknown fields on
//! read, update, export and import. Rejecting unknown keys would be a regression.

use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub type ValidationResult<T> = Result<T, String>;

fn home() -> PathBuf {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn gguf_candidates(dir: &Path, found: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            gguf_candidates(&path, found);
        } else if path.is_file() {
            let name = entry.file_name().to_string_lossy().to_lowercase();
            if name.ends_with(".gguf") && !name.contains("mmproj") && !name.contains("draft") {
                found.push(path);
            }
        }
    }
}

/// Construct defaults under the current user's private home directory.
/// No hard-coded developer disk/model paths may be introduced.
pub fn default_config() -> Value {
    let home = home();
    let model_root = env::var_os("LMM_MODEL_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join("Models"));
    let mut candidates = Vec::new();
    gguf_candidates(&model_root, &mut candidates);
    candidates.sort_by_key(|path| path.to_string_lossy().to_lowercase());

    let selected = env::var("LMM_DEFAULT_MODEL_PATH")
        .ok()
        .filter(|p| !p.trim().is_empty())
        .map(PathBuf::from)
        .or_else(|| candidates.into_iter().next());
    let projector = env::var("LMM_DEFAULT_PROJECTOR")
        .ok()
        .map(PathBuf::from)
        .filter(|p| p.is_file());
    let projector_path = projector
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut models = Vec::new();
    let default_id = if selected.is_some() {
        "local-model"
    } else {
        ""
    };
    if let Some(path) = selected {
        let model_name = path
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| "Local model".into());
        models.push(json!({
            "id": "local-model", "name": model_name,
            "path": path.to_string_lossy(), "mmproj": projector_path,
            "vision": !projector_path.is_empty(), "context": 8192,
            "gpu_layers": 0, "auto_fit": true,
            "fit_target_enabled": false, "fit_target_mib": 2048,
            "cache_type": "f16", "cpu_threads": 0, "native_context": 0,
            "mtp": false, "mtp_source": "native",
            "mtp_draft_path": "", "mtp_draft_max": null, "mtp_capability": "unknown",
            "mtp_layers": 0, "keep_loaded": false, "idle_minutes": null,
            "default_profile_id": "coding", "temperature": null, "top_p": null,
            "top_k": null, "min_p": null, "reasoning_supported": false,
            "reasoning_capability": "unknown", "reasoning_efforts": [],
            "reasoning_default_effort": "", "reasoning_budget_supported": false,
            "reasoning_toggle_keys": [], "reasoning_detection": "pending"
        }));
    }

    json!({
        "schema_version": 1,
        "model_dirs": [model_root.to_string_lossy()],
        "engine_dir": env::var("LMM_ENGINE_DIR").unwrap_or_else(|_| home.join("llama.cpp").to_string_lossy().into_owned()),
        "api_port": 8080, "engine_port": 8081,
        "default_model_id": default_id,
        "default_profile_id": "coding",
        "idle_minutes": 15, "auto_start": false, "start_hidden": false,
        "close_to_tray": true, "preload": false, "log_request_bodies": false,
        "log_retention_days": 7, "vscode_abort_watch": true,
        "models": models,
        "profiles": [
            {"id":"quick-chat","name":"Quick Chat","thinking_mode":"auto","reasoning_level":"light",
             "budget_mode":"auto","thinking_budget":512,"max_tokens":4096},
            {"id":"coding","name":"Coding","thinking_mode":"auto","reasoning_level":"balanced",
             "budget_mode":"auto","thinking_budget":1536,"max_tokens":8192},
            {"id":"deep-coding","name":"Deep Coding","thinking_mode":"auto","reasoning_level":"extreme",
             "budget_mode":"auto","thinking_budget":4096,"max_tokens":12288}
        ]
    })
}

fn set_defaults(map: &mut Map<String, Value>, defaults: &Map<String, Value>) {
    for (key, value) in defaults {
        map.entry(key.clone()).or_insert_with(|| value.clone());
    }
}

fn check_number(
    value: &Value,
    name: &str,
    low: f64,
    high: f64,
    integer: bool,
) -> ValidationResult<()> {
    let Some(n) = value.as_f64() else {
        return Err(format!("{name} 必須是有效數字。"));
    };
    if !n.is_finite() || n < low || n > high || (integer && n.fract() != 0.0) {
        return Err(format!("{name} 必須介於 {low} 與 {high}。"));
    }
    Ok(())
}

fn check_bool(value: &Value, name: &str) -> ValidationResult<()> {
    if !value.is_boolean() {
        Err(format!("{name} 必須是開啟或關閉。"))
    } else {
        Ok(())
    }
}

fn is_id(value: &str, profile: bool) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 100 || !bytes[0].is_ascii_alphanumeric() {
        return false;
    }
    bytes
        .iter()
        .skip(1)
        .all(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-' || (!profile && *b == b'.'))
}

fn named_ids(items: &Value, kind: &str) -> ValidationResult<HashSet<String>> {
    let Some(items) = items.as_array() else {
        return Err(format!("{kind} 必須是清單。"));
    };
    let profile = kind == "profiles";
    let mut ids = HashSet::new();
    for item in items {
        let id = item.get("id").and_then(Value::as_str).unwrap_or("");
        if !is_id(id, profile) {
            return Err(if profile {
                "模式 ID 只能使用英文字母、數字、連字號與底線。".into()
            } else {
                "ID 只能使用英文字母、數字、點、連字號及底線。".into()
            });
        }
        if !ids.insert(id.to_owned()) {
            return Err("模型或模式 ID 不可重複。".into());
        }
        let name = item.get("name").and_then(Value::as_str).unwrap_or("");
        if name.trim().is_empty() {
            return Err("請填寫模型及模式名稱。".into());
        }
        if name.chars().count() > 200 {
            return Err("名稱最多 200 字。".into());
        }
    }
    Ok(ids)
}

fn agent_defaults(id: &str, name: &str) -> Value {
    match id {
        "quick-chat" => json!({"agent_name":"Local Quick Chat","agent_sync_mode":"preserve",
            "agent_description":"Fast local-model chat for questions and explanations.",
            "agent_tools":["web"],
            "agent_instructions":"Answer the user's question directly and concisely. Do not claim to inspect, change, or run anything."}),
        "coding" => json!({"agent_name":"Local Coding","agent_sync_mode":"preserve",
            "agent_description":"Focused local coding assistant.",
            "agent_tools":["execute","read","edit","search","web"],
            "agent_instructions":"Work directly on the user's requested coding task. Make focused changes and verify the result."}),
        "deep-coding" => json!({"agent_name":"Local Deep Coding","agent_sync_mode":"preserve",
            "agent_description":"Deep analysis for difficult coding tasks.",
            "agent_tools":["execute","read","edit","search","web"],
            "agent_instructions":"Perform deep analysis before acting on difficult coding tasks. Investigate root causes and verify conclusions."}),
        _ => json!({"agent_name":name,"agent_sync_mode":"preserve",
            "agent_description":"AMIEBL local-model agent.",
            "agent_tools":["read","search","web"],
            "agent_instructions":"Answer the user's request clearly and use the available tools only as needed."}),
    }
}

fn allowed(value: &Value, valid: &[&str]) -> bool {
    value.as_str().map(|v| valid.contains(&v)).unwrap_or(false)
}

fn validate_profile(value: &mut Value) -> ValidationResult<()> {
    let p = value.as_object_mut().ok_or("模式必須是物件。")?;
    let id = p.get("id").and_then(Value::as_str).unwrap_or("").to_owned();
    let name = p
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    set_defaults(p, agent_defaults(&id, &name).as_object().unwrap());
    if !allowed(&p["thinking_mode"], &["auto", "on", "off", "model"]) {
        return Err("無效的思考策略。".into());
    }

    if !p.contains_key("reasoning_level") {
        let legacy = p.get("effort").and_then(Value::as_str).unwrap_or("medium");
        let level = match legacy {
            "low" => "light",
            "high" => "deep",
            "xhigh" => "extreme",
            _ => "balanced",
        };
        p.insert("reasoning_level".into(), json!(level));
    }
    if !allowed(
        &p["reasoning_level"],
        &["light", "balanced", "deep", "extreme"],
    ) {
        return Err("無效的 AMIEBL 思考強度。".into());
    }
    let effort = match p["reasoning_level"].as_str().unwrap() {
        "light" => "low",
        "deep" => "high",
        "extreme" => "xhigh",
        _ => "medium",
    };
    p.insert("effort".into(), json!(effort));
    if !p.contains_key("budget_mode") {
        let mode = if p.contains_key("thinking_budget") {
            "custom"
        } else {
            "auto"
        };
        p.insert("budget_mode".into(), json!(mode));
    }
    if !allowed(&p["budget_mode"], &["auto", "custom"]) {
        return Err("無效的思考預算模式。".into());
    }
    p.entry("thinking_budget").or_insert(json!(1536));
    check_number(&p["max_tokens"], "總生成上限", 1., 1048576., true)?;
    check_number(&p["thinking_budget"], "思考預算", 0., 1048576., true)?;
    if p["budget_mode"] == "custom" && (p["thinking_mode"] == "auto" || p["thinking_mode"] == "on")
    {
        let cap = p["max_tokens"].as_f64().unwrap_or(0.0) as u64;
        let reserve = u64::min(256, u64::max(1, cap / 4));
        if (p["thinking_budget"].as_f64().unwrap_or(f64::INFINITY) as u64)
            > cap.saturating_sub(reserve)
        {
            return Err(format!(
                "自訂思考預算過高；總生成上限 {cap} 至少需保留 {reserve} tokens 給回答與工具呼叫。"
            ));
        }
    }
    if !allowed(&p["agent_sync_mode"], &["preserve", "managed"]) {
        return Err("無效的 VS Code Agent 同步方式。".into());
    }
    let agent = p["agent_name"]
        .as_str()
        .ok_or("VS Code Agent 名稱不可留白。")?;
    if agent.trim().is_empty() {
        return Err("VS Code Agent 名稱不可留白。".into());
    }
    if agent.chars().count() > 200 {
        return Err("VS Code Agent 名稱最多 200 字。".into());
    }
    let desc = p["agent_description"]
        .as_str()
        .ok_or("VS Code Agent 說明必須是 1000 字以內的文字。")?;
    if desc.chars().count() > 1000 {
        return Err("VS Code Agent 說明必須是 1000 字以內的文字。".into());
    }
    let instructions = p["agent_instructions"]
        .as_str()
        .ok_or("VS Code Agent 行為指令不可留白。")?;
    if instructions.trim().is_empty() {
        return Err("VS Code Agent 行為指令不可留白。".into());
    }
    if instructions.chars().count() > 20000 {
        return Err("VS Code Agent 行為指令最多 20000 字。".into());
    }
    let tools = p["agent_tools"]
        .as_array()
        .ok_or("VS Code Agent 工具名稱格式不正確。")?;
    if tools.iter().any(|t| {
        t.as_str()
            .map(|v| {
                v.is_empty()
                    || !v
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_.:/-".contains(&b))
            })
            .unwrap_or(true)
    }) {
        return Err("VS Code Agent 工具名稱格式不正確。".into());
    }
    Ok(())
}

fn validate_model(
    value: &mut Value,
    default_profile: &str,
    ids: &HashSet<String>,
) -> ValidationResult<()> {
    let m = value.as_object_mut().ok_or("模型必須是物件。")?;
    if !m.contains_key("fit_target_enabled") {
        m.insert("fit_target_enabled".into(), json!(true));
    }
    if !m.contains_key("reasoning_capability") {
        let legacy = m
            .get("reasoning_supported")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        m.insert(
            "reasoning_capability".into(),
            json!(if legacy { "toggle" } else { "unknown" }),
        );
        m.insert("reasoning_detection".into(), json!("legacy"));
        m.entry("reasoning_default_effort").or_insert(json!(""));
        m.entry("reasoning_budget_supported")
            .or_insert(json!(legacy));
        m.entry("reasoning_toggle_keys").or_insert(json!(if legacy {
            vec!["enable_thinking"]
        } else {
            vec![]
        }));
    }
    let defaults = json!({
        "mmproj":"","vision":false,"context":8192,"gpu_layers":0,"auto_fit":true,
        "fit_target_enabled":false,"fit_target_mib":2048,"cache_type":"f16",
        "cpu_threads":0,"native_context":0,"mtp":false,"mtp_source":"native",
        "mtp_draft_path":"","mtp_draft_max":null,"mtp_capability":"unknown","mtp_layers":0,
        "keep_loaded":false,"idle_minutes":null,"default_profile_id":default_profile,
        "temperature":null,"top_p":null,"top_k":null,"min_p":null,
        "reasoning_supported":false,"reasoning_capability":"unknown","reasoning_efforts":[],
        "reasoning_default_effort":"","reasoning_budget_supported":false,"reasoning_toggle_keys":[],
        "reasoning_detection":"pending"
    });
    set_defaults(m, defaults.as_object().unwrap());
    if m["path"]
        .as_str()
        .map(|v| v.trim().is_empty())
        .unwrap_or(true)
    {
        return Err("請指定模型檔案。".into());
    }
    if !m["mmproj"].is_string() {
        return Err("視覺模型路徑必須是文字。".into());
    }
    for key in [
        "vision",
        "auto_fit",
        "fit_target_enabled",
        "mtp",
        "keep_loaded",
        "reasoning_supported",
    ] {
        check_bool(&m[key], key)?;
    }
    if m["vision"] == true && m["mmproj"] == "" {
        return Err("啟用視覺時請指定視覺模型。".into());
    }
    for (key, name, min, max) in [
        ("context", "上下文容量", 512., 2097152.),
        ("native_context", "模型原生上下文", 0., 2097152.),
        ("cpu_threads", "CPU 執行緒上限", 0., 4096.),
        ("gpu_layers", "GPU 層數", -1., 999.),
        ("fit_target_mib", "GPU 預留記憶體", 0., 1048576.),
        ("mtp_layers", "MTP 層數", 0., 1024.),
    ] {
        check_number(&m[key], name, min, max, true)?;
    }
    if m["native_context"].as_f64().unwrap_or(0.0) > 0.0
        && m["context"].as_f64().unwrap_or(0.0) > m["native_context"].as_f64().unwrap_or(0.0)
    {
        return Err(format!(
            "上下文容量不可超過此 GGUF 宣告的原生上限 {} tokens。",
            m["native_context"]
        ));
    }
    for (key, lo, hi, integer) in [
        ("temperature", 0., 5., false),
        ("top_p", 0., 1., false),
        ("top_k", 0., 100000., true),
        ("min_p", 0., 1., false),
    ] {
        if !m[key].is_null() {
            check_number(&m[key], key, lo, hi, integer)?;
        }
    }
    if !allowed(&m["mtp_source"], &["native", "external"]) {
        return Err("MTP 來源只能是模型內建或外部 Draft。".into());
    }
    if !m["mtp_draft_path"].is_string() {
        return Err("MTP Draft 路徑必須是文字。".into());
    }
    if m["mtp"] == true
        && m["mtp_source"] == "external"
        && m["mtp_draft_path"].as_str().unwrap_or("").trim().is_empty()
    {
        return Err("使用外部 MTP Draft 時請指定 GGUF 路徑。".into());
    }
    if !allowed(
        &m["mtp_capability"],
        &["available", "unavailable", "incomplete", "unknown"],
    ) {
        return Err("無效的 MTP 能力偵測狀態。".into());
    }
    if !m["mtp_draft_max"].is_null() {
        check_number(&m["mtp_draft_max"], "MTP 最大猜測 Token", 1., 64., true)?;
    }
    if !m["idle_minutes"].is_null() {
        check_number(&m["idle_minutes"], "模型閒置時間", 0., 10080., false)?;
    }
    if !allowed(
        &m["cache_type"],
        &[
            "f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "q5_0", "q5_1", "iq4_nl",
        ],
    ) {
        return Err("不支援此 KV cache 精度。".into());
    }
    if !m["default_profile_id"]
        .as_str()
        .map(|v| ids.contains(v))
        .unwrap_or(false)
    {
        return Err("模型指定的使用模式不存在。".into());
    }
    if !allowed(
        &m["reasoning_capability"],
        &["unknown", "none", "always", "toggle"],
    ) {
        return Err("無效的 reasoning 能力狀態。".into());
    }
    let efforts = ["minimal", "low", "medium", "high", "xhigh", "max"];
    if !m["reasoning_efforts"]
        .as_array()
        .map(|v| v.iter().all(|e| allowed(e, &efforts)))
        .unwrap_or(false)
    {
        return Err("不支援的模型原生 reasoning effort 清單。".into());
    }
    let default_effort = m["reasoning_default_effort"]
        .as_str()
        .ok_or("模型預設 reasoning effort 必須是文字。")?;
    if !default_effort.is_empty() && !efforts.contains(&default_effort) {
        return Err("模型預設 reasoning effort 不在支援清單。".into());
    }
    if !m["reasoning_budget_supported"].is_boolean() {
        return Err("reasoning_budget_supported 必須是布林值。".into());
    }
    if !m["reasoning_toggle_keys"]
        .as_array()
        .map(|v| {
            v.iter().all(|e| {
                allowed(
                    e,
                    &[
                        "enable_thinking",
                        "thinking",
                        "thinking_mode",
                        "add_nothink_token",
                    ],
                )
            })
        })
        .unwrap_or(false)
    {
        return Err("無效的 reasoning toggle key。".into());
    }
    if !allowed(
        &m["reasoning_detection"],
        &["pending", "legacy", "gguf", "runtime"],
    ) {
        return Err("無效的 reasoning 能力來源。".into());
    }
    Ok(())
}

/// Startup migration is intentionally more forgiving than PUT/import:
/// only old disk configs may be repaired to fit a newly detected native context.
pub fn normalize_startup_config(mut value: Value) -> Value {
    if let Some(models) = value.get_mut("models").and_then(Value::as_array_mut) {
        for model in models {
            if let Some(map) = model.as_object_mut() {
                let context = map.get("context").and_then(Value::as_f64);
                let native = map.get("native_context").and_then(Value::as_f64);
                if let (Some(context), Some(native)) = (context, native) {
                    if context.is_finite() && native.is_finite() && native > 0. && context > native
                    {
                        map.insert(
                            "context".into(),
                            if native.fract() == 0.0 {
                                json!(native as u64)
                            } else {
                                json!(native)
                            },
                        );
                    }
                }
            }
        }
    }
    value
}

/// Complete schema v1 validation with unknown-field preservation.
pub fn validate_config(mut value: Value) -> ValidationResult<Value> {
    let defaults = default_config();
    let c = value.as_object_mut().ok_or("設定必須是 JSON 物件。")?;
    set_defaults(c, defaults.as_object().unwrap());

    if c["schema_version"] != 1 {
        return Err("不支援此設定版本。".into());
    }
    for key in ["api_port", "engine_port"] {
        check_number(&c[key], key, 1024., 65535., true)?;
    }
    if c["api_port"] == c["engine_port"] {
        return Err("API 與模型引擎必須使用不同連接埠。".into());
    }
    check_number(&c["idle_minutes"], "閒置時間", 0., 10080., false)?;
    check_number(&c["log_retention_days"], "紀錄保留天數", 1., 365., true)?;
    if !c["model_dirs"]
        .as_array()
        .map(|v| {
            v.iter()
                .all(|p| p.as_str().map(|s| !s.trim().is_empty()).unwrap_or(false))
        })
        .unwrap_or(false)
    {
        return Err("模型資料夾必須是路徑清單。".into());
    }
    if c["engine_dir"]
        .as_str()
        .map(|s| s.trim().is_empty())
        .unwrap_or(true)
    {
        return Err("請指定 llama.cpp 資料夾。".into());
    }
    for key in [
        "auto_start",
        "start_hidden",
        "close_to_tray",
        "preload",
        "log_request_bodies",
        "vscode_abort_watch",
    ] {
        check_bool(&c[key], key)?;
    }
    let model_ids = named_ids(&c["models"], "models")?;
    let profile_ids = named_ids(&c["profiles"], "profiles")?;
    if profile_ids.is_empty() {
        return Err("至少保留一個使用模式。".into());
    }
    let default_profile = c["default_profile_id"].as_str().unwrap_or("").to_owned();
    if !profile_ids.contains(&default_profile) {
        return Err("預設使用模式不存在。".into());
    }
    let default_model = c["default_model_id"].as_str().unwrap_or("").to_owned();
    if !model_ids.is_empty() && !model_ids.contains(&default_model) {
        return Err("預設模型不存在。".into());
    }
    for profile in c.get_mut("profiles").unwrap().as_array_mut().unwrap() {
        validate_profile(profile)?;
    }
    for model in c.get_mut("models").unwrap().as_array_mut().unwrap() {
        validate_model(model, &default_profile, &profile_ids)?;
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fresh_config_has_three_profiles_no_implicit_models_when_dir_is_missing() {
        let c = default_config();
        assert_eq!(c["profiles"].as_array().unwrap().len(), 3);
        assert_eq!(c["auto_start"], false);
        assert_eq!(c["preload"], false);
    }
    #[test]
    fn retains_future_properties_and_existing_user_config() {
        let mut c = default_config();
        c["future_feature"] = json!({"nested":"keep"});
        c["profiles"][0]["future_profile_field"] = json!(42);
        let out = validate_config(c).unwrap();
        assert_eq!(out["future_feature"]["nested"], "keep");
        assert_eq!(out["profiles"][0]["future_profile_field"], 42);
    }
    #[test]
    fn keeps_legacy_profile_effort_and_generates_agent_fields() {
        let mut c = default_config();
        let p = c["profiles"][0].as_object_mut().unwrap();
        p.remove("reasoning_level");
        p.remove("budget_mode");
        p.insert("effort".into(), json!("high"));
        let out = validate_config(c).unwrap();
        assert_eq!(out["profiles"][0]["reasoning_level"], "deep");
        assert_eq!(out["profiles"][0]["effort"], "high");
        assert_eq!(out["profiles"][0]["budget_mode"], "custom");
        assert_eq!(out["profiles"][0]["agent_sync_mode"], "preserve");
    }
    #[test]
    fn accepts_integer_valued_json_floats_like_python() {
        let mut c = default_config();
        c["profiles"][0]["max_tokens"] = json!(4096.0);
        c["profiles"][0]["thinking_budget"] = json!(512.0);
        assert!(validate_config(c).is_ok());
    }
    #[test]
    fn rejects_invalid_schema_ports_and_duplicate_ids() {
        let mut c = default_config();
        c["api_port"] = json!(80);
        assert!(validate_config(c.clone()).is_err());
        c["api_port"] = json!(8080);
        c["engine_port"] = json!(8080);
        assert!(validate_config(c.clone()).is_err());
        c["engine_port"] = json!(8081);
        let duplicate = c["profiles"][0].clone();
        c["profiles"].as_array_mut().unwrap().push(duplicate);
        assert!(validate_config(c).is_err());
    }
    #[test]
    fn startup_repair_does_not_loosen_strict_validation() {
        let mut c = default_config();
        c["models"] = json!([{"id":"m","name":"model","path":"C:/m.gguf","context":32768,
                             "native_context":4096,"default_profile_id":"coding"}]);
        c["default_model_id"] = json!("m");
        assert!(validate_config(c.clone()).is_err());
        let repaired = normalize_startup_config(c);
        assert_eq!(repaired["models"][0]["context"], 4096);
        assert!(validate_config(repaired).is_ok());
    }
    #[test]
    fn validates_model_options_and_keeps_disabled_feature_values() {
        let mut c = default_config();
        c["models"] = json!([{"id":"m","name":"model","path":"C:/m.gguf","context":4096,
            "vision":false,"mmproj":"C:/projector.gguf","mtp":false,
            "mtp_source":"external","mtp_draft_path":"C:/draft.gguf",
            "fit_target_enabled":true,"fit_target_mib":512,
            "custom_external_field":"retained"}]);
        c["default_model_id"] = json!("m");
        let out = validate_config(c).unwrap();
        assert_eq!(out["models"][0]["custom_external_field"], "retained");
        assert_eq!(out["models"][0]["mmproj"], "C:/projector.gguf");
        assert_eq!(out["models"][0]["mtp_draft_path"], "C:/draft.gguf");
    }
}
