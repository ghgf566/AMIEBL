//! AMIEBL native core, under migration from v1.0.0 Python reference.
//!
//! Not currently used by the GUI or release packaging. Never ship this crate
//! as a replacement until every gate in migration/v1.1.0/PARITY_CONTRACT.md
//! passes. The legacy application remains the working oracle.

pub mod config;

pub mod vscode;

pub mod storage;

pub mod gguf;

pub mod request;

pub mod runtime;
