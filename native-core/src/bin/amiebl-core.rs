//! Actual inference runtime; explicit development opt-in until all parity gates pass.
use amiebl_core::runtime::{router, Manager};
use std::{env, path::PathBuf};
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut data = None;
    let mut port = None;
    let mut engine = None;
    let mut experimental = false;
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--data-dir" => data = args.next().map(PathBuf::from),
            "--port" => port = Some(args.next().ok_or("missing port")?.parse()?),
            "--engine-port" => engine = Some(args.next().ok_or("missing engine port")?.parse()?),
            "--experimental-runtime" => experimental = true,
            _ => return Err(format!("unknown argument: {arg}").into()),
        }
    }
    if !experimental {
        return Err("Native migration runtime is incomplete. Explicit --experimental-runtime and isolated --data-dir are required; never deploy this build.".into());
    }
    let data = data.ok_or("An explicit isolated --data-dir is required")?;
    let manager = Manager::open(data, port, engine).await?;
    let listener =
        tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, manager.port)).await?;
    let stopping = manager.stopping.clone();
    let ctrl = manager.clone();
    axum::serve(listener,router(manager)).with_graceful_shutdown(async move{tokio::select!{_ = stopping.cancelled()=>{},_ = tokio::signal::ctrl_c()=>{ctrl.stop().await;}}}).await?;
    Ok(())
}
