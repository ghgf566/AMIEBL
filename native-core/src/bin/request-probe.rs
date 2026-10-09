//! Isolated differential-test adapter; never packaged as a production Core.
use amiebl_core::request;
use serde_json::{json, Value};
use std::io::{self, Read};

fn run(input: Value) -> Result<Value, String> {
    if input["op"] == "helpers" {
        return Ok(json!({
            "effort": request::map_native_reasoning_effort(input["level"].as_str().unwrap_or(""), &input["supported"]),
            "budget": request::auto_reasoning_budget(input["level"].as_str().unwrap_or(""), input["max_budget"].as_i64().unwrap_or(0)),
        }));
    }
    request::validate_request(&input["body"])?;
    let (model, profile) = request::resolve(&input["config"], &input["body"], &input["headers"])?;
    let mut record = input
        .get("record")
        .cloned()
        .unwrap_or(json!({"phase":"queued"}));
    let body = request::policy(&input["body"], &model, &profile, &mut record)?;
    Ok(json!({"body":body,"model":model,"profile":profile,"record":record}))
}

fn main() {
    let mut input = String::new();
    let result = io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| e.to_string())
        .and_then(|_| serde_json::from_str(&input).map_err(|e| e.to_string()))
        .and_then(run);
    println!(
        "{}",
        match result {
            Ok(result) => json!({"ok":true,"result":result}),
            Err(error) => json!({"ok":false,"error":error}),
        }
    );
}
