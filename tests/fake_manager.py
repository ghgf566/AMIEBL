"""Standard-library HTTP fixture for desktop smoke/render checks ONLY.

This is not the production backend and cannot load or run a model. All numeric
telemetry is illustrative and clearly labelled. An explicit isolated --data-dir
is required; no startup registration or VS Code configuration is modified.
Set LMM_FIXTURE_SCENE=busy|error|idle to choose the initial display state.
"""
from __future__ import annotations

import argparse
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit


def initial_config(port):
    return {
        "schema_version": 1, "model_dirs": ["D:\\model"],
        "engine_dir": "C:\\Users\\kissi\\llama.cpp", "api_port": port,
        "engine_port": port + 1, "default_model_id": "fixture-qwen",
        "default_profile_id": "coding", "idle_minutes": 15,
        "auto_start": False, "start_hidden": False, "close_to_tray": True,
        "preload": False, "log_request_bodies": False, "log_retention_days": 7,
        "models": [{"id": "fixture-qwen", "name": "Qwen3.8 27B · 模擬展示",
                    "path": "D:\\model\\Qwen3.8-27B-UD-IQ4_XS.gguf",
                    "mmproj": "D:\\model\\mmproj-Qwen3.8.gguf", "vision": True,
                    "context": 65536, "gpu_layers": 17, "auto_fit": True,
                    "fit_target_mib": 2048, "cache_type": "q4_0", "mtp": True, "mtp_draft_max": None,
                    "keep_loaded": False, "idle_minutes": None, "default_profile_id": "coding",
                    "temperature": None, "top_p": None, "top_k": None, "min_p": None,
                    "reasoning_supported": True, "reasoning_efforts": ["low", "medium", "xhigh"]}],
        "profiles": [{"id": ident, "name": name, "thinking_mode": "auto", "effort": effort,
                      "thinking_budget": budget, "max_tokens": cap}
                     for ident, name, effort, budget, cap in [
                         ("quick-chat", "Quick Chat", "low", 512, 4096),
                         ("coding", "Coding", "medium", 1536, 8192),
                         ("deep-coding", "Deep Coding", "xhigh", 4096, 12288)]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--engine-port", type=int)
    args = parser.parse_args()
    data = Path(args.data_dir).resolve()
    if data.name == "LocalModelManager" and data.parent.name == "Local":
        parser.error("Refusing the normal application data directory; use an isolated test directory.")
    data.mkdir(parents=True, exist_ok=True)
    path = data / "config.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else initial_config(args.port)
    config["api_port"] = args.port
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    token_path = data / "admin-token"
    token = token_path.read_text().strip() if token_path.exists() else secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    (data / "FIXTURE-ONLY.txt").write_text("Mock HTTP backend for desktop validation. No real inference.", encoding="utf-8")
    scene = os.environ.get("LMM_FIXTURE_SCENE", "busy")
    state = {"loaded": scene == "busy", "accepting": True, "scene": scene}
    started = time.monotonic()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    requests = [{"id": "fixture-active", "model_id": config["default_model_id"],
                 "model_name": "Qwen3.8 27B · 模擬展示", "profile_id": "coding", "profile_name": "Coding",
                 "phase": "generating", "started_at": now, "finished_at": None, "elapsed_seconds": 12.8,
                 "first_token_seconds": 3.4, "classifier_seconds": 0.2, "prompt_tokens": 6240,
                 "cached_tokens": 4096, "generated_tokens": 286, "thinking_tokens": 96,
                 "prompt_tps": 1280.0, "generation_tps": 28.6, "prompt_progress": 1.0,
                 "effort": "medium", "thinking_budget": 1536, "max_tokens": 8192,
                 "decision": "模擬展示：需要多步程式分析", "error": None, "cancel_confirmed": False}]
    if scene != "busy":
        requests[0].update(phase="error" if scene == "error" else "completed", finished_at=now,
                           error="模擬展示：模型檔案已移動，請重新選擇檔案。" if scene == "error" else None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            if not secrets.compare_digest(self.headers.get("X-Manager-Token", ""), token):
                self.reply({"detail": "Invalid fixture token"}, 401)
                return False
            return True

        def body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/health":
                self.reply({"ok": True, "app": "local-model-manager", "version": "1.0.0-fixture", "fixture": True})
                return
            if not self.allowed():
                return
            active = [r for r in requests if r["phase"] == "generating"]
            values = {
                "/manager/config": config, "/manager/export": config,
                "/manager/status": {"state": "error" if state["scene"] == "error" else "ready" if state["loaded"] else "unloaded",
                                    "model_id": config["default_model_id"] if state["loaded"] else None,
                                    "model_name": "Qwen3.8 27B · 模擬展示" if state["loaded"] else None,
                                    "pid": None, "accepting": state["accepting"], "active_count": len(active),
                                    "queued_count": 0, "uptime_seconds": time.monotonic() - started, "idle_seconds": 0,
                                    "unload_in_seconds": None, "last_error": requests[0]["error"] if state["scene"] == "error" else None,
                                    "api_url": f"http://127.0.0.1:{args.port}/v1/chat/completions",
                                    "engine_version": "模擬展示（未載入真實模型）", "slots": [{"id": 0, "is_processing": bool(active)}] if state["loaded"] else [],
                                    "resources": {"ram_used_gb": 23.4, "ram_total_gb": 64, "gpu_used_mib": 7812, "gpu_total_mib": 12288},
                                    "pending_config": False, "fixture": True},
                "/manager/requests": {"requests": requests},
                "/manager/logs": {"lines": ["[模擬展示] 桌面 HTTP 與畫面測試；數值並非真實模型測量。", "[模擬展示] 收到請求 → 處理輸入 → 生成中。"]},
                "/manager/connection": {"ok": True, "api_url": f"http://127.0.0.1:{args.port}/v1/chat/completions", "models": [],
                                        "engine_exists": False, "python_ok": True, "port_status": "模擬展示：連線正常"},
                "/manager/vscode/preview": {"ok": True, "model_count": 4, "message": "模擬展示：不會更改 VS Code", "models_file": "fixture", "agents_dir": "fixture"},
            }
            self.reply(values.get(route, {"detail": "Unknown fixture route"}), 200 if route in values else 404)

        def do_PUT(self):
            self.do_POST()

        def do_POST(self):
            nonlocal config
            if not self.allowed():
                return
            route = urlsplit(self.path).path
            try:
                body = self.body()
            except ValueError:
                self.reply({"detail": "Invalid JSON"}, 400)
                return
            if route in ("/manager/config", "/manager/import"):
                config = body
                path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                self.reply(config)
            elif route == "/manager/scan":
                self.reply({"models": [{"path": config["models"][0]["path"], "name": "模擬展示模型", "size_bytes": 15000000000}],
                            "projectors": [{"path": config["models"][0]["mmproj"], "name": "模擬展示視覺模組", "size_bytes": 800000000}]})
            elif route == "/manager/accepting":
                state["accepting"] = body.get("accepting", True)
                self.reply({"ok": True})
            elif route == "/manager/load":
                state.update(loaded=True, scene="idle")
                self.reply({"ok": True})
            elif route == "/manager/unload":
                state.update(loaded=False, scene="idle")
                self.reply({"ok": True, "deferred": False})
            elif route == "/manager/keep-loaded":
                for model in config["models"]:
                    if model["id"] == body.get("model_id"):
                        model["keep_loaded"] = body.get("keep_loaded", False)
                self.reply({"ok": True})
            elif route.endswith("/cancel"):
                for request in requests:
                    request.update(phase="cancelled", finished_at=now, cancel_confirmed=True)
                self.reply({"ok": True})
            elif route == "/manager/shutdown":
                self.reply({"ok": True})
                threading.Thread(target=server.shutdown, daemon=True).start()
            else:
                self.reply({"detail": "Fixture only: this action is not implemented and performs no real work."}, 501)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever(poll_interval=0.1)
    server.server_close()


if __name__ == "__main__":
    main()
