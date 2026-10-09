//! Temporary, explicit migration-only HTTP host for v1.1.0.
//!
//! This executable IS NOT AMIEBL Core production replacement. It intentionally
//! exposes only the already-ported READ-ONLY endpoints while engine lifecycle,
//! SSE, queue, cancellation and mutation parity are pending. It MUST NEVER be
//! included in a public installer or silently used by the WinUI GUI.

use amiebl_core::storage::ConfigStore;
use axum::{
    extract::{Request,State},
    http::{HeaderValue,StatusCode},
    middleware::{self,Next},
    response::{IntoResponse,Response},
    routing::get,
    Json,Router
};
use serde_json::{json,Value};
use std::{env,net::{IpAddr,Ipv4Addr,SocketAddr},path::PathBuf,sync::Arc};
use subtle::ConstantTimeEq;

struct Preview {
    config:ConfigStore,
    port:u16
}

async fn safety_gate(State(state):State<Arc<Preview>>,request:Request,next:Next)->Response {
    let origin = request.headers().get("origin").and_then(|s|s.to_str().ok());
    let origin_ok=origin.is_none_or(|value| {
        value==format!("http://127.0.0.1:{}",state.port)
            ||value==format!("http://localhost:{}",state.port)
    });
    if !origin_ok {
        return (StatusCode::FORBIDDEN,Json(json!({"error":"不允許此網站存取本地管理器。"}))).into_response();
    }
    if request.uri().path().starts_with("/manager/") {
        let presented=request.headers().get("X-Manager-Token")
            .and_then(|v|v.to_str().ok()).unwrap_or("");
        let valid=bool::from(state.config.admin_token().as_bytes().ct_eq(presented.as_bytes()));
        if !valid {
            return (StatusCode::UNAUTHORIZED,Json(json!({"error":"需要有效的管理權杖。"}))).into_response();
        }
    }
    let mut response=next.run(request).await;
    response.headers_mut().insert("X-AMIEBL-Migration-Preview",HeaderValue::from_static("true"));
    response
}
async fn health()->Json<Value>{
    Json(json!({"ok":true,"app":"local-model-manager","version":"1.1.0-preview"}))
}
async fn config(State(state):State<Arc<Preview>>)->Json<Value>{
    Json(state.config.config().clone())
}
async fn models(State(state):State<Arc<Preview>>)->Json<Value>{
    let mut rows=Vec::new();
    let c=state.config.config();
    for model in c["models"].as_array().into_iter().flatten() {
        let id=model["id"].as_str().unwrap_or("");
        let mut identities=vec![id.to_owned()];
        for profile in c["profiles"].as_array().into_iter().flatten() {
            identities.push(format!("{}::{}",id,profile["id"].as_str().unwrap_or("")));
        }
        for ident in identities {
            rows.push(json!({"id":ident,"object":"model","created":0,"owned_by":"local-model-manager"}));
        }
    }
    Json(json!({"object":"list","data":rows}))
}
fn usage()->String{
    "Usage: amiebl-core-preview --data-dir <isolated-new-folder> [--port 18080]\n\
     This intentionally incomplete server is for internal migration tests ONLY.\n\
     It refuses existing config.json folders and production port 8080.".into()
}
fn parse_args()->Result<(PathBuf,u16),String>{
    let mut args=env::args().skip(1);
    let mut data=None;
    let mut port=18080u16;
    while let Some(arg)=args.next() {
        match arg.as_str() {
            "--data-dir" => data=Some(PathBuf::from(args.next().ok_or_else(usage)?)),
            "--port" => port=args.next().ok_or_else(usage)?
                .parse().map_err(|_|usage())?,
            _=>return Err(usage())
        }
    }
    let data_dir=data.ok_or_else(usage)?;
    if data_dir.join("config.json").exists() {
        return Err("拒絕開啟既有設定目錄；請用全新的隔離測試資料夾。".into());
    }
    if port==8080||port<1024 {return Err("測試服務不得使用正式 8080 或系統保留連接埠。".into());}
    Ok((data_dir,port))
}
#[tokio::main]
async fn main()->Result<(),Box<dyn std::error::Error>>{
    let (data_dir,port)=parse_args()?;
    let config=ConfigStore::open(&data_dir)?;
    let state=Arc::new(Preview{config,port});
    let app=Router::new()
        .route("/health",get(health))
        .route("/manager/config",get(config))
        .route("/manager/export",get(config))
        .route("/v1/models",get(models))
        .layer(middleware::from_fn_with_state(state.clone(),safety_gate))
        .with_state(state);
    let address=SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST),port);
    let listener=tokio::net::TcpListener::bind(address).await?;
    eprintln!("AMIEBL MIGRATION-ONLY preview listening on http://127.0.0.1:{port}; not for inference.");
    axum::serve(listener,app)
        .with_graceful_shutdown(async {let _=tokio::signal::ctrl_c().await;})
        .await?;
    Ok(())
}
