//! Request validation, profile resolution and pre-inference policy from v1.0.0.
//!
//! This is shared logic for the future inference worker, not an HTTP gateway.
//! Unknown OpenAI fields and multimodal/tool payloads stay intact. In particular,
//! a client reasoning override wins over an agent profile, while Model Library
//! sampling overrides win over client sampling defaults, exactly as in v1.0.0.

use regex::Regex;
use serde_json::{json, Value};
use std::sync::LazyLock;
use std::time::Instant;

static PROFILE_MARKER: LazyLock<Regex> = LazyLock::new(|| {
    // Python re.I includes Turkish I, long S and Kelvin sign in ASCII ranges;
    // Python \s also includes the four information separator controls.
    Regex::new(r"(?i:AM[Iİı]EBL_PROF[Iİı]LE)[\s\x1c-\x1f]*:[\s\x1c-\x1f]*([A-Za-z0-9_İıſK-]+)")
        .unwrap()
});
static CLASSIFIER_WHITESPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\\s+").unwrap());
static COMPLEX_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
    r"(?i)\\b(debug|bug|error|exception|traceback|crash|race condition|deadlock|root cause|architecture|design|refactor|optimi[sz]e|algorithm|complexity|plan|strategy|compare|analy[sz]e|reason|why|implement|code|program|database|sql|api|network|performance|security|calculate|derive|proof|diagnose|troubleshoot)\\b",
    r"(除錯|偵錯|錯誤|異常|崩潰|閃退|競態|死鎖|根因|架構|設計|重構|最佳化|優化|演算法|複雜度|規劃|策略|比較|分析|推理|為什麼|原因|實作|程式碼|程式|資料庫|網路|效能|安全|計算|推導|證明|診斷|排查|怎麼修|修復)",
].iter().map(|p| Regex::new(p).unwrap()).collect()
});
static SIMPLE_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
    r"(?i)^(hi|hello|hey|yo|thanks?|thank you|你好|嗨|哈囉|哈啰|早安|午安|晚安|謝謝|感謝)[\\s!！?？,.，。～~]*$",
    r"(?i)\\b(translate|rewrite|proofread|paraphrase|shorten)\\b",
    r"(翻譯|翻成|改寫|潤飾|校對|縮短|換句話說|修正文法|整理格式)",
].iter().map(|p| Regex::new(p).unwrap()).collect()
});
static TASK_COMPLEX_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    COMPLEX_PATTERNS
        .iter()
        .map(|p| Regex::new(&p.as_str().replace(r"\\b", r"\b")).unwrap())
        .collect()
});
static TASK_SIMPLE_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    SIMPLE_PATTERNS
        .iter()
        .map(|p| Regex::new(&p.as_str().replace(r"\\b", r"\b").replace(r"\\s", r"\s")).unwrap())
        .collect()
});

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|n| n != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn python_whitespace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn integer(v: &Value, name: &str, minimum: i64, maximum: i64) -> Result<i64, String> {
    let n = v
        .as_f64()
        .ok_or_else(|| format!("{name} 必須是有效數字。"))?;
    if !n.is_finite() {
        return Err(format!("{name} 必須是有效數字。"));
    }
    if n < minimum as f64 || n > maximum as f64 || n.fract() != 0.0 {
        return Err(format!("{name} 必須介於 {minimum} 與 {maximum}。"));
    }
    Ok(n as i64)
}

/// Validation order is observable: return the same first error as submit().
pub fn validate_request(body: &Value) -> Result<(), String> {
    let messages = body
        .get("messages")
        .and_then(Value::as_array)
        .filter(|m| !m.is_empty())
        .ok_or("messages 必須是非空陣列。")?;
    if messages
        .iter()
        .any(|m| !m.is_object() || !m["role"].is_string())
    {
        return Err("訊息格式不正確。".into());
    }
    for key in ["max_tokens", "max_completion_tokens", "n_predict"] {
        if let Some(value) = body.get(key) {
            integer(value, key, 1, 1048576)?;
        }
    }
    if body.get("stream").is_some_and(|v| !v.is_boolean()) {
        return Err("stream 必須是布林值。".into());
    }
    // Python considers 1.0 and True equal to 1; keep that compatibility.
    if body
        .get("n")
        .is_some_and(|v| v.as_f64() != Some(1.0) && v != &json!(true))
    {
        return Err("目前單 slot 模式每次只支援一份生成結果（n=1）。".into());
    }
    if body
        .get("chat_template_kwargs")
        .is_some_and(|v| !v.is_object())
    {
        return Err("chat_template_kwargs 必須是物件。".into());
    }
    Ok(())
}

pub fn message_text(message: &Value) -> String {
    match &message["content"] {
        Value::String(s) => s.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|p| p.get("text")?.as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

/// Call after validate_request(), using lowercase HTTP header names.
pub fn resolve(config: &Value, body: &Value, headers: &Value) -> Result<(Value, Value), String> {
    let ident = body
        .get("model")
        .filter(|v| truthy(v))
        .unwrap_or(&config["default_model_id"]);
    let ident = ident.as_str().ok_or("model 必須是模型 ID。")?;
    let parts: Vec<_> = ident.split("::").collect();
    if parts.len() > 2 {
        return Err("無效的模型模式別名。".into());
    }
    let model = config["models"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|m| m["id"] == parts[0])
        .ok_or("找不到指定模型，請先加入模型庫。")?;
    let mut profile_id = if parts.len() == 2 {
        json!(parts[1])
    } else {
        headers["x-llm-profile"].clone()
    };
    if !truthy(&profile_id) {
        let text = body["messages"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|m| m["role"] == "system" || m["role"] == "developer")
            .map(message_text)
            .collect::<Vec<_>>()
            .join("\n");
        if let Some(c) = PROFILE_MARKER.captures(&text) {
            profile_id = json!(&c[1]);
        } else {
            let text = text.to_lowercase();
            profile_id =
                if text.contains("local deep coding") || text.contains("perform deep analysis") {
                    json!("deep-coding")
                } else if text.contains("answer the user's question directly and concisely") {
                    json!("quick-chat")
                } else if text.contains("work directly on the user's requested coding task") {
                    json!("coding")
                } else {
                    model
                        .get("default_profile_id")
                        .filter(|v| truthy(v))
                        .unwrap_or(&config["default_profile_id"])
                        .clone()
                };
        }
    }
    let profile = config["profiles"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|p| p["id"] == profile_id)
        .ok_or("指定的使用模式不存在。")?;
    Ok((model.clone(), profile.clone()))
}

pub fn map_native_reasoning_effort(level: &str, supported: &Value) -> Option<&'static str> {
    let target: usize = match level {
        "light" => 1,
        "deep" => 3,
        "extreme" => 5,
        _ => 2,
    };
    ["minimal", "low", "medium", "high", "xhigh", "max"]
        .into_iter()
        .enumerate()
        .filter(|(_, e)| supported.as_array().is_some_and(|a| a.contains(&json!(e))))
        .min_by_key(|(i, _)| (i.abs_diff(target), std::cmp::Reverse(*i)))
        .map(|(_, e)| e)
}

pub fn auto_reasoning_budget(level: &str, max_budget: i64) -> i64 {
    let ratio = match level {
        "light" => 0.15,
        "deep" => 0.50,
        "extreme" => 0.70,
        _ => 0.30,
    };
    // Python round() uses ties-to-even, unlike f64::round().
    ((max_budget as f64 * ratio).round_ties_even() as i64).clamp(0, max_budget)
}

fn classify_legacy(body: &Value, record: &mut Value) -> bool {
    let started = Instant::now();
    record["phase"] = json!("classifying");
    let user_text = body["messages"]
        .as_array()
        .into_iter()
        .flatten()
        .rev()
        .filter(|m| m["role"] == "user")
        .map(message_text)
        .find(|s| !s.trim_matches(python_whitespace).is_empty())
        .unwrap_or_default();
    // Preserve the literal doubled escapes in the frozen Python oracle. Changing
    // English classifier matching is a separate baseline behavior change.
    let normalized = CLASSIFIER_WHITESPACE
        .replace_all(user_text.trim_matches(python_whitespace), " ")
        .trim_matches(python_whitespace)
        .to_lowercase();
    let complex = COMPLEX_PATTERNS.iter().any(|p| p.is_match(&normalized));
    let simple = SIMPLE_PATTERNS.iter().any(|p| p.is_match(&normalized));
    record["decision"] = json!(if complex {
        "自動判斷：需要思考"
    } else if simple {
        "自動判斷：直接回答"
    } else {
        "自動判斷：採用模式預設"
    });
    record["classifier_seconds"] =
        json!((started.elapsed().as_secs_f64() * 10000.0).round_ties_even() / 10000.0);
    complex || !simple
}

/// Classify only the task text, never Copilot's surrounding instructions.
/// Unknown wrappers and tool continuations conservatively keep profile thinking.
fn classify(body: &Value, record: &mut Value) -> bool {
    let started = Instant::now();
    record["phase"] = json!("classifying");
    let messages = body["messages"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let latest = messages.iter().rev().find(|m| {
        m["role"] == "tool" || (m["role"] == "user" && !message_text(m).trim().is_empty())
    });
    let mut kind = "一般請求";
    let mut reason = "未命中明確規則，沿用模式的思考強度與預算";
    let mut enabled = true;
    if let Some(message) = latest {
        let text = message_text(message);
        if message["role"] == "tool" || message.get("tool_call_id").is_some() {
            kind = "工具結果續接";
            reason = "保留模式思考，避免把工具結果當成使用者問題";
        } else {
            // These names are emitted by Microsoft's AgentUserMessage renderer.
            // Reject ambiguous/repeated/incomplete delimiters rather than guess.
            let mut extracted = None;
            let mut ambiguous = false;
            for tag in ["userRequest", "user_query"] {
                let open = format!("<{tag}>");
                let close = format!("</{tag}>");
                let opens = text.matches(&open).count();
                let closes = text.matches(&close).count();
                if opens == 0 && closes == 0 {
                    continue;
                }
                if opens != 1 || closes != 1 || extracted.is_some() {
                    ambiguous = true;
                    break;
                }
                let start = text.find(&open).unwrap() + open.len();
                let end = text.find(&close).unwrap();
                if end < start {
                    ambiguous = true;
                    break;
                }
                extracted = Some(&text[start..end]);
            }
            let wrapped = text.contains("<context>")
                || text.contains("<reminderInstructions>")
                || text.contains("<conversation-summary>")
                || text.contains("<attachments>");
            // Microsoft's Copilot title prompts use this specific system/user
            // pairing; the quoted initial user task may contain technical keywords.
            // Do not suppress thinking for ordinary requests merely mentioning titles.
            let title_request = extracted.is_none()
                && !ambiguous
                && [
                    "Please write a brief title for the following request:",
                    "Please write a brief title for the following conversation:",
                    "Please write a brief title for the chat conversation above.",
                ]
                .iter()
                .any(|prefix| text.trim_start().starts_with(prefix))
                && messages.iter().any(|m| {
                    m["role"] == "system"
                        && message_text(m).trim_start().starts_with(
                            "You are an expert in crafting ultra-compact titles for chatbot conversations.",
                        )
                });
            let summary_request = extracted.is_none() && !ambiguous
                && text.trim_start().starts_with("Summarize the conversation history so far, paying special attention to the most recent agent commands and tool results that triggered this summarization.")
                && text.contains("Structure your summary using the enhanced format provided in the system message.")
                && messages.iter().any(|m| m["role"] == "system" && message_text(m).contains("<summary>"));
            if title_request {
                kind = "Copilot 對話標題";
                enabled = false;
                reason = "關閉思考：已辨識官方標題生成提示格式";
            } else if summary_request {
                kind = "Copilot 對話摘要";
                enabled = false;
                reason = "關閉思考：已辨識官方摘要提示格式";
            } else if ambiguous || (wrapped && extracted.is_none()) {
                kind = "未確認的包裝／輔助請求";
                reason = "無法可靠取出使用者問題，沿用模式";
            } else {
                if extracted.is_some() {
                    kind = "Copilot 使用者問題";
                }
                let task = extracted
                    .unwrap_or(&text)
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
                    .to_lowercase();
                // Correct doubled escapes from the frozen legacy classifier.
                let complex = TASK_COMPLEX_PATTERNS.iter().any(|p| p.is_match(&task));
                let simple = TASK_SIMPLE_PATTERNS.iter().any(|p| p.is_match(&task));
                let continuation = messages
                    .iter()
                    .rev()
                    .take_while(|m| !std::ptr::eq(*m, message))
                    .any(|m| {
                        m["role"] == "tool"
                            || m["tool_calls"]
                                .as_array()
                                .is_some_and(|calls| !calls.is_empty())
                    });
                if continuation {
                    kind = "工具結果續接";
                    reason = "保留模式思考以處理工具結果";
                } else if complex {
                    reason = "開啟思考：包含分析、技術或推導要求";
                } else if simple {
                    enabled = false;
                    reason = "關閉思考：辨識為問候或單純語言處理";
                }
            }
        }
    } else {
        kind = "未確認的請求";
        reason = "沒有可判斷的使用者文字，沿用模式";
    }
    record["request_kind"] = json!(kind);
    record["decision"] = json!(format!("自動判斷：{kind}；{reason}"));
    record["classifier_seconds"] =
        json!((started.elapsed().as_secs_f64() * 10000.0).round_ties_even() / 10000.0);
    enabled
}

/// Apply to validated requests and models/profiles from validate_config().
/// Record changes match policy()/classify(); timing is measured locally.
pub fn policy(
    input: &Value,
    model: &Value,
    p: &Value,
    record: &mut Value,
) -> Result<Value, String> {
    let mut body = input.clone();
    body["model"] = model["id"].clone();
    let mut cap = if model.get("output_percent").is_some() {
        model_output_limit(model)?
    } else {
        // Compatibility for callers supplying an unnormalized legacy model.
        integer(&p["max_tokens"], "總生成上限", 1, 1048576)?
            .min((integer(&model["context"], "上下文容量", 512, 2097152)? - 256).max(1))
    };
    for key in ["max_tokens", "max_completion_tokens", "n_predict"] {
        if let Some(v) = body.get(key) {
            cap = cap.min(integer(v, key, 1, 1048576)?);
        }
    }
    body["max_tokens"] = json!(cap);
    for key in ["max_completion_tokens", "n_predict"] {
        if body.get(key).is_some() {
            body[key] = json!(cap);
        }
    }
    record["max_tokens"] = json!(cap);
    for key in ["temperature", "top_p", "top_k", "min_p"] {
        if let Some(v) = model.get(key).filter(|v| !v.is_null()) {
            body[key] = v.clone();
        }
    }
    let mut kwargs = body
        .get("chat_template_kwargs")
        .cloned()
        .unwrap_or(json!({}));
    let reasoning = body
        .get("reasoning")
        .filter(|v| v.is_object())
        .cloned()
        .unwrap_or(json!({}));
    let explicit = [
        "reasoning_effort",
        "thinking_budget_tokens",
        "reasoning_budget_tokens",
    ]
    .iter()
    .any(|k| body.get(k).is_some())
        || reasoning.get("effort").is_some()
        || [
            "enable_thinking",
            "thinking",
            "thinking_mode",
            "add_nothink_token",
            "reasoning_effort",
            "reasoning_strength",
            "thinking_budget",
            "thinking_budget_tokens",
        ]
        .iter()
        .any(|k| kwargs.get(k).is_some());
    let max_budget = (cap - (cap / 4).clamp(1, 256)).max(0);
    if explicit {
        record["decision"] = json!("採用客戶端指定的思考設定");
        let effort = body
            .get("reasoning_effort")
            .unwrap_or(&reasoning["effort"])
            .clone();
        let budget = body
            .get("reasoning_budget_tokens")
            .or_else(|| body.get("thinking_budget_tokens"))
            .or_else(|| kwargs.get("thinking_budget_tokens"))
            .or_else(|| kwargs.get("thinking_budget"));
        let mut budget = budget.cloned().unwrap_or(Value::Null);
        if !budget.is_null() {
            let n = integer(&budget, "思考預算", -1, 1048576)?;
            budget = json!(if n == -1 {
                max_budget
            } else {
                n.min(max_budget)
            });
            body["thinking_budget_tokens"] = budget.clone();
            if body.get("reasoning_budget_tokens").is_some() {
                body["reasoning_budget_tokens"] = budget.clone();
            }
            for key in ["thinking_budget", "thinking_budget_tokens"] {
                if kwargs.get(key).is_some() {
                    kwargs[key] = budget.clone();
                }
            }
            if body.get("chat_template_kwargs").is_some() {
                body["chat_template_kwargs"] = kwargs;
            }
        }
        record["effort"] = effort;
        record["thinking_budget"] = budget;
        return Ok(body);
    }
    let capability = model["reasoning_capability"].as_str().unwrap_or("unknown");
    let mode = p["thinking_mode"].as_str().unwrap_or("model");
    let custom_budget = p["budget_mode"] == "custom";
    let level = p["reasoning_level"].as_str().unwrap_or("balanced");
    if mode == "model" {
        record["decision"] = json!("跟隨模型預設");
        return Ok(body);
    }
    if capability == "none" {
        record["decision"] = json!("此模型未提供可控制的思考能力");
        return Ok(body);
    }
    if capability == "unknown" {
        if custom_budget && mode != "off" {
            let budget = integer(&p["thinking_budget"], "思考預算", 0, 1048576)?.min(max_budget);
            body["thinking_budget_tokens"] = json!(budget);
            record["decision"] = json!("模型思考控制尚未確認；僅套用自訂思考 Token 上限");
            record["reasoning_level"] = json!(level);
            record["thinking_budget"] = json!(budget);
        } else {
            record["decision"] = json!("模型思考控制尚未確認，沿用模型預設");
        }
        return Ok(body);
    }
    let enabled = if mode == "auto" {
        if model.get("output_percent").is_some() {
            classify(&body, record)
        } else {
            classify_legacy(&body, record)
        }
    } else {
        record["decision"] = json!(if mode == "off" {
            "固定關閉思考"
        } else {
            "固定開啟思考"
        });
        mode != "off"
    };
    record["reasoning_level"] = json!(level);
    if capability == "always" && !enabled {
        record["decision"] = json!("模型不支援關閉思考，已跟隨模型預設");
        return Ok(body);
    }
    let effort = if enabled {
        map_native_reasoning_effort(level, &model["reasoning_efforts"])
    } else {
        None
    };
    if capability == "toggle" {
        for key in model["reasoning_toggle_keys"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            match key {
                "enable_thinking" | "thinking" => kwargs[key] = json!(enabled),
                "thinking_mode" => {
                    kwargs[key] = json!(if enabled { "enabled" } else { "disabled" })
                }
                "add_nothink_token" => kwargs[key] = json!(!enabled),
                _ => {}
            }
        }
        if truthy(&kwargs) {
            body["chat_template_kwargs"] = kwargs;
        }
    }
    let budget_supported = model["reasoning_budget_supported"] == true;
    if !enabled {
        if budget_supported {
            body["thinking_budget_tokens"] = json!(0);
        }
        record["effort"] = Value::Null;
        record["thinking_budget"] = json!(0);
        return Ok(body);
    }
    let budget = if custom_budget {
        Some(integer(&p["thinking_budget"], "思考預算", 0, 1048576)?.min(max_budget))
    } else if effort.is_none() && budget_supported {
        Some(auto_reasoning_budget(level, max_budget))
    } else {
        None
    };
    if let Some(effort) = effort {
        body["reasoning_effort"] = json!(effort);
    }
    if let Some(budget) = budget {
        body["thinking_budget_tokens"] = json!(budget);
    }
    record["effort"] = json!(effort);
    record["thinking_budget"] = json!(budget);
    Ok(body)
}

pub fn model_output_limit(model: &Value) -> Result<i64, String> {
    let context = integer(&model["context"], "上下文容量", 512, 2097152)?;
    let percent = integer(
        model.get("output_percent").unwrap_or(&json!(25)),
        "輸出預留比例",
        5,
        95,
    )?;
    Ok((context * percent / 100).max(1))
}

#[cfg(test)]
mod allocation_tests {
    use super::*;
    #[test]
    fn task_classifier_isolates_wrappers_and_preserves_uncertain_continuations() {
        let samples = [
            ("hello", false), ("translate this sentence", false),
            ("debug this error", true), ("分析後翻譯", true),
            ("<context>debug architecture</context><reminderInstructions>analyze code</reminderInstructions><userRequest>hello</userRequest>", false),
            ("<user_query>translate this</user_query>", false),
            ("<context>hello</context>", true),
            ("<userRequest>hello", true),
            ("<userRequest>hello</userRequest><userRequest>translate</userRequest>", true),
            ("please summarize this conversation", true),
        ];
        for (text, expected) in samples {
            let mut record = json!({});
            assert_eq!(
                classify(
                    &json!({"messages":[{"role":"user","content":text}]}),
                    &mut record
                ),
                expected,
                "{text}"
            );
            assert!(record["request_kind"].is_string());
        }
        let mut record = json!({});
        assert!(classify(
            &json!({"messages":[{"role":"user","content":"<userRequest>hello</userRequest>"},{"role":"assistant","tool_calls":[{"id":"t"}]},{"role":"tool","tool_call_id":"t","content":"translate this"}]}),
            &mut record
        ));
        assert_eq!(record["request_kind"], "工具結果續接");
        let summary = json!({"messages":[{"role":"system","content":"Return <summary> text"},{"role":"user","content":"Summarize the conversation history so far, paying special attention to the most recent agent commands and tool results that triggered this summarization. Structure your summary using the enhanced format provided in the system message."}]});
        assert!(!classify(&summary, &mut record));
        assert_eq!(record["request_kind"], "Copilot 對話摘要");
    }
    #[test]
    fn copilot_title_generation_uses_official_system_and_user_pair() {
        let system = "You are an expert in crafting ultra-compact titles for chatbot conversations. You are presented with a chat request, and you reply with only a brief title that captures the main topic of that request.";
        let prefixes = [
            "Please write a brief title for the following request:",
            "Please write a brief title for the following conversation:",
            "Please write a brief title for the chat conversation above.",
        ];
        for prefix in prefixes {
            let mut record = json!({});
            let body = json!({"messages":[
                {"role":"system","content":system},
                {"role":"user","content":format!("{prefix}\n\nAnalyze this Rust architecture and debug a race condition")},
            ]});
            assert!(!classify(&body, &mut record), "{prefix}");
            assert_eq!(record["request_kind"], "Copilot 對話標題");
            assert_eq!(
                record["decision"],
                "自動判斷：Copilot 對話標題；關閉思考：已辨識官方標題生成提示格式"
            );
        }
        // Requiring BOTH roles avoids disabling thinking when a user is
        // actually asking for technical help with a title-generation prompt.
        let user_only = json!({"messages":[
            {"role":"user","content":"Please write a brief title for the following request:\n\nAnalyze this Rust architecture"},
        ]});
        let mut record = json!({});
        assert!(classify(&user_only, &mut record));
        assert_ne!(record["request_kind"], "Copilot 對話標題");
        let system_only = json!({"messages":[
            {"role":"system","content":system},
            {"role":"user","content":"Debug a Rust race condition"},
        ]});
        assert!(classify(&system_only, &mut record));
        assert_ne!(record["request_kind"], "Copilot 對話標題");
        let wrapped = json!({"messages":[
            {"role":"system","content":system},
            {"role":"user","content":"<userRequest>Please write a brief title for the following request: debug Rust</userRequest>"},
        ]});
        assert!(classify(&wrapped, &mut record));
        assert_ne!(record["request_kind"], "Copilot 對話標題");

        let model = json!({
            "id":"m","context":8192,"output_percent":25,
            "reasoning_capability":"toggle","reasoning_budget_supported":true,
            "reasoning_toggle_keys":["enable_thinking"],
        });
        let mut profile = json!({
            "thinking_mode":"auto","reasoning_level":"balanced","budget_mode":"auto",
        });
        let title = json!({"model":"m","messages":[
            {"role":"system","content":system},
            {"role":"user","content":"Please write a brief title for the following request:\n\nDebug the architecture"},
        ]});
        let disabled = policy(&title, &model, &profile, &mut record).unwrap();
        assert_eq!(disabled["chat_template_kwargs"]["enable_thinking"], false);
        assert_eq!(record["request_kind"], "Copilot 對話標題");
        profile["thinking_mode"] = json!("on");
        let fixed = policy(&title, &model, &profile, &mut record).unwrap();
        assert_eq!(fixed["chat_template_kwargs"]["enable_thinking"], true);
        profile["thinking_mode"] = json!("auto");
        let mut explicit = title.clone();
        explicit["chat_template_kwargs"] = json!({"enable_thinking":true});
        let overridden = policy(&explicit, &model, &profile, &mut record).unwrap();
        assert_eq!(overridden["chat_template_kwargs"]["enable_thinking"], true);
        assert_eq!(record["decision"], "採用客戶端指定的思考設定");
    }
    #[test]
    fn allocation_scales_and_client_can_only_lower_the_limit() {
        let mut model = json!({"id":"m","context":4096,"output_percent":50});
        let profile = json!({"max_tokens":1,"thinking_mode":"off","budget_mode":"auto","reasoning_level":"light"});
        let mut record = json!({});
        let request = json!({"model":"m","messages":[]});
        assert_eq!(
            policy(&request, &model, &profile, &mut record).unwrap()["max_tokens"],
            2048
        );
        model["context"] = json!(65536);
        assert_eq!(model_output_limit(&model).unwrap(), 32768);
        let request =
            json!({"model":"m","messages":[],"max_tokens":1000,"max_completion_tokens":800});
        assert_eq!(
            policy(&request, &model, &profile, &mut record).unwrap()["max_tokens"],
            800
        );
        model["output_percent"] = json!(100);
        assert!(model_output_limit(&model).is_err());
    }
}
