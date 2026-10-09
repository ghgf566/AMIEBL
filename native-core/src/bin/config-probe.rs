//! Temporary differential-test probe for the schema-v1 Rust migration.
//! This is a test utility, not the final AMIEBL service/API process.
//! Reads one JSON config from stdin, emits one structured JSON result.

use std::io::{self, Read};

use amiebl_core::config::{normalize_startup_config, validate_config};
use serde_json::{json, Value};

fn main() {
    let mut input = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut input) {
        println!("{}", json!({"ok":false,"error":e.to_string()}));
        std::process::exit(1);
    }
    let input = match serde_json::from_str::<Value>(&input) {
        Ok(value) => value,
        Err(e) => {
            println!("{}", json!({"ok":false,"error":e.to_string()}));
            return;
        }
    };
    let startup = std::env::args().any(|arg| arg == "--startup");
    let input = if startup { normalize_startup_config(input) } else { input };
    match validate_config(input) {
        Ok(value) => println!("{}", json!({"ok":true,"result":value})),
        Err(error) => println!("{}", json!({"ok":false,"error":error})),
    }
}
