//! Differential-test bridge for the v1.0.0 VS Code integration port.
//! Never shipped in AMIEBL production bundles.

use amiebl_core::vscode;
use serde_json::{json, Value};
use std::io::{self, Read};
use std::path::Path;

fn run(query: &Value) -> Result<Value, String> {
    let op = query["op"].as_str().ok_or("missing op")?;
    let config = &query["config"];
    match op {
        "jsonc" => vscode::read_jsonc(query["text"].as_str().unwrap_or("")),
        "model_entries" => Ok(vscode::model_entries(config)),
        "render_agent" => Ok(json!(vscode::render_agent(&query["profile"]))),
        "agent_name" => Ok(json!(vscode::agent_display_name(&query["profile"]))),
        "preserve" => Ok(json!(vscode::preserve_agent_profile_marker(
            query["text"].as_str().unwrap_or(""),
            query["profile_id"].as_str().unwrap_or(""),
            query["display_name"].as_str(),
        )?)),
        "frontmatter" => Ok(json!(vscode::update_frontmatter(
            query["text"].as_str().unwrap_or(""),
            query["model_name"].as_str()
        )?)),
        "preview" => Ok(vscode::preview_at(
            config,
            Path::new(query["models_file"].as_str().ok_or("missing models_file")?),
            Path::new(query["agents_dir"].as_str().ok_or("missing agents_dir")?),
        )),
        "apply" => vscode::apply_at(
            config,
            Path::new(query["data_dir"].as_str().ok_or("missing data_dir")?),
            Path::new(query["models_file"].as_str().ok_or("missing models_file")?),
            Path::new(query["agents_dir"].as_str().ok_or("missing agents_dir")?),
        ),
        _ => Err(format!("unsupported op {op}")),
    }
}

fn main() {
    let mut input = String::new();
    let result = match io::stdin().read_to_string(&mut input) {
        Ok(_) => match serde_json::from_str::<Value>(&input) {
            Ok(request) => run(&request),
            Err(e) => Err(e.to_string()),
        },
        Err(e) => Err(e.to_string()),
    };
    let output = match result {
        Ok(value) => json!({"ok":true,"result":value}),
        Err(error) => json!({"ok":false,"error":error}),
    };
    println!("{}", serde_json::to_string(&output).unwrap());
}
