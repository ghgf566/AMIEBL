//! Isolated GGUF capability probe for Rust/Python parity tests.
use amiebl_core::gguf;
use serde_json::{json,Value};
use std::io::{self,Read};
use std::path::Path;

fn main() {
    let mut input=String::new();
    if let Err(e)=io::stdin().read_to_string(&mut input) {
        println!("{}",json!({"ok":false,"error":e.to_string()}));
        return;
    }
    let parsed=serde_json::from_str::<Value>(&input);
    let result=parsed.map_err(|e|e.to_string()).and_then(|request|{
        match request["op"].as_str().unwrap_or("") {
            "inspect"=>Ok(gguf::inspect_gguf_capabilities(Path::new(request["path"].as_str().unwrap_or("")))),
            "reasoning"=>Ok(gguf::analyze_reasoning_template(request["template"].as_str().unwrap_or(""))),
            "merge"=>{
                let mut model=request["model"].clone();
                gguf::apply_detected_model_capabilities(&mut model,&request["detected"],
                    request["reset_for_path_change"].as_bool().unwrap_or(false));
                Ok(model)
            }
            other=>Err(format!("unsupported op: {other}"))
        }
    });
    println!("{}",match result {Ok(value)=>json!({"ok":true,"result":value}),
        Err(error)=>json!({"ok":false,"error":error})});
}
