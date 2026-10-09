//! GGUF-only metadata and reasoning capability inspection.
//!
//! Equivalent to the v1.0.0 manager's inspection, without loading tensor
//! weights or requiring llama.cpp, Python, CUDA, or any native runtime.

use regex::Regex;
use serde_json::{json, Value};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("constant regex is valid")
}

pub fn analyze_reasoning_template(template: &str) -> Value {
    let mut result = json!({
        "reasoning_capability":"unknown","reasoning_efforts":[],
        "reasoning_default_effort":"","reasoning_budget_supported":false,
        "reasoning_toggle_keys":[]
    });
    if template.trim().is_empty() {
        return result;
    }
    let lower = template.to_lowercase();
    let mut toggle = Vec::<String>::new();
    if re(r"\benable_thinking\b").is_match(template) {
        toggle.push("enable_thinking".into());
    }
    if re(r"\badd_nothink_token\b").is_match(template) {
        toggle.push("add_nothink_token".into());
    }
    if re(r"\bthinking_mode\b").is_match(template)
        && re(r#"(?i)['"](?:enabled|disabled|adaptive)['"]"#).is_match(template)
    {
        toggle.push("thinking_mode".into());
    }
    let expression_pattern = re(r"(?s)\{[%{]-?(.*?)-?[%}]\}");
    let expressions = expression_pattern
        .captures_iter(template)
        .filter_map(|c| c.get(1).map(|m| m.as_str()))
        .collect::<Vec<_>>()
        .join("\n");
    // String literals are NOT template inputs. This prevents falsely
    // detecting enable_thinking from prose in a Jinja expression.
    let expressions =
        re(r#"(?s)'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*""#).replace_all(&expressions, "");
    if re(r"(?i)(?:if|set|default|defined|not)\s+[^\n{}]{0,80}\bthinking\b|\bthinking\s+is\s+(?:not\s+)?defined")
        .is_match(&expressions) {
        toggle.push("thinking".into());
    }
    let effort_present = re(r"\b(?:reasoning_effort|reasoning_strength)\b").is_match(template);
    let markers = [
        "<think>",
        "</think>",
        "<mm:think>",
        "</mm:think>",
        "<|think|>",
        "reasoning_content",
        "thinking_start_token",
        "thinking_end_token",
        "think_begin_token",
        "think_end_token",
    ];
    let has_markers = markers.iter().any(|m| lower.contains(m));
    let reject_disable = re(r"(?i)(disabl(?:e|ing)\s+thinking\s+is\s+not\s+supported|enable_thinking[^\n]{0,120}false[^\n]{0,120}raise_exception)")
        .is_match(template);
    let can_disable = !toggle.is_empty() && !reject_disable;

    let allowed = ["minimal", "low", "medium", "high", "xhigh", "max"];
    let mut efforts = Vec::<String>::new();
    if effort_present {
        for name in allowed {
            if re(&format!(r#"(?i)['"]{name}['"]"#)).is_match(template) {
                efforts.push(name.to_owned());
            }
        }
    }
    let default_patterns = [
        r#"(?i)(?:reasoning_effort|reasoning_strength)\s*\|\s*default\(\s*['"](minimal|low|medium|high|xhigh|max)['"]"#,
        r#"(?i)default\(\s*['"](minimal|low|medium|high|xhigh|max)['"]\s*\)[^\n]{0,80}(?:reasoning_effort|reasoning_strength)"#,
        r#"(?i)(?:reasoning_effort|reasoning_strength)[^\n]{0,80}\belse\s+['"](minimal|low|medium|high|xhigh|max)['"]"#,
    ];
    let mut default = String::new();
    for pattern in default_patterns {
        if let Some(c) = re(pattern).captures(template) {
            default = c[1].to_lowercase();
            break;
        }
    }
    if !default.is_empty() && !efforts.contains(&default) {
        efforts.push(default.clone());
    }
    efforts.sort_by_key(|name| allowed.iter().position(|v| v == name).unwrap_or(100));
    result["reasoning_capability"] = json!(if can_disable {
        "toggle"
    } else if has_markers || effort_present {
        "always"
    } else {
        "none"
    });
    result["reasoning_efforts"] = json!(efforts);
    result["reasoning_default_effort"] = json!(default);
    result["reasoning_budget_supported"] = json!(has_markers);
    result["reasoning_toggle_keys"] = json!(if can_disable { toggle } else { vec![] });
    result
}

fn exact<R: Read>(r: &mut R, count: usize) -> Result<Vec<u8>, String> {
    let mut out = vec![0u8; count];
    r.read_exact(&mut out)
        .map_err(|_| "GGUF 檔案標頭不完整。".to_owned())?;
    Ok(out)
}
fn u32le<R: Read>(r: &mut R) -> Result<u32, String> {
    Ok(u32::from_le_bytes(exact(r, 4)?.try_into().unwrap()))
}
fn u64le<R: Read>(r: &mut R) -> Result<u64, String> {
    Ok(u64::from_le_bytes(exact(r, 8)?.try_into().unwrap()))
}
fn gguf_string<R: Read>(r: &mut R, maximum: u64) -> Result<String, String> {
    let size = u64le(r)?;
    if size > maximum {
        return Err("GGUF 字串長度異常。".into());
    }
    let bytes = exact(r, size as usize)?;
    Ok(String::from_utf8_lossy(&bytes).to_string())
}
fn skip_string<R: Read + Seek>(r: &mut R) -> Result<(), String> {
    let size = u64le(r)?;
    if size > 1024 * 1024 * 1024 {
        return Err("GGUF 字串長度異常。".into());
    }
    r.seek(SeekFrom::Current(size as i64))
        .map_err(|e| e.to_string())?;
    Ok(())
}
fn fixed_size(kind: u32) -> Option<usize> {
    match kind {
        0 | 1 | 7 => Some(1),
        2 | 3 => Some(2),
        4..=6 => Some(4),
        10..=12 => Some(8),
        _ => None,
    }
}
fn skip_value<R: Read + Seek>(r: &mut R, kind: u32) -> Result<(), String> {
    if let Some(size) = fixed_size(kind) {
        r.seek(SeekFrom::Current(size as i64))
            .map_err(|e| e.to_string())?;
        return Ok(());
    }
    if kind == 8 {
        return skip_string(r);
    }
    if kind == 9 {
        let element_type = u32le(r)?;
        let count = u64le(r)?;
        if count > 1024 * 1024 * 1024 {
            return Err("GGUF 陣列元素數量異常。".into());
        }
        if let Some(size) = fixed_size(element_type) {
            let bytes = (size as u64)
                .checked_mul(count)
                .ok_or("GGUF 陣列長度溢位。")?;
            r.seek(SeekFrom::Current(bytes as i64))
                .map_err(|e| e.to_string())?;
        } else if element_type == 8 {
            for _ in 0..count {
                skip_string(r)?;
            }
        } else {
            return Err("GGUF 含有不支援的巢狀 metadata 類型。".into());
        }
        return Ok(());
    }
    Err("GGUF metadata 類型無法辨識。".into())
}
fn scalar<R: Read>(r: &mut R, kind: u32) -> Result<Value, String> {
    if kind == 8 {
        return Ok(json!(gguf_string(r, 1024 * 1024 * 1024)?));
    }
    if fixed_size(kind).is_none() {
        return Err("GGUF metadata 不是可讀取的純量。".into());
    }
    let v = match kind {
        0 => json!(exact(r, 1)?[0]),
        1 => json!(exact(r, 1)?[0] as i8),
        2 => json!(u16::from_le_bytes(exact(r, 2)?.try_into().unwrap())),
        3 => json!(i16::from_le_bytes(exact(r, 2)?.try_into().unwrap())),
        4 => json!(u32le(r)?),
        5 => json!(i32::from_le_bytes(exact(r, 4)?.try_into().unwrap())),
        6 => json!(f32::from_le_bytes(exact(r, 4)?.try_into().unwrap())),
        7 => json!(exact(r, 1)?[0] != 0),
        10 => json!(u64le(r)?),
        11 => json!(i64::from_le_bytes(exact(r, 8)?.try_into().unwrap())),
        12 => json!(f64::from_le_bytes(exact(r, 8)?.try_into().unwrap())),
        _ => unreachable!(),
    };
    Ok(v)
}
fn integer(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_u64().map(|x| x as i64))
        .or_else(|| v.as_f64().map(|x| x as i64))
        .unwrap_or(0)
}

fn inspect_one(path: &Path) -> Result<Value, String> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    if exact(&mut file, 4)?.as_slice() != b"GGUF" {
        return Err("invalid GGUF magic".into());
    }
    let version = u32le(&mut file)?;
    if version != 2 && version != 3 {
        return Err("unsupported GGUF version".into());
    }
    let tensor_count = u64le(&mut file)?;
    let kv_count = u64le(&mut file)?;
    if tensor_count > 10_000_000 || kv_count > 10_000_000 {
        return Err("unreasonable GGUF metadata".into());
    }
    let mut architecture = String::new();
    let mut native_context = 0;
    let mut nextn_layers = 0;
    let mut template = String::new();
    let mut named = Vec::<String>::new();
    for _ in 0..kv_count {
        let key = gguf_string(&mut file, 65535)?;
        let kind = u32le(&mut file)?;
        if key == "general.architecture" {
            let value = scalar(&mut file, kind)?;
            architecture = value
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| value.to_string());
        } else if key.ends_with(".nextn_predict_layers") && fixed_size(kind).is_some() {
            nextn_layers = nextn_layers.max(integer(&scalar(&mut file, kind)?));
        } else if key.ends_with(".context_length") && fixed_size(kind).is_some() {
            let context = integer(&scalar(&mut file, kind)?);
            if (512..=2097152).contains(&context) {
                native_context = native_context.max(context);
            }
        } else if key == "tokenizer.chat_template" && kind == 8 {
            template = scalar(&mut file, kind)?.as_str().unwrap_or("").to_owned();
        } else if key.starts_with("tokenizer.chat_template.") && kind == 8 {
            named.push(scalar(&mut file, kind)?.as_str().unwrap_or("").to_owned());
        } else {
            skip_value(&mut file, kind)?;
        }
    }
    let mut nextn_tensors = 0;
    for _ in 0..tensor_count {
        let name = gguf_string(&mut file, 65535)?;
        let dimensions = u32le(&mut file)?;
        if dimensions > 16 {
            return Err("GGUF tensor 維度數異常。".into());
        }
        file.seek(SeekFrom::Current(8 * dimensions as i64 + 4 + 8))
            .map_err(|e| e.to_string())?;
        if name.contains(".nextn.") || name.starts_with("nextn.") {
            nextn_tensors += 1;
        }
    }
    if template.is_empty() && named.len() == 1 {
        template = named.remove(0);
    }
    let mut result = json!({
        "inspection_ok":true,
        "mtp_capability":"unknown","mtp_layers":nextn_layers,
        "gguf_architecture":architecture,"native_context":native_context,
        "mtp_tensor_count":nextn_tensors,"reasoning_detection":if template.is_empty(){"pending"}else{"gguf"}
    });
    if let Value::Object(caps) = analyze_reasoning_template(&template) {
        for (key, val) in caps {
            result[key] = val;
        }
    }
    let cap = if nextn_layers > 0 && nextn_tensors > 0 {
        "available"
    } else if nextn_layers > 0 {
        "incomplete"
    } else if nextn_tensors > 0 {
        "unknown"
    } else {
        "unavailable"
    };
    result["mtp_capability"] = json!(cap);
    Ok(result)
}
fn default_result() -> Value {
    json!({"inspection_ok":false,"mtp_capability":"unknown","mtp_layers":0,
        "gguf_architecture":"","native_context":0,"mtp_tensor_count":0,
        "reasoning_capability":"unknown","reasoning_efforts":[],
        "reasoning_default_effort":"","reasoning_budget_supported":false,
        "reasoning_toggle_keys":[],"reasoning_detection":"pending"})
}

/// Model headers and split metadata only. Does not touch tensor weight contents.
pub fn inspect_gguf_capabilities(path: &Path) -> Value {
    inspect(path, true)
}
fn inspect(path: &Path, include_shards: bool) -> Value {
    let mut result = if path.is_file() {
        inspect_one(path).unwrap_or_else(|_| default_result())
    } else {
        default_result()
    };
    if include_shards {
        let filename = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let pattern = re(r"(?i)-(\d{5})-of-(\d{5})\.gguf$");
        if let Some(c) = pattern.captures(filename) {
            let first = c[1].parse::<u32>().unwrap_or(0);
            let total = c[2].parse::<u32>().unwrap_or(0);
            if first == 1 {
                let base = &filename[..c.get(0).unwrap().start()];
                let extension = path.extension().and_then(|s| s.to_str()).unwrap_or("gguf");
                for index in 2..=total {
                    let shard =
                        path.with_file_name(format!("{base}-{index:05}-of-{total:05}.{extension}"));
                    if !shard.is_file() {
                        continue;
                    }
                    let other = inspect(&shard, false);
                    result["mtp_layers"] =
                        json!(integer(&result["mtp_layers"]).max(integer(&other["mtp_layers"])));
                    result["mtp_tensor_count"] = json!(
                        integer(&result["mtp_tensor_count"]) + integer(&other["mtp_tensor_count"])
                    );
                    if result["gguf_architecture"] == "" && other["gguf_architecture"] != "" {
                        result["gguf_architecture"] = other["gguf_architecture"].clone();
                    }
                    if integer(&result["native_context"]) <= 0
                        && integer(&other["native_context"]) > 0
                    {
                        result["native_context"] = other["native_context"].clone();
                    }
                }
                let layers = integer(&result["mtp_layers"]);
                let tensors = integer(&result["mtp_tensor_count"]);
                result["mtp_capability"] = json!(if layers > 0 && tensors > 0 {
                    "available"
                } else if layers > 0 {
                    "incomplete"
                } else if tensors > 0 {
                    "unknown"
                } else {
                    "unavailable"
                });
            }
        }
    }
    result
}

/// Merge detected fields with the prior model without erasing established
/// runtime reasoning after transient file inspection errors.
pub fn apply_detected_model_capabilities(
    model: &mut Value,
    detected: &Value,
    reset_for_path_change: bool,
) {
    if !detected["inspection_ok"].as_bool().unwrap_or(false) && !reset_for_path_change {
        return;
    }
    model["mtp_capability"] = detected
        .get("mtp_capability")
        .cloned()
        .unwrap_or(json!("unknown"));
    model["mtp_layers"] = json!(integer(&detected["mtp_layers"]));
    let native = integer(&detected["native_context"]);
    model["native_context"] = json!(native);
    if native > 0 && integer(&model["context"]) > native {
        model["context"] = json!(native);
    }
    if reset_for_path_change || detected["reasoning_detection"] == "gguf" {
        for (key, default) in [
            ("reasoning_capability", json!("unknown")),
            ("reasoning_efforts", json!([])),
            ("reasoning_default_effort", json!("")),
            ("reasoning_budget_supported", json!(false)),
            ("reasoning_toggle_keys", json!([])),
            ("reasoning_detection", json!("pending")),
        ] {
            model[key] = detected.get(key).cloned().unwrap_or(default);
        }
        model["reasoning_supported"] = json!(
            model["reasoning_capability"] == "toggle" || model["reasoning_capability"] == "always"
        );
    }
    if model["mtp_capability"] != "available"
        && model["mtp_source"].as_str().unwrap_or("native") == "native"
    {
        model["mtp"] = json!(false);
        model["mtp_draft_max"] = Value::Null;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_and_nonexistent_file_capabilities_are_unknown() {
        let root = tempfile::tempdir().unwrap();
        assert_eq!(
            inspect_gguf_capabilities(&root.path().join("missing.gguf"))["inspection_ok"],
            false
        );
        let path = root.path().join("bad.gguf");
        std::fs::write(&path, b"not-a-gguf").unwrap();
        assert_eq!(inspect_gguf_capabilities(&path)["inspection_ok"], false);
    }

    #[test]
    fn qwen_template_recognizes_native_effort_and_toggle() {
        let s="{% if enable_thinking is undefined %}{% set reasoning_effort = reasoning_effort|default('xhigh') %}{% endif %}{{ '<think>' }}";
        let cap = analyze_reasoning_template(s);
        assert_eq!(cap["reasoning_capability"], "toggle");
        assert_eq!(cap["reasoning_default_effort"], "xhigh");
        assert_eq!(cap["reasoning_budget_supported"], true);
    }

    #[test]
    fn unknown_read_error_preserves_reasoning_without_path_change() {
        let mut model = json!({"context":4096,"mtp_source":"external","mtp":false,
            "reasoning_capability":"toggle","reasoning_detection":"runtime"});
        apply_detected_model_capabilities(&mut model, &default_result(), false);
        assert_eq!(model["reasoning_capability"], "toggle");
    }
}
