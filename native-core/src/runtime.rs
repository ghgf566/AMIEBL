//! Real migration runtime. Never included in production packaging before parity gates.
//!
//! One worker owns inference ordering; cancellation completes upstream cleanup
//! before that worker advances. Lifecycle operations are serialized separately
//! from short state locks. Only Child handles created here can be terminated.
use crate::{
    gguf, request,
    storage::{self, ConfigStore},
    vscode,
};
use axum::{
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, Path as ApiPath, Request, State},
    http::{HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::{
    collections::{HashMap, VecDeque},
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex as RecordMutex},
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
    sync::{mpsc, oneshot, Mutex},
    time::{sleep, timeout},
};
use tokio_util::sync::CancellationToken;

mod watch;
const MAX_QUEUE: usize = 32;
const MAX_HISTORY: usize = 200;
const MAX_BODY: usize = 32 * 1024 * 1024;
type ApiResult = Result<Json<Value>, ApiError>;
#[derive(Debug)]
pub struct ApiError(u16, Value);
impl ApiError {
    fn invalid(message: impl Into<String>) -> Self {
        Self(
            400,
            json!({"error":{"message":message.into(),"type":"invalid_request_error"}}),
        )
    }
    fn detail(code: u16, message: &str) -> Self {
        Self(code, json!({"detail":message}))
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            StatusCode::from_u16(self.0).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
            Json(self.1),
        )
            .into_response()
    }
}
impl From<String> for ApiError {
    fn from(e: String) -> Self {
        Self::invalid(e)
    }
}
struct Reply {
    code: u16,
    body: Vec<u8>,
    stream: Option<mpsc::Receiver<Bytes>>,
}
pub struct Ticket {
    id: String,
    body: Value,
    model: Value,
    profile: Value,
    headers: Value,
    record: RecordMutex<Value>,
    started: Instant,
    cancel: CancellationToken,
    done: CancellationToken,
    response: RecordMutex<Option<oneshot::Sender<Reply>>>,
    upstream_sent: std::sync::atomic::AtomicBool,
    upstream_headers: std::sync::atomic::AtomicBool,
}
impl Ticket {
    fn set(&self, key: &str, value: Value) {
        self.record.lock().unwrap()[key] = value;
    }
    fn reply(&self, reply: Reply) {
        if let Some(sender) = self.response.lock().unwrap().take() {
            let _ = sender.send(reply);
        }
    }
}
struct Inner {
    managed_engine_dir: Option<PathBuf>,
    store: ConfigStore,
    state: &'static str,
    model: Option<Value>,
    loaded: Option<Value>,
    process: Option<Child>,
    engine_version: Value,
    last_error: Value,
    accepting: bool,
    deferred_unload: bool,
    manual: bool,
    load_cancel: Option<CancellationToken>,
    tickets: HashMap<String, Arc<Ticket>>,
    active: Option<String>,
    history: VecDeque<Value>,
    lines: VecDeque<String>,
    slots: Value,
    resources: Value,
    last_used: Instant,
}
pub struct Manager {
    engines: Arc<crate::engines::EngineManager>,
    background: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    inner: Mutex<Inner>,
    lifecycle: Mutex<()>,
    queue: mpsc::Sender<Arc<Ticket>>,
    http: reqwest::Client,
    pub stopping: CancellationToken,
    pub port: u16,
    engine_port: Option<u16>,
    started: Instant,
    data: PathBuf,
    token: String,
}
fn utc() -> String {
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}
fn round(n: f64, digits: i32) -> f64 {
    let s = 10f64.powi(digits);
    (n * s).round_ties_even() / s
}
fn clean(text: &str, token: &str) -> String {
    let text = text.replace(token, "[已隱藏]");
    let secrets =
        regex::Regex::new(r"(?i)(authorization|api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+")
            .unwrap();
    secrets
        .replace_all(&text, "$1=[已隱藏]")
        .chars()
        .filter(|c| !matches!(*c,'\x00'..='\x08'|'\x0b'..='\x1f'|'\x7f'))
        .take(800)
        .collect()
}
fn effective(model: &Value) -> Value {
    let mut result = json!({"path":model["path"],"context":model["context"],"cpu_threads":model.get("cpu_threads").unwrap_or(&json!(0)),"cache_type":model["cache_type"],"auto_fit":model["auto_fit"]==true,"mtp":model["mtp"]==true,"vision":model["vision"]==true});
    if model["auto_fit"] == true {
        result["fit_target_enabled"] = json!(model["fit_target_enabled"] == true);
        if model["fit_target_enabled"] == true {
            result["fit_target_mib"] = model["fit_target_mib"].clone();
        }
    } else {
        result["gpu_layers"] = model["gpu_layers"].clone();
    }
    if model["mtp"] == true {
        result["mtp_source"] = model.get("mtp_source").cloned().unwrap_or(json!("native"));
        result["mtp_draft_max"] = model["mtp_draft_max"].clone();
        if result["mtp_source"] == "external" {
            result["mtp_draft_path"] = model["mtp_draft_path"].clone();
        }
    }
    if model["vision"] == true {
        result["mmproj"] = model["mmproj"].clone();
    }
    result
}
fn engine_command(config: &Value) -> Result<Vec<String>, String> {
    if let Ok(raw) = std::env::var("LMM_ENGINE_COMMAND_JSON") {
        let value: Value = serde_json::from_str(&raw)
            .map_err(|_| "LMM_ENGINE_COMMAND_JSON 必須是命令參數陣列。")?;
        let values = value
            .as_array()
            .filter(|a| !a.is_empty())
            .ok_or("LMM_ENGINE_COMMAND_JSON 必須是命令參數陣列。")?;
        return values
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .ok_or("LMM_ENGINE_COMMAND_JSON 必須是命令參數陣列。".into())
            })
            .collect();
    }
    Ok(vec![Path::new(config["engine_dir"].as_str().unwrap_or(""))
        .join("llama-server.exe")
        .to_string_lossy()
        .into_owned()])
}
fn hidden_command(executable: &str) -> Command {
    let mut command = Command::new(executable);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    command.kill_on_drop(true);
    command
}
fn history_prune(inner: &mut Inner, data: &Path) -> Result<(), String> {
    let cutoff = Utc::now()
        - chrono::Duration::days(
            inner.store.config()["log_retention_days"]
                .as_i64()
                .unwrap_or(7),
        );
    inner.history.retain(|row| {
        row["started_at"]
            .as_str()
            .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
            .is_some_and(|t| t >= cutoff)
    });
    while inner.history.len() > MAX_HISTORY {
        inner.history.pop_front();
    }
    storage::atomic_json(&data.join("history.json"), &json!(inner.history))
}
fn prune_bodies(data: &Path, retention: f64) {
    let mut files = std::fs::read_dir(data.join("request-bodies"))
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
        .collect::<Vec<_>>();
    files.sort_by_key(|p| p.metadata().and_then(|m| m.modified()).ok());
    let count = files.len();
    for (i, path) in files.into_iter().enumerate() {
        let old = path
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.elapsed().ok())
            .is_some_and(|d| d.as_secs_f64() > retention * 86400.0);
        if i < count.saturating_sub(100) || old {
            let _ = std::fs::remove_file(path);
        }
    }
}
impl Manager {
    pub async fn open(
        data: PathBuf,
        port: Option<u16>,
        engine_port: Option<u16>,
    ) -> Result<Arc<Self>, String> {
        let mut store = ConfigStore::open(&data)?;
        let mut config = store.config().clone();
        for model in config["models"].as_array_mut().unwrap() {
            let path = Path::new(model["path"].as_str().unwrap_or(""));
            if path.is_file()
                && (model["mtp_capability"] == "unknown"
                    || model["native_context"].as_f64().unwrap_or(0.) <= 0.
                    || !matches!(
                        model["reasoning_detection"].as_str(),
                        Some("gguf" | "runtime")
                    ))
            {
                let caps = gguf::inspect_gguf_capabilities(path);
                gguf::apply_detected_model_capabilities(model, &caps, false);
            }
        }
        if &config != store.config() {
            store.persist_runtime(config)?;
        }
        let port = port.unwrap_or(store.config()["api_port"].as_u64().unwrap_or(8080) as u16);
        let token = store.admin_token().to_owned();
        let history = storage::read_json(&data.join("history.json"))
            .ok()
            .and_then(|v| v.as_array().cloned())
            .unwrap_or_default()
            .into_iter()
            .filter(|r| {
                matches!(
                    r["phase"].as_str(),
                    Some("completed" | "cancelled" | "error")
                )
            })
            .collect();
        let (queue, receiver) = mpsc::channel(MAX_QUEUE);
        let http = reqwest::Client::builder()
            .no_proxy()
            // Do not retain sockets to an engine whose lifetime is owned here.
            // Windows exclusive rebind can be blocked by an old pooled socket.
            .pool_max_idle_per_host(0)
            .connect_timeout(Duration::from_secs(5))
            .build()
            .map_err(|e| e.to_string())?;
        let engines = crate::engines::EngineManager::open(
            std::env::current_exe()
                .map_err(|e| e.to_string())?
                .parent()
                .ok_or("Missing application directory")?,
        )?;
        let managed_engine_dir = engines.active_directory().await?;
        let manager = Arc::new(Self {
            engines,
            background: Mutex::new(Vec::new()),
            inner: Mutex::new(Inner {
                managed_engine_dir,
                store,
                state: "unloaded",
                model: None,
                loaded: None,
                process: None,
                engine_version: Value::Null,
                last_error: Value::Null,
                accepting: true,
                deferred_unload: false,
                manual: false,
                load_cancel: None,
                tickets: HashMap::new(),
                active: None,
                history,
                lines: VecDeque::new(),
                slots: json!([]),
                resources: json!({"ram_used_gb":null,"ram_total_gb":null,"gpu_used_mib":null,"gpu_total_mib":null}),
                last_used: Instant::now(),
            }),
            lifecycle: Mutex::new(()),
            queue,
            http,
            stopping: CancellationToken::new(),
            port,
            engine_port,
            started: Instant::now(),
            data,
            token,
        });
        {
            let mut i = manager.inner.lock().await;
            history_prune(&mut i, &manager.data)?;
        }
        let worker = manager.clone();
        let worker_task = tokio::spawn(async move {
            worker.worker(receiver).await;
        });
        let monitor = manager.clone();
        let monitor_task = tokio::spawn(async move {
            monitor.monitor().await;
        });
        let watcher = manager.clone();
        let watcher_task = tokio::spawn(async move {
            watcher.watch_vscode().await;
        });
        manager
            .background
            .lock()
            .await
            .extend([worker_task, monitor_task, watcher_task]);
        manager.log("管理器已啟動，等待本地請求。").await;
        let updater = manager.clone();
        let update_task = tokio::spawn(async move {
            let _ = updater.engines.detect().await;
            let mut elapsed = 0u32;
            loop {
                if elapsed.is_multiple_of(360) {
                    if let Err(e) = updater.engines.automatic_check().await {
                        updater.log(&format!("引擎更新檢查：{e}")).await;
                    }
                }
                if let Some(id) = updater.engines.auto_candidate().await {
                    let _ = updater.activate_engine(&id).await;
                }
                tokio::select! { _ = updater.stopping.cancelled() => break, _ = sleep(Duration::from_secs(60)) => {} }
                elapsed = elapsed.wrapping_add(1);
            }
        });
        manager.background.lock().await.push(update_task);
        let config = manager.inner.lock().await.store.config().clone();
        if config["preload"] == true && !config["models"].as_array().unwrap().is_empty() {
            manager
                .begin_load(config["default_model_id"].as_str().unwrap_or(""))
                .await?;
        }
        Ok(manager)
    }
    async fn log(&self, message: &str) {
        let mut i = self.inner.lock().await;
        i.lines
            .push_back(format!("{}  {}", utc(), clean(message, &self.token)));
        while i.lines.len() > 300 {
            i.lines.pop_front();
        }
    }
    async fn activate_engine(&self, id: &str) -> Result<(), String> {
        let _lifecycle = self.lifecycle.lock().await;
        let directory = self.engines.directory(id).await?;
        let mut inner = self.inner.lock().await;
        if !inner.tickets.is_empty()
            || inner.process.is_some()
            || matches!(inner.state, "loading" | "unloading")
        {
            return Err("引擎仍在使用中；請先卸載模型再切換版本。".into());
        }
        // Same guarded state lock as admission prevents a new request during switching.
        self.engines.activate(id).await?;
        inner.managed_engine_dir = Some(directory);
        Ok(())
    }
    fn changed(&self, i: &Inner, model: &Value) -> bool {
        i.loaded.as_ref().is_some_and(|loaded| {
            loaded["engine_dir"]
                != i.managed_engine_dir
                    .as_ref()
                    .map(|p| json!(p))
                    .unwrap_or_else(|| i.store.config()["engine_dir"].clone())
                || loaded["engine_port"]
                    != json!(self
                        .engine_port
                        .unwrap_or(i.store.config()["engine_port"].as_u64().unwrap() as u16))
                || loaded["model"]["id"] != model["id"]
                || effective(&loaded["model"]) != effective(model)
        })
    }
    async fn finish(&self, ticket: &Arc<Ticket>, phase: &str, error: Option<&str>) {
        let mut i = self.inner.lock().await;
        if ticket.done.is_cancelled() {
            return;
        }
        {
            let mut r = ticket.record.lock().unwrap();
            r["phase"] = json!(phase);
            r["finished_at"] = json!(utc());
            r["elapsed_seconds"] = json!(round(ticket.started.elapsed().as_secs_f64(), 3));
            if let Some(e) = error {
                r["error"] = json!(clean(e, &self.token));
            }
        }
        ticket.reply(Reply {
            code: if phase == "cancelled" { 499 } else { 502 },
            body: serde_json::to_vec(
                &json!({"error":{"message":error.unwrap_or("請求已取消。"),"type":phase}}),
            )
            .unwrap(),
            stream: None,
        });
        ticket.done.cancel();
        i.history.push_back(ticket.record.lock().unwrap().clone());
        i.tickets.remove(&ticket.id);
        i.last_used = Instant::now();
        if let Err(e) = history_prune(&mut i, &self.data) {
            i.last_error = json!(clean(&e, &self.token));
        }
        drop(i);
        self.log(&format!("任務 {}：{phase}。", &ticket.id[..8]))
            .await;
    }
    async fn cancel(&self, ticket: Arc<Ticket>) {
        ticket.cancel.cancel();
        let queued = self.inner.lock().await.active.as_deref() != Some(&ticket.id);
        if queued {
            ticket.set("cancel_confirmed", json!(true));
            self.finish(&ticket, "cancelled", None).await;
        }
    }
    async fn submit(
        &self,
        body: Value,
        headers: Value,
    ) -> Result<(Arc<Ticket>, oneshot::Receiver<Reply>), ApiError> {
        let mut i = self.inner.lock().await;
        if !i.accepting || self.stopping.is_cancelled() {
            return Err(ApiError::detail(503, "管理器目前暫停接收新任務。"));
        }
        if i.tickets.len() >= MAX_QUEUE {
            return Err(ApiError::detail(429, "等待中的任務過多，請稍後再試。"));
        }
        request::validate_request(&body)?;
        let (model, profile) = request::resolve(i.store.config(), &body, &headers)?;
        let id = uuid::Uuid::new_v4().to_string();
        let record = json!({"id":id,"model_id":model["id"],"model_name":model["name"],"profile_id":profile["id"],"profile_name":profile["name"],"phase":"queued","started_at":utc(),"finished_at":null,"elapsed_seconds":0,"first_token_seconds":null,"classifier_seconds":null,"prompt_tokens":null,"cached_tokens":null,"generated_tokens":null,"thinking_tokens":null,"prompt_tps":null,"generation_tps":null,"prompt_progress":null,"reasoning_level":null,"effort":null,"thinking_budget":null,"max_tokens":null,"decision":null,"error":null,"cancel_confirmed":false});
        let (tx, rx) = oneshot::channel();
        let ticket = Arc::new(Ticket {
            id: id.clone(),
            body,
            model,
            profile,
            headers,
            record: RecordMutex::new(record),
            started: Instant::now(),
            cancel: CancellationToken::new(),
            done: CancellationToken::new(),
            response: RecordMutex::new(Some(tx)),
            upstream_sent: false.into(),
            upstream_headers: false.into(),
        });
        // Admission count includes the active ticket, matching the Python bound.
        self.queue
            .try_send(ticket.clone())
            .map_err(|_| ApiError::detail(429, "等待中的任務過多，請稍後再試。"))?;
        i.tickets.insert(id, ticket.clone());
        i.last_used = Instant::now();
        if i.store.config()["log_request_bodies"] == true
            && serde_json::to_vec(&ticket.body).unwrap().len() <= 1024 * 1024
        {
            storage::atomic_json(
                &self
                    .data
                    .join("request-bodies")
                    .join(format!("{}.json", ticket.id)),
                &ticket.body,
            )?;
            prune_bodies(
                &self.data,
                i.store.config()["log_retention_days"]
                    .as_f64()
                    .unwrap_or(7.),
            );
        }
        Ok((ticket, rx))
    }
    pub async fn begin_load(self: &Arc<Self>, ident: &str) -> Result<(), String> {
        let mut i = self.inner.lock().await;
        let model = i.store.config()["models"]
            .as_array()
            .unwrap()
            .iter()
            .find(|m| m["id"] == ident)
            .cloned()
            .ok_or("找不到指定模型，請先加入模型庫。")?;
        let same = i.state == "ready" && i.model.as_ref().is_some_and(|m| m["id"] == ident);
        let reload = !same || self.changed(&i, &model);
        if !i.tickets.is_empty() && reload {
            return Err("目前有執行中或排隊任務，請完成後再切換或重新載入模型。".into());
        }
        if i.state == "loading" || i.manual {
            if i.model
                .as_ref()
                .is_some_and(|m| m["id"] == ident && effective(m) == effective(&model))
            {
                return Ok(());
            }
            return Err(
                "模型正在以舊設定載入；請等這次載入完成後，再重新載入以套用新設定。".into(),
            );
        }
        if !reload {
            return Ok(());
        }
        i.manual = true;
        i.model = Some(model.clone());
        let cancel = CancellationToken::new();
        i.load_cancel = Some(cancel.clone());
        drop(i);
        let manager = self.clone();
        tokio::spawn(async move {
            tokio::select! {_ = cancel.cancelled()=>{manager.unload().await;},_ = manager.load(model)=>{}}
            let mut i = manager.inner.lock().await;
            i.manual = false;
            i.load_cancel = None;
        });
        Ok(())
    }
    async fn stop_child(&self) {
        let mut child = {
            let mut i = self.inner.lock().await;
            i.model = None;
            i.loaded = None;
            i.slots = json!([]);
            i.process.take()
        };
        if let Some(child) = child.as_mut() {
            let _ = child.start_kill();
            let _ = timeout(Duration::from_secs(5), child.wait()).await;
        }
    }
    pub async fn unload(&self) {
        let _guard = self.lifecycle.lock().await;
        {
            self.inner.lock().await.state = "unloading";
        }
        self.stop_child().await;
        {
            let mut i = self.inner.lock().await;
            i.state = "unloaded";
            i.deferred_unload = false;
        }
        self.log("模型已卸載，API 仍待命。").await;
    }
    async fn load(self: &Arc<Self>, mut model: Value) -> Result<(), String> {
        let _guard = self.lifecycle.lock().await;
        {
            let i = self.inner.lock().await;
            if i.state == "ready"
                && i.model.as_ref().is_some_and(|m| m["id"] == model["id"])
                && !self.changed(&i, &model)
            {
                return Ok(());
            }
        }
        let replacing_owned_port = {
            let i = self.inner.lock().await;
            let target = self
                .engine_port
                .unwrap_or(i.store.config()["engine_port"].as_u64().unwrap() as u16);
            i.process.is_some()
                && i.loaded
                    .as_ref()
                    .is_some_and(|loaded| loaded["engine_port"] == json!(target))
        };
        self.stop_child().await;
        {
            let mut i = self.inner.lock().await;
            i.state = "loading";
            i.last_error = Value::Null;
            i.model = Some(model.clone());
        }
        let result = self.load_inner(&mut model, replacing_owned_port).await;
        if let Err(e) = &result {
            self.stop_child().await;
            let mut i = self.inner.lock().await;
            i.state = "error";
            i.last_error = json!(clean(e, &self.token));
        }
        result
    }
    async fn load_inner(
        self: &Arc<Self>,
        model: &mut Value,
        replacing_owned_port: bool,
    ) -> Result<(), String> {
        let engine_status = self.engines.status().await;
        if engine_status["policy"]["mode"] == "managed" && engine_status["active"].is_null() {
            return Err(
                "尚未啟用管理的推理引擎，請在系統頁下載並啟用版本，或改回外部引擎。".into(),
            );
        }
        check_gguf(
            &model["path"],
            "模型檔案不存在，請在模型庫重新選擇位置。",
            "指定的主模型不是有效的 GGUF 檔案。",
            "無法讀取主模型 GGUF。",
        )?;
        if model["vision"] == true {
            check_gguf(
                &model["mmproj"],
                "視覺模型檔案不存在，請重新指定。",
                "指定的視覺模型不是有效的 GGUF 檔案。",
                "無法讀取視覺模型 GGUF。",
            )?;
        }
        if model["mtp"] == true {
            if model["mtp_source"] == "external" {
                check_gguf(
                    &model["mtp_draft_path"],
                    "找不到外部 MTP Draft GGUF，請重新指定檔案。",
                    "指定的外部 MTP Draft 不是有效的 GGUF 檔案。",
                    "無法讀取外部 MTP Draft GGUF。",
                )?;
            } else if gguf::inspect_gguf_capabilities(Path::new(model["path"].as_str().unwrap()))
                ["mtp_capability"]
                != "available"
            {
                return Err(
                    "此 GGUF 未偵測到可用的內建 MTP／NextN 權重，請改用外部 MTP Draft 或關閉 MTP。"
                        .into(),
                );
            }
        }
        let config = {
            let i = self.inner.lock().await;
            let mut config = i.store.config().clone();
            if let Some(path) = &i.managed_engine_dir {
                config["engine_dir"] = json!(path);
            }
            config
        };
        let port = self
            .engine_port
            .unwrap_or(config["engine_port"].as_u64().unwrap() as u16);
        let mut available = free_port(port);
        // wait() has completed before this path. Windows can still be settling
        // sockets from that owned child; allow a bounded exclusive-bind recheck
        // only for its original port. A timeout is never evidence of availability.
        if !available && replacing_owned_port {
            let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
            while !available && tokio::time::Instant::now() < deadline {
                tokio::time::sleep(Duration::from_millis(50)).await;
                available = free_port(port);
            }
            if !available {
                available = matches!(
                    timeout(
                        Duration::from_secs(2),
                        tokio::net::TcpStream::connect((std::net::Ipv4Addr::LOCALHOST, port)),
                    ).await,
                    Ok(Err(e)) if e.kind() == std::io::ErrorKind::ConnectionRefused
                );
            }
        }
        // Never relax the conflict check for an external or newly selected port,
        // and never stop another process to obtain it.
        if !available {
            return Err(format!(
                "模型引擎連接埠 {port} 已被使用。請停止舊啟動器或更換連接埠。"
            ));
        }
        let command = engine_command(&config)?;
        if !Path::new(&command[0]).is_file() {
            return Err("找不到 llama-server.exe，請確認 llama.cpp 資料夾。".into());
        }
        let mut layers = model["gpu_layers"].as_i64().unwrap_or(0);
        if model["auto_fit"] == true && std::env::var("LMM_SKIP_FIT").as_deref() != Ok("1") {
            let fit =
                Path::new(config["engine_dir"].as_str().unwrap()).join("llama-fit-params.exe");
            if !fit.is_file() {
                return Err(
                    "找不到 llama-fit-params.exe；請選擇完整引擎資料夾，或關閉自動 GPU 分配。"
                        .into(),
                );
            }
            let mut cmd = hidden_command(&fit.to_string_lossy());
            cmd.current_dir(config["engine_dir"].as_str().unwrap())
                .args([
                    "-m",
                    model["path"].as_str().unwrap(),
                    "-c",
                    &numeric(&model["context"]),
                    "-ctk",
                    model["cache_type"].as_str().unwrap(),
                    "-ctv",
                    model["cache_type"].as_str().unwrap(),
                ]);
            if model["fit_target_enabled"] == true {
                cmd.args(["--fit-target", &numeric(&model["fit_target_mib"])]);
            }
            let output = timeout(Duration::from_secs(180), cmd.output())
                .await
                .map_err(|_| "GPU 分配計算超時。")?
                .map_err(|e| e.to_string())?;
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            let re = regex::Regex::new(r"(?:^|\s)-ngl\s+(-?\d+)").unwrap();
            layers = re
                .captures(&text)
                .and_then(|c| c[1].parse().ok())
                .filter(|_| output.status.success())
                .ok_or_else(|| {
                    format!(
                        "GPU 分配計算失敗（退出代碼 {:?}）。\nllama-fit-params 輸出：\n{}",
                        output.status.code(),
                        clean(&text, &self.token)
                    )
                })?;
        }
        let threads = model["cpu_threads"].as_i64().unwrap_or(0);
        let threads = if threads > 0 { threads } else { -1 };
        let mut args = vec![
            "-m".into(),
            model["path"].as_str().unwrap().into(),
            "--alias".into(),
            model["id"].as_str().unwrap().into(),
            "-c".into(),
            numeric(&model["context"]),
            "-ctk".into(),
            model["cache_type"].as_str().unwrap().into(),
            "-ctv".into(),
            model["cache_type"].as_str().unwrap().into(),
            "--fit".into(),
            "off".into(),
            "-ngl".into(),
            layers.to_string(),
            "-t".into(),
            threads.to_string(),
            "-tb".into(),
            threads.to_string(),
        ];
        args.extend(
            [
                "--jinja",
                "--reasoning-effort",
                "default",
                "--reasoning-budget",
                "-1",
                "--timeout",
                "18000",
                "--sse-ping-interval",
                "10",
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "-np",
                "1",
                "--slots",
                "--metrics",
            ]
            .map(str::to_owned),
        );
        if model["mtp"] == true {
            args.extend([
                "--spec-type".into(),
                "draft-mtp".into(),
                "--spec-draft-n-max".into(),
                if model["mtp_draft_max"].is_null() {
                    "2".into()
                } else {
                    numeric(&model["mtp_draft_max"])
                },
            ]);
            if model["mtp_source"] == "external" {
                args.extend([
                    "--spec-draft-model".into(),
                    model["mtp_draft_path"].as_str().unwrap().into(),
                ]);
            }
        }
        if model["vision"] == true {
            args.extend(["--mmproj".into(), model["mmproj"].as_str().unwrap().into()]);
        }
        let mut child = hidden_command(&command[0])
            .args(&command[1..])
            .args(args)
            .current_dir(config["engine_dir"].as_str().unwrap())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| e.to_string())?;
        if let Some(out) = child.stdout.take() {
            let m = self.clone();
            tokio::spawn(async move {
                let mut lines = BufReader::new(out).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    m.engine_line(&line).await;
                }
            });
        }
        if let Some(out) = child.stderr.take() {
            let m = self.clone();
            tokio::spawn(async move {
                let mut lines = BufReader::new(out).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    m.engine_line(&line).await;
                }
            });
        }
        self.inner.lock().await.process = Some(child);
        let base = format!("http://127.0.0.1:{port}");
        let deadline = Instant::now() + Duration::from_secs(300);
        loop {
            if Instant::now() >= deadline {
                return Err("模型載入超過五分鐘，已停止本次載入。".into());
            }
            {
                let mut i = self.inner.lock().await;
                if let Some(exit) = i.process.as_mut().and_then(|p| p.try_wait().ok().flatten()) {
                    return Err(format!(
                        "模型引擎在載入時結束（代碼 {}）。請檢查模型與 GPU／記憶體設定。",
                        exit.code().unwrap_or(-1)
                    ));
                }
            }
            if self
                .http
                .get(format!("{base}/health"))
                .timeout(Duration::from_secs(1))
                .send()
                .await
                .is_ok_and(|r| r.status().as_u16() == 200)
            {
                break;
            }
            sleep(Duration::from_millis(150)).await;
        }
        {
            let mut i = self.inner.lock().await;
            i.loaded = Some(
                json!({"model":model,"engine_dir":config["engine_dir"],"engine_port":port,"gpu_layers_effective":layers}),
            );
            i.last_used = Instant::now();
            i.deferred_unload = false;
        }
        if let Ok(props) = self
            .http
            .get(format!("{base}/props"))
            .timeout(Duration::from_secs(1))
            .send()
            .await
        {
            if let Ok(props) = props.json::<Value>().await {
                self.refine(model, &props).await?;
            }
        }
        {
            let mut i = self.inner.lock().await;
            i.model = Some(model.clone());
            i.state = "ready";
        }
        self.log(&format!(
            "模型已載入：{}（GPU 層數 {layers}）。",
            model["name"].as_str().unwrap()
        ))
        .await;
        Ok(())
    }
    async fn engine_line(&self, line: &str) {
        let low = line.to_lowercase();
        if ["out of memory", "cuda error", "failed to allocate"]
            .iter()
            .any(|s| low.contains(s))
        {
            let msg = "模型引擎回報記憶體或 GPU 錯誤，請減少 GPU 層數或上下文容量。";
            self.inner.lock().await.last_error = json!(msg);
            self.log(msg).await;
        }
    }
    async fn refine(&self, model: &mut Value, props: &Value) -> Result<(), String> {
        let mut i = self.inner.lock().await;
        i.engine_version = props["build_info"]
            .as_str()
            .map(|s| json!(clean(s, &self.token)))
            .unwrap_or(Value::Null);
        let Some(template) = props["chat_template"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
        else {
            return Ok(());
        };
        let mut caps = gguf::analyze_reasoning_template(template);
        if caps["reasoning_capability"] == "unknown" {
            return Ok(());
        }
        if props["chat_template_caps"]["supports_reasoning_effort"] == false {
            caps["reasoning_efforts"] = json!([]);
            caps["reasoning_default_effort"] = json!("");
        }
        for k in [
            "reasoning_capability",
            "reasoning_efforts",
            "reasoning_default_effort",
            "reasoning_budget_supported",
            "reasoning_toggle_keys",
        ] {
            model[k] = caps[k].clone();
        }
        model["reasoning_detection"] = json!("runtime");
        model["reasoning_supported"] = json!(matches!(
            caps["reasoning_capability"].as_str(),
            Some("always" | "toggle")
        ));
        let mut config = i.store.config().clone();
        if let Some(saved) = config["models"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|m| m["id"] == model["id"])
        {
            for k in [
                "reasoning_capability",
                "reasoning_efforts",
                "reasoning_default_effort",
                "reasoning_budget_supported",
                "reasoning_toggle_keys",
                "reasoning_detection",
                "reasoning_supported",
            ] {
                saved[k] = model[k].clone();
            }
            i.store.persist_runtime(config)?;
        }
        Ok(())
    }
    async fn base(&self) -> String {
        let i = self.inner.lock().await;
        let port = i
            .loaded
            .as_ref()
            .and_then(|v| v["engine_port"].as_u64())
            .unwrap_or(
                self.engine_port
                    .unwrap_or(i.store.config()["engine_port"].as_u64().unwrap() as u16)
                    as u64,
            );
        format!("http://127.0.0.1:{port}")
    }
    async fn ready(self: &Arc<Self>, ticket: &Arc<Ticket>) -> Result<(), String> {
        let mut waited = false;
        loop {
            let mut i = self.inner.lock().await;
            if i.state == "ready"
                && i.model
                    .as_ref()
                    .is_some_and(|m| m["id"] == ticket.model["id"])
                && !self.changed(&i, &ticket.model)
            {
                return Ok(());
            }
            if waited && i.state == "error" {
                return Err(i.last_error.as_str().unwrap_or("模型載入失敗。").to_owned());
            }
            ticket.set("phase", json!("loading"));
            if i.load_cancel.is_some() || i.manual || i.state == "loading" {
                drop(i);
                waited = true;
                sleep(Duration::from_millis(50)).await;
                continue;
            }
            let token = CancellationToken::new();
            i.load_cancel = Some(token.clone());
            let model = ticket.model.clone();
            drop(i);
            let manager = self.clone();
            tokio::spawn(async move {
                tokio::select! {_ = token.cancelled()=>{manager.unload().await;},_ = manager.load(model)=>{}}
                manager.inner.lock().await.load_cancel = None;
            });
            waited = true;
            sleep(Duration::from_millis(25)).await;
        }
    }
    async fn cancel_upstream(&self, ticket: &Arc<Ticket>) {
        use std::sync::atomic::Ordering::SeqCst;
        if !ticket.upstream_sent.load(SeqCst) {
            ticket.set("cancel_confirmed", json!(true));
            return;
        }
        let base = self.base().await;
        let mut success = false;
        for attempt in 0..3 {
            success = self
                .http
                .delete(format!("{base}/v1/stream"))
                .query(&[("conv_id", &ticket.id)])
                .timeout(Duration::from_secs(2))
                .send()
                .await
                .is_ok_and(|r| r.status().is_success());
            if success && (ticket.upstream_headers.load(SeqCst) || attempt >= 1) {
                break;
            }
            sleep(Duration::from_millis(150 * (attempt + 1))).await;
        }
        if success {
            let deadline = Instant::now() + Duration::from_secs(3);
            let mut replay = Instant::now() + Duration::from_millis(500);
            loop {
                let slots = match self
                    .http
                    .get(format!("{base}/slots"))
                    .timeout(Duration::from_secs(1))
                    .send()
                    .await
                {
                    Ok(r) => r.json::<Value>().await.ok(),
                    Err(_) => None,
                };
                match slots {
                    None => {
                        ticket.set("cancel_confirmed", json!(true));
                        return;
                    }
                    Some(v) => {
                        if let Some(slots) = v.as_array() {
                            if !slots.iter().any(|s| s["is_processing"] == true) {
                                ticket.set("cancel_confirmed", json!(true));
                                return;
                            }
                        } else {
                            break;
                        }
                    }
                }
                if Instant::now() >= deadline {
                    break;
                }
                if Instant::now() >= replay {
                    let _ = self
                        .http
                        .delete(format!("{base}/v1/stream"))
                        .query(&[("conv_id", &ticket.id)])
                        .timeout(Duration::from_secs(2))
                        .send()
                        .await;
                    replay = Instant::now() + Duration::from_millis(500);
                }
                sleep(Duration::from_millis(200)).await;
            }
        }
        self.log("引擎在取消寬限時間後仍未停止，正在停止本管理器擁有的模型進程。")
            .await;
        self.unload().await;
        ticket.set("cancel_confirmed", json!(true));
    }
    fn observe(ticket: &Arc<Ticket>, value: &Value) {
        if !value.is_object() {
            return;
        }
        let mut r = ticket.record.lock().unwrap();
        for (v, k) in [
            (&value["usage"]["prompt_tokens"], "prompt_tokens"),
            (&value["usage"]["completion_tokens"], "generated_tokens"),
            (
                &value["usage"]["prompt_tokens_details"]["cached_tokens"],
                "cached_tokens",
            ),
            (
                &value["usage"]["completion_tokens_details"]["reasoning_tokens"],
                "thinking_tokens",
            ),
            (&value["timings"]["prompt_per_second"], "prompt_tps"),
            (&value["timings"]["predicted_per_second"], "generation_tps"),
            (&value["timings"]["prompt_n"], "prompt_tokens"),
            (&value["timings"]["predicted_n"], "generated_tokens"),
            (&value["tokens_cached"], "cached_tokens"),
        ] {
            if v.as_f64().is_some_and(|n| n.is_finite() && n >= 0.) {
                r[k] = v.clone();
            }
        }
        for choice in value["choices"].as_array().into_iter().flatten() {
            let delta = choice
                .get("delta")
                .filter(|v| v.is_object() && !v.as_object().unwrap().is_empty())
                .unwrap_or(&choice["message"]);
            let present = |k: &str| {
                delta
                    .get(k)
                    .is_some_and(|v| !v.is_null() && v != false && v != "" && v != &json!([]))
            };
            let phase = if present("reasoning_content") || present("reasoning") {
                "thinking"
            } else if present("content") || present("tool_calls") {
                "generating"
            } else {
                continue;
            };
            r["phase"] = json!(phase);
            if r["first_token_seconds"].is_null() {
                r["first_token_seconds"] = json!(round(ticket.started.elapsed().as_secs_f64(), 3));
            }
        }
    }
    async fn process(self: &Arc<Self>, ticket: &Arc<Ticket>) -> Result<(), String> {
        self.ready(ticket).await?;
        let mut model = ticket.model.clone();
        {
            let i = self.inner.lock().await;
            if let Some(current) = i.model.as_ref().filter(|m| m["id"] == model["id"]) {
                for k in [
                    "reasoning_capability",
                    "reasoning_efforts",
                    "reasoning_default_effort",
                    "reasoning_budget_supported",
                    "reasoning_toggle_keys",
                    "reasoning_detection",
                ] {
                    model[k] = current[k].clone();
                }
            }
        }
        let body = request::policy(
            &ticket.body,
            &model,
            &ticket.profile,
            &mut ticket.record.lock().unwrap(),
        )?;
        ticket.set("phase", json!("prompt"));
        use std::sync::atomic::Ordering::SeqCst;
        ticket.upstream_sent.store(true, SeqCst);
        let mut req = self
            .http
            .post(format!("{}/v1/chat/completions", self.base().await))
            .header("X-Conversation-Id", &ticket.id)
            .json(&body);
        if let Some(agent) = ticket.headers["x-agent-task-id"].as_str() {
            req = req.header("X-Agent-Task-Id", agent);
        }
        let reply = req
            .send()
            .await
            .map_err(|_| "本地引擎連線失敗（ConnectError）；本次任務未自動重送。".to_owned())?;
        ticket.upstream_headers.store(true, SeqCst);
        let code = reply.status().as_u16();
        if code >= 400 {
            let bytes = reply
                .bytes()
                .await
                .map_err(|_| "本地引擎連線失敗（ReadError）；本次任務未自動重送。")?;
            ticket.reply(Reply {
                code,
                body: bytes.to_vec(),
                stream: None,
            });
            self.finish(
                ticket,
                "error",
                Some(&format!(
                    "模型引擎回覆 HTTP {code}。請檢查上下文長度與模型設定。"
                )),
            )
            .await;
            return Ok(());
        }
        let streaming = reply
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .is_some_and(|s| s.contains("text/event-stream"));
        if streaming {
            let (tx, rx) = mpsc::channel(16);
            ticket.reply(Reply {
                code,
                body: vec![],
                stream: Some(rx),
            });
            let mut stream = reply.bytes_stream();
            let mut buffer = Vec::new();
            while let Some(chunk) = stream.next().await {
                let chunk =
                    chunk.map_err(|_| "本地引擎連線失敗（ReadError）；本次任務未自動重送。")?;
                buffer.extend_from_slice(&chunk);
                while let Some(end) = buffer.iter().position(|b| *b == b'\n') {
                    let mut line = buffer.drain(..=end).collect::<Vec<_>>();
                    line.pop();
                    if line.last() == Some(&b'\r') {
                        line.pop();
                    }
                    let text = String::from_utf8_lossy(&line);
                    if let Some(payload) = text.strip_prefix("data:") {
                        if let Ok(v) = serde_json::from_str(payload.trim()) {
                            Self::observe(ticket, &v);
                        }
                    }
                    line.push(b'\n');
                    if tx.send(Bytes::from(line)).await.is_err() {
                        ticket.cancel.cancel();
                        return Ok(());
                    }
                }
            }
            if !buffer.is_empty() {
                let text = String::from_utf8_lossy(&buffer);
                if let Some(payload) = text.strip_prefix("data:") {
                    if let Ok(v) = serde_json::from_str(payload.trim()) {
                        Self::observe(ticket, &v);
                    }
                }
                buffer.push(b'\n');
                let _ = tx.send(Bytes::from(buffer)).await;
            }
            if !ticket.cancel.is_cancelled() {
                self.finish(ticket, "completed", None).await;
            }
            return Ok(());
        } else {
            let bytes = reply
                .bytes()
                .await
                .map_err(|_| "本地引擎連線失敗（ReadError）；本次任務未自動重送。")?;
            if let Ok(v) = serde_json::from_slice(&bytes) {
                Self::observe(ticket, &v);
            }
            ticket.reply(Reply {
                code,
                body: bytes.to_vec(),
                stream: None,
            });
        }
        if !ticket.cancel.is_cancelled() {
            self.finish(ticket, "completed", None).await;
        }
        Ok(())
    }
    async fn worker(self: Arc<Self>, mut queue: mpsc::Receiver<Arc<Ticket>>) {
        while let Some(ticket) = tokio::select! { _ = self.stopping.cancelled() => None, ticket = queue.recv() => ticket }
        {
            if ticket.done.is_cancelled() {
                continue;
            }
            {
                self.inner.lock().await.active = Some(ticket.id.clone());
            }
            let result = tokio::select! {biased;_ = ticket.cancel.cancelled()=>None,r=self.process(&ticket)=>Some(r)};
            if ticket.cancel.is_cancelled() {
                let cancel_loading = {
                    let i = self.inner.lock().await;
                    if i.state == "loading" && !i.manual && i.tickets.len() <= 1 {
                        i.load_cancel.clone()
                    } else {
                        None
                    }
                };
                if let Some(cancel) = cancel_loading {
                    cancel.cancel();
                    while self.inner.lock().await.load_cancel.is_some() {
                        sleep(Duration::from_millis(25)).await;
                    }
                }
                self.cancel_upstream(&ticket).await;
                self.finish(&ticket, "cancelled", None).await;
            } else if let Some(Err(e)) = result {
                self.finish(&ticket, "error", Some(&e)).await;
            }
            self.inner.lock().await.active = None;
            if self.stopping.is_cancelled() {
                break;
            }
        }
    }
    async fn monitor(self: Arc<Self>) {
        let mut resource_at = Instant::now();
        while !self.stopping.is_cancelled() {
            let (ready, idle_unload) = {
                let mut i = self.inner.lock().await;
                if i.state == "ready" {
                    if let Some(exit) = i.process.as_mut().and_then(|p| p.try_wait().ok().flatten())
                    {
                        i.state = "error";
                        i.last_error = json!(format!(
                            "模型引擎意外結束（代碼 {}）。下次請求可以重新載入。",
                            exit.code().unwrap_or(-1)
                        ));
                        i.slots = json!([]);
                    }
                }
                let idle_minutes = i.store.config()["idle_minutes"].as_f64().unwrap_or(15.);
                let keep = i
                    .model
                    .as_ref()
                    .and_then(|m| {
                        i.store.config()["models"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .find(|s| s["id"] == m["id"])
                    })
                    .is_some_and(|m| m["keep_loaded"] == true);
                (
                    i.state == "ready",
                    i.state == "ready"
                        && i.tickets.is_empty()
                        && (i.deferred_unload
                            || (!keep
                                && idle_minutes > 0.
                                && i.last_used.elapsed().as_secs_f64() >= idle_minutes * 60.)),
                )
            };
            if ready {
                if let Ok(response) = self
                    .http
                    .get(format!("{}/slots", self.base().await))
                    .timeout(Duration::from_millis(700))
                    .send()
                    .await
                {
                    if let Ok(slots) = response.json::<Value>().await {
                        let mut i = self.inner.lock().await;
                        let allowed = [
                            "id",
                            "id_task",
                            "state",
                            "is_processing",
                            "n_ctx",
                            "n_decoded",
                            "n_prompt_tokens",
                            "n_prompt_tokens_processed",
                            "n_prompt_tokens_cache",
                            "n_tokens",
                            "n_past",
                            "prompt_progress",
                        ];
                        i.slots = json!(slots
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(|v| v.as_object())
                            .map(|s| Value::Object(
                                s.iter()
                                    .filter(|(k, _)| allowed.contains(&k.as_str()))
                                    .map(|(k, v)| (k.clone(), v.clone()))
                                    .collect()
                            ))
                            .collect::<Vec<_>>());
                        if let Some(ticket) = i.active.as_ref().and_then(|id| i.tickets.get(id)) {
                            if let Some(slot) = slots
                                .as_array()
                                .into_iter()
                                .flatten()
                                .find(|s| s["is_processing"] == true)
                            {
                                if matches!(
                                    ticket.record.lock().unwrap()["phase"].as_str(),
                                    Some("prompt" | "thinking" | "generating")
                                ) {
                                    for (k, target) in [
                                        ("n_prompt_tokens", "prompt_tokens"),
                                        ("n_decoded", "generated_tokens"),
                                        ("n_prompt_tokens_cache", "cached_tokens"),
                                    ] {
                                        if slot[k].as_f64().is_some_and(|n| n >= 0.) {
                                            ticket.set(target, slot[k].clone());
                                        }
                                    }
                                    if let (Some(total), Some(processed)) = (
                                        slot["n_prompt_tokens"].as_f64().filter(|n| *n > 0.),
                                        slot["n_prompt_tokens_processed"].as_f64(),
                                    ) {
                                        ticket.set(
                                            "prompt_progress",
                                            json!((processed / total).clamp(0., 1.)),
                                        );
                                    }
                                }
                            }
                        }
                    }
                }
            }
            if idle_unload {
                self.unload().await;
            }
            if Instant::now() >= resource_at {
                let resources = self.sample_resources().await;
                self.inner.lock().await.resources = resources;
                resource_at = Instant::now() + Duration::from_secs(5);
            }
            sleep(Duration::from_millis(350)).await;
        }
    }
    pub async fn stop(&self) {
        {
            let mut i = self.inner.lock().await;
            i.accepting = false;
            if let Some(cancel) = &i.load_cancel {
                cancel.cancel();
            }
        }
        let tickets = self
            .inner
            .lock()
            .await
            .tickets
            .values()
            .cloned()
            .collect::<Vec<_>>();
        for ticket in tickets {
            self.cancel(ticket).await;
        }
        let deadline = Instant::now() + Duration::from_secs(8);
        while !self.inner.lock().await.tickets.is_empty() && Instant::now() < deadline {
            sleep(Duration::from_millis(25)).await;
        }
        self.stopping.cancel();
        self.engines.cancel().await;
        for mut task in self.background.lock().await.drain(..) {
            if timeout(Duration::from_secs(3), &mut task).await.is_err() {
                task.abort();
                let _ = task.await;
            }
        }
        self.unload().await;
    }
    async fn save(&self, mut config: Value) -> Result<Value, String> {
        let mut i = self.inner.lock().await;
        if let Some(models) = config.get_mut("models").and_then(Value::as_array_mut) {
            for model in models {
                let Some(path) = model["path"].as_str() else {
                    continue;
                };
                let old = i.store.config()["models"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|m| m["id"] == model["id"]);
                let changed = old.is_none_or(|m| m["path"] != model["path"]);
                if changed
                    || model["mtp_capability"] == "unknown"
                    || model["native_context"].as_f64().unwrap_or(0.) <= 0.
                    || !matches!(
                        model["reasoning_detection"].as_str(),
                        Some("gguf" | "runtime")
                    )
                {
                    let caps = gguf::inspect_gguf_capabilities(Path::new(path));
                    gguf::apply_detected_model_capabilities(model, &caps, changed);
                }
            }
        }
        let saved = i.store.replace_validated(config)?;
        history_prune(&mut i, &self.data)?;
        prune_bodies(
            &self.data,
            saved["log_retention_days"].as_f64().unwrap_or(7.),
        );
        drop(i);
        self.log("設定已儲存。需要重新載入的參數會在下次載入時套用。")
            .await;
        Ok(saved)
    }
    async fn status(&self) -> Value {
        let mut i = self.inner.lock().await;
        let config = i.store.config();
        let idle = if i.tickets.is_empty() {
            i.last_used.elapsed().as_secs_f64()
        } else {
            0.
        };
        let current = i
            .model
            .as_ref()
            .and_then(|m| {
                config["models"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|s| s["id"] == m["id"])
            })
            .or(i.model.as_ref());
        let limit = current
            .filter(|m| m["keep_loaded"] != true)
            .and_then(|_| config["idle_minutes"].as_f64())
            .filter(|n| *n > 0.)
            .map(|n| n * 60.);
        let pending_restart = config["api_port"] != json!(self.port);
        let pending_reload = i.loaded.as_ref().is_some_and(|l| {
            config["models"]
                .as_array()
                .unwrap()
                .iter()
                .find(|m| m["id"] == l["model"]["id"])
                .is_none_or(|m| self.changed(&i, m))
        });
        let model_id = i.model.as_ref().map(|m| m["id"].clone());
        let model_name = current.map(|m| m["name"].clone());
        let loaded = if i.state == "ready" {
            i.loaded.as_ref().map(|l| {
                let mut m = effective(&l["model"]);
                m["gpu_layers_effective"] = l["gpu_layers_effective"].clone();
                m
            })
        } else {
            None
        };
        let pid = if let Some(p) = i.process.as_mut() {
            if p.try_wait().ok().flatten().is_none() {
                p.id()
            } else {
                None
            }
        } else {
            None
        };
        json!({"state":i.state,"model_id":model_id,"model_name":model_name,"pid":pid,"accepting":i.accepting,"active_count":i.active.as_ref().is_some_and(|id|i.tickets.contains_key(id)) as u8,"queued_count":i.tickets.len().saturating_sub(i.active.as_ref().is_some_and(|id|i.tickets.contains_key(id)) as usize),"uptime_seconds":round(self.started.elapsed().as_secs_f64(),1),"idle_seconds":round(idle,1),"unload_in_seconds":if i.state=="ready"&&i.tickets.is_empty(){limit.map(|l|round((l-idle).max(0.),1))}else{None},"last_error":i.last_error,"api_url":format!("http://127.0.0.1:{}/v1/chat/completions",self.port),"engine_version":i.engine_version,"slots":i.slots,"resources":i.resources,"loaded_model_settings":loaded,"pending_config":pending_restart||pending_reload,"pending_restart":pending_restart,"pending_model_reload":pending_reload,"deferred_unload":i.deferred_unload})
    }
    async fn records(&self) -> Value {
        let i = self.inner.lock().await;
        let mut rows = i
            .tickets
            .values()
            .map(|t| {
                let mut r = t.record.lock().unwrap().clone();
                r["elapsed_seconds"] = json!(round(t.started.elapsed().as_secs_f64(), 3));
                r
            })
            .chain(i.history.iter().cloned())
            .collect::<Vec<_>>();
        rows.sort_by(|a, b| b["started_at"].as_str().cmp(&a["started_at"].as_str()));
        rows.truncate(MAX_HISTORY);
        json!({"requests":rows})
    }
}
fn numeric(v: &Value) -> String {
    v.as_f64()
        .filter(|n| n.fract() == 0.)
        .map(|n| (n as i64).to_string())
        .unwrap_or_else(|| v.to_string())
}
fn check_gguf(value: &Value, missing: &str, invalid: &str, unreadable: &str) -> Result<(), String> {
    use std::io::Read;
    let p = Path::new(value.as_str().unwrap_or(""));
    if !p.is_file() {
        return Err(missing.into());
    }
    let mut bytes = [0; 4];
    std::fs::File::open(p)
        .and_then(|mut f| f.read_exact(&mut bytes))
        .map_err(|_| unreadable)?;
    if &bytes != b"GGUF" {
        return Err(invalid.into());
    }
    Ok(())
}
fn free_port(port: u16) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawSocket;
        #[link(name = "ws2_32")]
        unsafe extern "system" {
            fn setsockopt(
                socket: usize,
                level: i32,
                option: i32,
                value: *const u8,
                length: i32,
            ) -> i32;
        }
        let Ok(socket) = tokio::net::TcpSocket::new_v4() else {
            return false;
        };
        let exclusive: i32 = 1;
        // Match Python SO_EXCLUSIVEADDRUSE, including rebind after an owned
        // engine exits. SO_REUSEADDR could steal an external listener's port.
        if unsafe {
            setsockopt(
                socket.as_raw_socket() as usize,
                0xffff,
                -5,
                (&exclusive as *const i32).cast(),
                4,
            )
        } != 0
        {
            return false;
        }
        socket
            .bind(std::net::SocketAddr::from(([127, 0, 0, 1], port)))
            .is_ok()
    }
    #[cfg(not(windows))]
    std::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).is_ok()
}
async fn gate(State(m): State<Arc<Manager>>, req: Request, next: Next) -> Response {
    if let Some(origin) = req
        .headers()
        .get("origin")
        .and_then(|v| v.to_str().ok())
        .filter(|v| !v.is_empty())
    {
        if origin != format!("http://127.0.0.1:{}", m.port)
            && origin != format!("http://localhost:{}", m.port)
        {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"error":"不允許此網站存取本地管理器。"})),
            )
                .into_response();
        }
    }
    if req.uri().path().starts_with("/manager/") {
        let token = req
            .headers()
            .get("x-manager-token")
            .map(|v| v.as_bytes())
            .unwrap_or_default();
        if !bool::from(m.token.as_bytes().ct_eq(token)) {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error":"需要有效的管理權杖。"})),
            )
                .into_response();
        }
    }
    next.run(req).await
}
async fn config(State(m): State<Arc<Manager>>) -> Json<Value> {
    Json(m.inner.lock().await.store.config().clone())
}
async fn save(State(m): State<Arc<Manager>>, body: Bytes) -> ApiResult {
    Ok(Json(m.save(parse(&body)?).await?))
}
async fn status(State(m): State<Arc<Manager>>) -> Json<Value> {
    Json(m.status().await)
}
async fn records(State(m): State<Arc<Manager>>) -> Json<Value> {
    Json(m.records().await)
}
async fn logs(State(m): State<Arc<Manager>>) -> Json<Value> {
    Json(json!({"lines":m.inner.lock().await.lines}))
}
async fn load(State(m): State<Arc<Manager>>, body: Bytes) -> ApiResult {
    let body = parse(&body)?;
    let default = m.inner.lock().await.store.config()["default_model_id"].clone();
    m.begin_load(
        body.get("model_id")
            .unwrap_or(&default)
            .as_str()
            .unwrap_or(""),
    )
    .await?;
    Ok(Json(json!({"ok":true})))
}
async fn unload(State(m): State<Arc<Manager>>) -> ApiResult {
    let mut i = m.inner.lock().await;
    if !i.tickets.is_empty() {
        i.deferred_unload = true;
        return Ok(Json(json!({"ok":true,"deferred":true})));
    }
    if let Some(cancel) = &i.load_cancel {
        cancel.cancel();
    }
    drop(i);
    m.unload().await;
    Ok(Json(json!({"ok":true,"deferred":false})))
}
async fn accepting(State(m): State<Arc<Manager>>, body: Bytes) -> ApiResult {
    let body = parse(&body)?;
    let value = body["accepting"]
        .as_bool()
        .ok_or_else(|| ApiError::invalid("accepting 必須是布林值。"))?;
    m.inner.lock().await.accepting = value;
    Ok(Json(json!({"ok":true,"accepting":value})))
}
async fn keep(State(m): State<Arc<Manager>>, body: Bytes) -> ApiResult {
    let body = parse(&body)?;
    let value = body["keep_loaded"]
        .as_bool()
        .ok_or_else(|| ApiError::invalid("keep_loaded 必須是布林值。"))?;
    let mut config = m.inner.lock().await.store.config().clone();
    let model = config["models"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|m| m["id"] == body["model_id"])
        .ok_or_else(|| ApiError::invalid("找不到指定模型。"))?;
    model["keep_loaded"] = json!(value);
    m.save(config).await?;
    Ok(Json(json!({"ok":true})))
}
async fn cancel(State(m): State<Arc<Manager>>, ApiPath(id): ApiPath<String>) -> ApiResult {
    let i = m.inner.lock().await;
    let ticket = i.tickets.get(&id).cloned();
    if ticket.is_none() && !i.history.iter().any(|r| r["id"] == id) {
        return Err(ApiError::detail(404, "找不到此任務。"));
    }
    drop(i);
    if let Some(ticket) = ticket {
        m.cancel(ticket).await;
    }
    Ok(Json(json!({"ok":true})))
}
async fn shutdown(State(m): State<Arc<Manager>>) -> Json<Value> {
    m.inner.lock().await.accepting = false;
    tokio::spawn(async move {
        sleep(Duration::from_millis(100)).await;
        m.stop().await;
    });
    Json(json!({"ok":true}))
}
async fn clear(State(m): State<Arc<Manager>>) -> ApiResult {
    let mut i = m.inner.lock().await;
    let requests = i.history.len();
    let logs = i.lines.len();
    let mut bodies = 0;
    for path in std::fs::read_dir(m.data.join("request-bodies"))
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
    {
        if std::fs::remove_file(path).is_ok() {
            bodies += 1;
        }
    }
    i.history.clear();
    storage::atomic_json(&m.data.join("history.json"), &json!([]))?;
    i.lines.clear();
    Ok(Json(
        json!({"ok":true,"requests":requests,"logs":logs,"request_bodies":bodies}),
    ))
}
fn model_list(config: &Value) -> Value {
    let mut rows = vec![];
    for m in config["models"].as_array().into_iter().flatten() {
        let mut ids = vec![m["id"].as_str().unwrap().to_owned()];
        ids.extend(
            config["profiles"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|p| {
                    format!(
                        "{}::{}",
                        m["id"].as_str().unwrap(),
                        p["id"].as_str().unwrap()
                    )
                }),
        );
        for id in ids {
            rows.push(
                json!({"id":id,"object":"model","created":0,"owned_by":"local-model-manager"}),
            );
        }
    }
    json!({"object":"list","data":rows})
}
async fn models(State(m): State<Arc<Manager>>) -> Json<Value> {
    Json(model_list(m.inner.lock().await.store.config()))
}
async fn connection(State(m): State<Arc<Manager>>) -> ApiResult {
    let i = m.inner.lock().await;
    let mut effective = i.store.config().clone();
    if let Some(directory) = &i.managed_engine_dir {
        effective["engine_dir"] = json!(directory);
    }
    let c = &effective;
    let status = m.engines.status().await;
    let selected = status["policy"]["mode"] != "managed" || status["active"].is_string();
    let exists = selected && Path::new(&engine_command(c)?[0]).is_file();
    let occupied = !free_port(
        m.engine_port
            .unwrap_or(c["engine_port"].as_u64().unwrap() as u16),
    );
    let owned = i.process.is_some();
    Ok(Json(
        json!({"ok":exists&&(!occupied||owned),"api_url":format!("http://127.0.0.1:{}/v1/chat/completions",m.port),"models":model_list(c)["data"],"engine_exists":exists,"python_ok":true,"port_status":if owned{"引擎由本管理器執行"}else if occupied{"引擎連接埠已被其他程序使用"}else{"可用"}}),
    ))
}
async fn vscode_preview(State(m): State<Arc<Manager>>) -> Json<Value> {
    let (models, agents) = vscode::locations();
    Json(vscode::preview_at(
        m.inner.lock().await.store.config(),
        &models,
        &agents,
    ))
}
async fn vscode_apply(State(m): State<Arc<Manager>>) -> ApiResult {
    let config = m.inner.lock().await.store.config().clone();
    let (models, agents) = vscode::locations();
    let data = m.data.clone();
    Ok(Json(
        tokio::task::spawn_blocking(move || vscode::apply_at(&config, &data, &models, &agents))
            .await
            .map_err(|e| ApiError::invalid(e.to_string()))??,
    ))
}
async fn capabilities(body: Bytes) -> ApiResult {
    let body = parse(&body)?;
    let path = body["path"]
        .as_str()
        .filter(|p| !p.trim().is_empty())
        .ok_or_else(|| ApiError::invalid("請指定要檢查的 GGUF 模型路徑。"))?
        .to_owned();
    Ok(Json(
        tokio::task::spawn_blocking(move || gguf::inspect_gguf_capabilities(Path::new(&path)))
            .await
            .map_err(|e| ApiError::invalid(e.to_string()))?,
    ))
}
async fn scan(State(m): State<Arc<Manager>>) -> Json<Value> {
    let dirs = m.inner.lock().await.store.config()["model_dirs"].clone();
    Json(
        tokio::task::spawn_blocking(move || scan_models(&dirs))
            .await
            .unwrap_or(json!({"models":[],"projectors":[]})),
    )
}
fn scan_models(dirs: &Value) -> Value {
    fn visit(dir: &Path, files: &mut Vec<PathBuf>) {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_symlink() && p.is_dir() {
                    continue;
                }
                if p.is_dir() {
                    visit(&p, files);
                } else if p.is_file() {
                    files.push(p);
                }
            }
        }
    }
    let mut files = vec![];
    for dir in dirs
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        visit(Path::new(dir), &mut files);
    }
    let mut seen = std::collections::HashSet::new();
    let mut models = vec![];
    let mut projectors = vec![];
    let shard = regex::Regex::new(r"-(\d{5})-of-(\d{5})\.gguf$").unwrap();
    for p in &files {
        let name = p.file_name().unwrap_or_default().to_string_lossy();
        let low = name.to_lowercase();
        if !low.ends_with(".gguf") || low.starts_with("mtp-") || low.contains("draft") {
            continue;
        }
        if !seen.insert(
            p.canonicalize()
                .unwrap_or(p.clone())
                .to_string_lossy()
                .to_lowercase(),
        ) {
            continue;
        }
        let mut size = p.metadata().map(|m| m.len()).unwrap_or(0);
        if let Some(c) = shard.captures(&low) {
            if &c[1] != "00001" {
                continue;
            }
            let start = c.get(0).unwrap().start();
            let prefix = &name[..start];
            let suffix = format!("-of-{}.gguf", &c[2]);
            size = files
                .iter()
                .filter(|f| {
                    f.parent() == p.parent()
                        && f.file_name().is_some_and(|n| {
                            let n = n.to_string_lossy();
                            n.starts_with(&format!("{prefix}-")) && n.ends_with(&suffix)
                        })
                })
                .filter_map(|f| f.metadata().ok().map(|m| m.len()))
                .sum();
        }
        let mut row = gguf::inspect_gguf_capabilities(p);
        row["path"] = json!(p.to_string_lossy());
        row["name"] = json!(p.file_stem().unwrap_or_default().to_string_lossy());
        row["size_bytes"] = json!(size);
        if low.contains("mmproj") {
            projectors.push(row);
        } else {
            models.push(row);
        }
    }
    models.sort_by_key(|r| r["name"].as_str().unwrap_or("").to_lowercase());
    projectors.sort_by_key(|r| r["name"].as_str().unwrap_or("").to_lowercase());
    json!({"models":models,"projectors":projectors})
}
fn parse(bytes: &[u8]) -> Result<Value, ApiError> {
    serde_json::from_slice(bytes).map_err(|_| ApiError::invalid("請求不是有效 JSON。"))
}
struct DisconnectGuard(Arc<Ticket>, bool);
impl Drop for DisconnectGuard {
    fn drop(&mut self) {
        if self.1 && !self.0.done.is_cancelled() {
            self.0.cancel.cancel();
        }
    }
}
async fn completion(
    State(m): State<Arc<Manager>>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, ApiError> {
    if body.len() > MAX_BODY {
        return Err(ApiError::detail(413, "請求超過 32 MiB。"));
    }
    let headers = Value::Object(
        headers
            .iter()
            .filter_map(|(k, v)| v.to_str().ok().map(|v| (k.to_string(), json!(v))))
            .collect(),
    );
    let (ticket, rx) = m.submit(parse(&body)?, headers).await?;
    let mut guard = DisconnectGuard(ticket.clone(), true);
    let reply = rx
        .await
        .map_err(|_| ApiError::detail(502, "本地引擎連線失敗。"))?;
    let streaming = reply.stream.is_some();
    let body = if let Some(rx) = reply.stream {
        let stream = async_stream_body(None, rx, guard);
        Body::from_stream(stream)
    } else {
        guard.1 = false;
        drop(guard);
        Body::from(reply.body)
    };
    let mut response = Response::builder()
        .status(reply.code)
        .header("X-Conversation-Id", &ticket.id)
        .header("X-Manager-Request-Id", &ticket.id)
        .header(
            "Content-Type",
            if streaming {
                "text/event-stream; charset=utf-8"
            } else {
                "application/json"
            },
        );
    if streaming {
        response = response
            .header("Cache-Control", "no-cache")
            .header("X-Accel-Buffering", "no");
    }
    Ok(response.body(body).unwrap())
}
fn async_stream_body(
    first: Option<Bytes>,
    rx: mpsc::Receiver<Bytes>,
    guard: DisconnectGuard,
) -> impl futures_util::Stream<Item = Result<Bytes, std::io::Error>> {
    futures_util::stream::unfold((first, rx, guard), |(first, mut rx, guard)| async move {
        let next = if first.is_some() {
            first
        } else {
            rx.recv().await
        };
        next.map(|chunk| (Ok(chunk), (None, rx, guard)))
    })
}
pub fn router(manager: Arc<Manager>) -> Router {
    Router::new()
        .route(
            "/health",
            get(|| async {
                (
                    [("X-AMIEBL-Core-Protocol", "1")],
                    Json(json!({"ok":true,"app":"local-model-manager","version":"1.1.0-dev"})),
                )
            }),
        )
        .route("/manager/config", get(config).put(save))
        .route("/manager/export", get(config))
        .route("/manager/import", post(save))
        .route("/manager/status", get(status))
        .route("/manager/requests", get(records))
        .route("/manager/logs", get(logs))
        .route("/manager/records/clear", post(clear))
        .route("/manager/load", post(load))
        .route("/manager/unload", post(unload))
        .route("/manager/accepting", post(accepting))
        .route("/manager/keep-loaded", post(keep))
        .route("/manager/requests/{id}/cancel", post(cancel))
        .route("/manager/shutdown", post(shutdown))
        .route("/manager/connection", get(connection))
        .route("/manager/vscode/preview", get(vscode_preview))
        .route("/manager/vscode/apply", post(vscode_apply))
        .route("/manager/scan", post(scan))
        .route("/manager/model-capabilities", post(capabilities))
        .route("/manager/engines", get(engine_status))
        .route("/manager/engines/hardware", post(engine_hardware))
        .route("/manager/engines/policy", post(engine_policy))
        .route("/manager/engines/check", post(engine_check))
        .route("/manager/engines/install", post(engine_install))
        .route("/manager/engines/cancel", post(engine_cancel))
        .route("/manager/engines/activate", post(engine_activate))
        .route("/manager/engines/rollback", post(engine_rollback))
        .route("/manager/engines/remove", post(engine_remove))
        .route("/manager/engines/stop", post(engine_stop))
        .route("/manager/engines/runtime", get(engine_runtime))
        .route("/v1/models", get(models))
        .route("/v1/chat/completions", post(completion))
        .layer(DefaultBodyLimit::max(MAX_BODY + 1))
        .layer(middleware::from_fn_with_state(manager.clone(), gate))
        .with_state(manager)
}

async fn engine_status(State(m): State<Arc<Manager>>) -> ApiResult {
    Ok(Json(m.engines.status().await))
}
async fn engine_hardware(State(m): State<Arc<Manager>>) -> ApiResult {
    Ok(Json(m.engines.detect().await?))
}
async fn engine_policy(
    State(m): State<Arc<Manager>>,
    Json(policy): Json<crate::engines::Policy>,
) -> ApiResult {
    let _lifecycle = m.lifecycle.lock().await;
    let mut inner = m.inner.lock().await;
    let current = m.engines.status().await;
    if policy.mode != current["policy"]["mode"]
        && (!inner.tickets.is_empty()
            || inner.process.is_some()
            || matches!(inner.state, "loading" | "unloading"))
    {
        return Err(ApiError::detail(409, "請先卸載模型，再切換引擎來源。"));
    }
    let result = m.engines.policy(policy).await?;
    inner.managed_engine_dir = m.engines.active_directory().await?;
    drop(inner);
    drop(_lifecycle);
    let updater = m.clone();
    let task = tokio::spawn(async move {
        if let Err(e) = updater.engines.automatic_check().await {
            updater.log(&format!("引擎更新檢查：{e}")).await;
        }
    });
    m.background.lock().await.push(task);
    Ok(Json(result))
}
async fn engine_check(State(m): State<Arc<Manager>>) -> ApiResult {
    Ok(Json(m.engines.check().await?))
}
async fn engine_install(State(m): State<Arc<Manager>>, Json(body): Json<Value>) -> ApiResult {
    m.engines.install().await?;
    if body["activate_when_idle"] == true {
        m.engines.request_activation().await;
    }
    Ok(Json(m.engines.status().await))
}
async fn engine_cancel(State(m): State<Arc<Manager>>) -> ApiResult {
    Ok(Json(m.engines.cancel().await))
}
async fn engine_activate(State(m): State<Arc<Manager>>, Json(body): Json<Value>) -> ApiResult {
    m.activate_engine(
        body["id"]
            .as_str()
            .ok_or_else(|| "Missing package id".to_owned())?,
    )
    .await?;
    Ok(Json(m.engines.status().await))
}
async fn engine_rollback(State(m): State<Arc<Manager>>) -> ApiResult {
    let id = m.engines.previous().await?;
    m.activate_engine(&id).await?;
    Ok(Json(m.engines.status().await))
}
async fn engine_remove(State(m): State<Arc<Manager>>, Json(body): Json<Value>) -> ApiResult {
    let id = body["id"]
        .as_str()
        .ok_or_else(|| "Missing package id".to_owned())?;
    let _lifecycle = m.lifecycle.lock().await;
    let mut inner = m.inner.lock().await;
    let current = m.engines.status().await;
    if current["active"] == id
        && (!inner.tickets.is_empty()
            || inner.process.is_some()
            || matches!(inner.state, "loading" | "unloading"))
    {
        return Err(ApiError::detail(
            409,
            "引擎仍在運作；請先停止推理引擎，再移除此版本。",
        ));
    }
    let result = m.engines.remove(id).await?;
    inner.managed_engine_dir = m.engines.active_directory().await?;
    Ok(Json(result))
}

async fn engine_stop(State(m): State<Arc<Manager>>) -> ApiResult {
    let tickets = {
        let mut inner = m.inner.lock().await;
        inner.accepting = false;
        if let Some(cancel) = &inner.load_cancel {
            cancel.cancel();
        }
        inner.tickets.values().cloned().collect::<Vec<_>>()
    };
    for ticket in tickets {
        m.cancel(ticket).await;
    }
    m.unload().await;
    m.log("推理引擎已停止，已暫停接受新任務；重新使用前請恢復接受任務。")
        .await;
    Ok(Json(json!({"ok":true,"accepting":false})))
}

async fn engine_runtime(State(m): State<Arc<Manager>>) -> ApiResult {
    let (pid, exited, port, executable, version) = {
        let mut inner = m.inner.lock().await;
        let (pid, exited) = match inner.process.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(Some(exit)) => (None, Some(exit.to_string())),
                Ok(None) => (child.id(), None),
                Err(error) => (child.id(), Some(error.to_string())),
            },
            None => (None, None),
        };
        let mut config = inner.store.config().clone();
        if let Some(path) = &inner.managed_engine_dir {
            config["engine_dir"] = json!(path);
        }
        (
            pid,
            exited,
            m.engine_port
                .unwrap_or(config["engine_port"].as_u64().unwrap() as u16),
            engine_command(&config)?[0].clone(),
            inner.engine_version.clone(),
        )
    };
    let selected = m.engines.status().await;
    let package = selected["packages"]
        .as_array()
        .and_then(|packages| packages.iter().find(|p| p["id"] == selected["active"]));
    let installed = (selected["policy"]["mode"] != "managed" || package.is_some())
        && Path::new(&executable).is_file();
    let occupied = !free_port(port);
    let health = if pid.is_some() || occupied {
        match m
            .http
            .get(format!("http://127.0.0.1:{port}/health"))
            .timeout(Duration::from_millis(750))
            .send()
            .await
        {
            Ok(response) => Some(response.status().as_u16()),
            Err(_) => None,
        }
    } else {
        None
    };
    let state = if exited.is_some() {
        "exited"
    } else if pid.is_none() && occupied {
        if matches!(health, Some(200 | 503)) {
            "external"
        } else {
            "port_in_use"
        }
    } else if pid.is_some() {
        match health {
            Some(200) => "running",
            Some(503) => "starting",
            _ => "unresponsive",
        }
    } else if installed {
        "stopped"
    } else {
        "not_installed"
    };
    Ok(Json(
        json!({"state":state,"pid":pid,"owned":pid.is_some(),"health_http_status":health,
        "exit_detail":exited,"url":format!("http://127.0.0.1:{port}"),"port":port,
        "engine":"llama.cpp","version":package.map(|p|p["version"].clone()).unwrap_or(version),
        "backend":package.map(|p|p["backend"].clone()),"build_tag":package.map(|p|p["build_tag"].clone())}),
    ))
}
