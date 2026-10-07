# Local Model Manager 1.0 implementation contract

Windows personal desktop manager. Native .NET 10 WPF WinExe + Python FastAPI local backend. Existing llama.cpp remains external. Runtime state defaults to `%LOCALAPPDATA%/LocalModelManager`; CLI `--data-dir` isolates tests. Backend default port 8080, engine 8081, loopback only. Desktop launches backend hidden using Python, uses a per-install generated admin token (`admin-token` in data dir) sent as `X-Manager-Token`. `/health` and inference endpoints are public loopback; all `/manager/*` require the token. Read/write configuration uses complete JSON objects preserving unknown fields.

## Configuration GET/PUT /manager/config
`{schema_version:1, model_dirs:["D:\\model"], engine_dir:"C:\\Users\\kissi\\llama.cpp", api_port:8080, engine_port:8081, default_model_id:"qwen3.8-27b-local", default_profile_id:"coding", idle_minutes:15, auto_start:false, start_hidden:false, close_to_tray:true, preload:false, log_request_bodies:false, log_retention_days:7, models:[...], profiles:[...]}`

Model: `{id,name,path,mmproj:"",vision:false,context:65536,gpu_layers:17,auto_fit:true,fit_target_mib:2048,cache_type:"q4_0",mtp:true,mtp_draft_max:null,keep_loaded:false,idle_minutes:null,default_profile_id:"coding",temperature:null,top_p:null,top_k:null,min_p:null,reasoning_supported:true,reasoning_efforts:["low","medium","xhigh"]}`. Model config applies on next load. Scan returns main models and projector paths without loading them. Only default Qwen proven model has MTP/known reasoning support enabled; new models conservative defaults, editable capabilities.

Profile: `{id,name,thinking_mode:"auto"|"on"|"off"|"model",effort:"low"|"medium"|"xhigh",thinking_budget:512,max_tokens:4096}`. Defaults quick-chat low/512/4096; coding medium/1536/8192; deep-coding xhigh/4096/12288; all auto. Total generation cap includes reasoning; profile enforcement respects stricter explicit client output cap and reserves answer space by clamping reasoning. Explicit reasoning client controls respected. Model context >= request generation limit validation. Public model aliases `model-id::profile-id`, physical model loaded once. Base legacy alias remains usable with marker fallback for existing .agent.md. New clients select explicit alias or X-LLM-Profile.

## API
- GET /health: `{ok:true,app:"local-model-manager",version:"1.0.0"}`.
- GET /manager/status: `{state:"unloaded"|"loading"|"ready"|"unloading"|"error", model_id:null, model_name:null, pid:null, accepting:true, active_count:0, queued_count:0, uptime_seconds:0, idle_seconds:0, unload_in_seconds:null, last_error:null, api_url:"http://127.0.0.1:8080/v1/chat/completions", engine_version:"...", slots:[], resources:{ram_used_gb:null,ram_total_gb:null,gpu_used_mib:null,gpu_total_mib:null},pending_config:false}`.
- GET /manager/requests: `{requests:[...]}` newest first, active + bounded persisted history. Request `{id,model_id,model_name,profile_id,profile_name,phase:"queued"|"loading"|"classifying"|"prompt"|"thinking"|"generating"|"completed"|"cancelled"|"error",started_at,finished_at:null,elapsed_seconds,first_token_seconds:null,classifier_seconds:null,prompt_tokens:null,cached_tokens:null,generated_tokens:null,thinking_tokens:null,prompt_tps:null,generation_tps:null,prompt_progress:null,effort:null,thinking_budget:null,max_tokens:null,decision:null,error:null,cancel_confirmed:false}`. UTC timestamps ISO strings. No prompt/response bodies in summaries.
- GET /manager/logs: `{lines:[string]}` sanitized bounded log tail.
- POST /manager/scan `{}`: `{models:[{path,name,size_bytes}],projectors:[{path,name,size_bytes}]}` uses configured model dirs, detects split shards and excludes mmproj from main list.
- POST /manager/load `{model_id}`: nonblocking; returns `{ok:true}` or structured error. GET status for progress.
- POST /manager/unload `{}`: if requests exist defer until idle, returns `{ok:true,deferred:true|false}`.
- POST /manager/accepting `{accepting:true|false}`.
- POST /manager/keep-loaded `{model_id,keep_loaded:true|false}` updates config and live policy.
- POST /manager/requests/{id}/cancel `{}`.
- POST /manager/shutdown `{}` graceful cancel/unload/server exit (desktop owns process too).
- GET /manager/connection: `{ok:true,api_url,models:[...],engine_exists:true,python_ok:true,port_status:"..."}` non-inference checks, no auto wake.
- GET /manager/export: full config export.
- POST /manager/import: full config object, same validation as PUT config.
- GET /v1/models: OpenAI list, available aliases, no model load.
- POST /v1/chat/completions: streaming + nonstream, demand loading, bounded queue single slot, cancellation throughout queued/load/classification/prefill/streaming. Preserve tool calls/vision/reasoning and original compatibility cancellation where possible. Do not duplicate inference on transport retry.

## Desktop behavior
Main window 5 pages Traditional Chinese: 總覽, 模型庫 (model detail editor), 使用模式, 任務與紀錄, 系統. Modern restrained light/dark-neutral native WPF theme, not unstyled defaults. Async polling only; never block UI on model load. Folder/file pickers; profile create/duplicate/delete; model add from scan and independent settings. System saves startup/close/preload etc. Autostart registry HKCU Run desktop implementation using executable path with --background. Off by default, do not auto-enable. Single instance with named mutex + signal event to show existing window. Tray icon and menu show/load/unload/keep/pause/exit; X hides if configured. Process CreateNoWindow, no console. Closing GUI can leave native app/tray/backend alive; full exit stops owned child processes. No arbitrary killing on port conflict. Data dir config persisted atomically with backup; UI import/export dialogs. Show errors, never silent failure or fake metrics. Desktop can expose --self-test and --smoke-test to validate startup/UI programmatically without changing live user config.
