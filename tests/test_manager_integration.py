"""HTTP/subprocess integration tests. Never loads the user's real model.

Run: python -m unittest discover -s tests -p test_manager_integration.py -v
Requirements are the manager's existing Python dependencies (httpx/FastAPI).
All mutable state lives under tests/.runs and is unique to each test.
"""

from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).with_name("fake_engine.py")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (httpx.HTTPError, FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.04)
    raise AssertionError(f"Timed out after {timeout}s; last result: {last!r}")


class ManagerIntegration(unittest.TestCase):
    def setUp(self):
        # Keep mutable integration-test state out of the repository. Repos are
        # commonly stored under OneDrive on Windows, whose sync/index hooks can
        # transiently lock files during atomic replace operations.
        self._run_tmp = tempfile.TemporaryDirectory(prefix="amiebl-" + self._testMethodName + "-")
        self.addCleanup(self._run_tmp.cleanup)
        self.run_dir = Path(self._run_tmp.name)
        self.data = self.run_dir / "data"
        self.data.mkdir()
        self.model_dir = self.run_dir / "models"
        self.model_dir.mkdir()
        self.model = self.model_dir / "tiny-test.gguf"
        self.model.write_bytes(b"GGUFfake-test-only")
        self.port = free_port()
        self.engine_port = free_port()
        while self.engine_port == self.port:
            self.engine_port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.engine_base = f"http://127.0.0.1:{self.engine_port}"
        self.events_file = self.run_dir / "events.jsonl"
        self.config = {
            "schema_version": 1,
            "model_dirs": [str(self.model_dir)], "engine_dir": str(self.run_dir),
            "api_port": self.port, "engine_port": self.engine_port,
            "default_model_id": "test-model", "default_profile_id": "coding",
            "idle_minutes": 15, "auto_start": False, "start_hidden": False,
            "close_to_tray": True, "preload": False, "log_request_bodies": False,
            "log_retention_days": 7,
            "models": [{"id": "test-model", "name": "Test Model", "path": str(self.model),
                        "mmproj": "", "vision": False, "context": 8192, "gpu_layers": 0,
                        "auto_fit": False, "fit_target_mib": 2048, "cache_type": "f16", "mtp": False,
                        "keep_loaded": False, "idle_minutes": None, "default_profile_id": "coding",
                        "temperature": 0.8, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
                        "reasoning_supported": True, "reasoning_efforts": ["low", "medium", "xhigh"]}],
            "profiles": [{"id": "coding", "name": "Coding", "thinking_mode": "off", "effort": "medium", "thinking_budget": 1536, "max_tokens": 4096},
                         {"id": "quick-chat", "name": "Quick Chat", "thinking_mode": "off", "effort": "low", "thinking_budget": 512, "max_tokens": 2048}]
        }
        (self.data / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update({"LMM_ENGINE_COMMAND_JSON": json.dumps([sys.executable, str(FAKE)]),
                         "LMM_SKIP_FIT": "1", "LMM_FAKE_EVENTS": str(self.events_file), "PYTHONUNBUFFERED": "1",
                         "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                         "LMM_FAKE_READY_DELAY_FILE": str(self.run_dir / "ready-delay")})
        self.log = (self.run_dir / "manager.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "manager.py"), "--data-dir", str(self.data),
                                     "--port", str(self.port), "--engine-port", str(self.engine_port)],
                                    env=self.env, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.client = httpx.Client(base_url=self.base, timeout=25, trust_env=False)
        try:
            def manager_ready():
                if self.proc.poll() is not None:
                    raise RuntimeError(f"manager exited during startup with code {self.proc.returncode}")
                return self.client.get("/health").status_code == 200
            eventually(manager_ready, 20)
            self.token = eventually(lambda: (self.data / "admin-token").read_text(encoding="utf-8").strip())
            self.headers = {"X-Manager-Token": self.token}
        except Exception:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            self.log.close()
            print((self.run_dir / "manager.log").read_text(encoding="utf-8", errors="replace"))
            raise

    def tearDown(self):
        if self.proc.poll() is None:
            try:
                self.client.post("/manager/shutdown", headers=self.headers, json={})
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        self.client.close()
        self.log.close()

    def api(self, method, path, **kwargs):
        return self.client.request(method, path, headers=self.headers, **kwargs)

    def get(self, path):
        r = self.api("GET", path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def events(self, kind=None):
        if not self.events_file.exists():
            return []
        lines = self.events_file.read_text(encoding="utf-8").splitlines()
        events = [json.loads(x) for x in lines if x]
        return [x for x in events if x["kind"] == kind] if kind else events

    def completion(self, text="plain task", stream=False, **extra):
        return self.client.post("/v1/chat/completions", json={"model": "test-model", "messages": [{"role": "user", "content": text}], "stream": stream, **extra})

    def save(self, **changes):
        config = self.get("/manager/config")
        config.update(changes)
        response = self.api("PUT", "/manager/config", json=config)
        if response.status_code != 200:
            self.log.flush()
            try:
                diagnostic = (self.run_dir / "manager.log").read_text(encoding="utf-8", errors="replace")[-6000:]
            except OSError as exc:
                diagnostic = f"<unable to read manager.log: {exc}>"
            self.fail(f"{response.status_code} != 200: {response.text}\nmanager.log tail:\n{diagnostic}")
        return config

    def records(self):
        return self.get("/manager/requests")["requests"]

    def load(self):
        r = self.api("POST", "/manager/load", json={"model_id": "test-model"})
        self.assertEqual(r.status_code, 200, r.text)
        eventually(lambda: self.get("/manager/status")["state"] == "ready")

    def control(self, **changes):
        with httpx.Client(trust_env=False) as client:
            response = client.post(self.engine_base + "/__control", json=changes)
            self.assertEqual(response.status_code, 200, response.text)

    def auto_profile(self, **changes):
        profiles = copy.deepcopy(self.get("/manager/config")["profiles"])
        profiles[0].update(thinking_mode="auto", **changes)
        self.save(profiles=profiles)

    def test_legacy_reasoning_profile_is_migrated_to_model_agnostic_schema(self):
        config = self.get("/manager/config")
        coding = next(x for x in config["profiles"] if x["id"] == "coding")
        quick = next(x for x in config["profiles"] if x["id"] == "quick-chat")
        self.assertEqual(coding["reasoning_level"], "balanced")
        self.assertEqual(quick["reasoning_level"], "light")
        self.assertEqual(coding["budget_mode"], "custom")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        self.assertEqual(model["reasoning_capability"], "toggle")
        self.assertEqual(model["reasoning_toggle_keys"], ["enable_thinking"])
    def test_legacy_fit_target_is_preserved_as_explicit_override(self):
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        self.assertTrue(model["fit_target_enabled"])
        self.assertEqual(model["fit_target_mib"], 2048)
    def test_observation_and_scan_do_not_load_model(self):
        for _ in range(2):
            self.assertEqual(self.client.get("/health").status_code, 200)
            models = self.client.get("/v1/models").json()["data"]
            ids = {x["id"] for x in models}
            self.assertIn("test-model", ids)
            self.assertIn("test-model::coding", ids)
            self.assertEqual(self.get("/manager/status")["state"], "unloaded")
            self.get("/manager/connection")
            self.get("/manager/requests")
            self.get("/manager/logs")
            r = self.api("POST", "/manager/scan", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(any(x["path"] == str(self.model) for x in r.json()["models"]))
        self.assertEqual(self.events("start"), [])

    def test_nonstream_preserves_tools_reasoning_and_client_message(self):
        body = {"model": "test-model::coding", "messages": [{"role": "user", "content": [{"type": "text", "text": "inspect"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,TEST"}}]}],
                "tools": [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}], "tool_choice": "auto", "stream": False}
        r = self.client.post("/v1/chat/completions", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        message = r.json()["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "fake reasoning")
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], '{"value":7}')
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["messages"], body["messages"])
        self.assertEqual(forwarded["tools"], body["tools"])
        self.assertEqual(forwarded["tool_choice"], "auto")
        self.assertEqual(forwarded["model"], "test-model")
        self.assertEqual(len(self.events("start")), 1)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["generated_tokens"], 8)
        self.assertEqual(rec["prompt_tokens"], 12)

    def test_stream_preserves_sse_and_usage(self):
        r = self.completion(stream=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/event-stream", r.headers["content-type"])
        frames = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ") and x != "data: [DONE]"]
        self.assertTrue(any(x.get("choices", [{}])[0].get("delta", {}).get("reasoning_content") for x in frames))
        self.assertTrue(any(x.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for x in frames))
        self.assertEqual(frames[-1]["usage"]["completion_tokens"], 8)
        self.assertIn("data: [DONE]", r.text)

    def test_fragmented_sse_preserves_unicode_and_tool_argument_fragments(self):
        response = self.completion("FAKE_FRAGMENTED", stream=True)
        self.assertEqual(response.status_code, 200, response.text)
        frames = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        deltas = [frame["choices"][0]["delta"] for frame in frames if frame.get("choices")]
        self.assertEqual("".join(d.get("reasoning_content", "") for d in deltas), "分析中")
        self.assertEqual("".join(d.get("content", "") for d in deltas), "答案")
        args = "".join(call.get("function", {}).get("arguments", "")
                       for delta in deltas for call in delta.get("tool_calls", []))
        self.assertEqual(json.loads(args), {"value": "中文"})
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["generated_tokens"], 8)
        self.assertEqual(rec["generation_tps"], 20)

    def test_concurrent_demand_starts_once_and_queues(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(self.completion, f"task {n}", True) for n in range(4)]
            eventually(lambda: self.get("/manager/status")["queued_count"] >= 1)
            responses = [j.result(timeout=20) for j in jobs]
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(len(self.events("start")), 1)
        self.assertEqual(len([e for e in self.events("post") if e["path"] == "/v1/chat/completions"]), 4)
        self.assertEqual(self.get("/manager/status")["active_count"], 0)

    def test_output_cap_honors_stricter_client_limit(self):
        r = self.completion(max_tokens=200)
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["max_tokens"], 200)
        r = self.completion(max_tokens=10000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 4096)

    def test_completion_cap_alias_is_respected(self):
        response = self.completion(max_completion_tokens=180)
        self.assertEqual(response.status_code, 200, response.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertLessEqual(forwarded.get("max_tokens", forwarded.get("max_completion_tokens")), 180)


    def test_auto_classifier_is_local_and_can_disable_thinking(self):
        self.load()
        self.auto_profile()
        response = self.completion(
            "你好",
            tools=[{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [], "Auto routing must not call the model a second time")
        self.assertEqual(len(self.events("post")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertFalse(forwarded["chat_template_kwargs"]["enable_thinking"])
        self.assertTrue(forwarded["tools"])
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：直接回答")
        self.assertIsNotNone(rec["classifier_seconds"])
        self.assertLess(rec["classifier_seconds"], 0.1)


    def test_auto_classifier_uses_profile_ceiling_for_complex_tasks(self):
        self.load()
        self.auto_profile(thinking_budget=512)
        response = self.completion("分析這個 Python race condition 為什麼會造成 deadlock", max_tokens=300)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [])
        self.assertEqual(len(self.events("post")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：需要思考")
        self.assertEqual(rec["max_tokens"], 300)
        self.assertGreaterEqual(rec["thinking_budget"], 0)
        self.assertLess(rec["thinking_budget"], 300, "Final answer must retain generation space")
        self.assertLessEqual(rec["thinking_budget"], 512)

    def test_nonreasoning_model_does_not_run_auto_classifier(self):
        models = copy.deepcopy(self.config["models"])
        models[0].update(reasoning_supported=False, reasoning_efforts=[])
        self.save(models=models)
        self.auto_profile()
        response = self.completion("analyze task")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [])
        self.assertEqual(len(self.events("post")), 1)

    def test_abstract_deep_profile_maps_to_native_effort(self):
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(
            reasoning_capability="toggle",
            reasoning_efforts=["low", "medium", "xhigh"],
            reasoning_default_effort="xhigh",
            reasoning_budget_supported=True,
            reasoning_toggle_keys=["enable_thinking"],
            reasoning_detection="legacy",
        )
        profile = next(x for x in config["profiles"] if x["id"] == "coding")
        profile.update(thinking_mode="on", reasoning_level="deep", budget_mode="auto")
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        r = self.completion()
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["reasoning_effort"], "xhigh")
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])
        self.assertNotIn("thinking_budget_tokens", forwarded)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["reasoning_level"], "deep")
        self.assertEqual(rec["effort"], "xhigh")

    def test_custom_budget_is_forwarded_with_native_effort(self):
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(
            reasoning_capability="toggle",
            reasoning_efforts=["low", "medium", "xhigh"],
            reasoning_default_effort="xhigh",
            reasoning_budget_supported=False,
            reasoning_toggle_keys=["enable_thinking"],
            reasoning_detection="legacy",
        )
        profile = next(x for x in config["profiles"] if x["id"] == "coding")
        profile.update(
            thinking_mode="on",
            reasoning_level="balanced",
            budget_mode="custom",
            thinking_budget=777,
            max_tokens=4096,
        )
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        r = self.completion()
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["reasoning_effort"], "medium")
        self.assertEqual(forwarded["thinking_budget_tokens"], 777)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["thinking_budget"], 777)

    def test_abstract_deep_profile_uses_budget_when_native_effort_is_absent(self):
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(
            reasoning_capability="toggle",
            reasoning_efforts=[],
            reasoning_default_effort="",
            reasoning_budget_supported=True,
            reasoning_toggle_keys=["enable_thinking"],
            reasoning_detection="legacy",
        )
        profile = next(x for x in config["profiles"] if x["id"] == "coding")
        profile.update(thinking_mode="on", reasoning_level="deep", budget_mode="auto", max_tokens=4096)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        r = self.completion()
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertNotIn("reasoning_effort", forwarded)
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])
        self.assertGreater(forwarded["thinking_budget_tokens"], 0)
        self.assertLess(forwarded["thinking_budget_tokens"], 4096)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["reasoning_level"], "deep")
        self.assertIsNone(rec["effort"])
        self.assertGreater(rec["thinking_budget"], 0)
    def test_custom_budget_survives_unknown_reasoning_capability(self):
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(
            reasoning_capability="unknown",
            reasoning_efforts=[],
            reasoning_default_effort="",
            reasoning_budget_supported=False,
            reasoning_toggle_keys=[],
            reasoning_detection="legacy",
        )
        profile = next(x for x in config["profiles"] if x["id"] == "coding")
        profile.update(thinking_mode="on", reasoning_level="balanced", budget_mode="custom", thinking_budget=321)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        r = self.completion()
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["thinking_budget_tokens"], 321)
        self.assertNotIn("reasoning_effort", forwarded)
        self.assertNotIn("chat_template_kwargs", forwarded)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["thinking_budget"], 321)
        self.assertIn("尚未確認", rec["decision"])
    def test_client_reasoning_override_skips_classifier(self):
        profiles = copy.deepcopy(self.get("/manager/config")["profiles"])
        profiles[0]["thinking_mode"] = "auto"
        self.save(profiles=profiles)
        r = self.completion(reasoning_effort="low", chat_template_kwargs={"enable_thinking": True, "thinking_budget": 100})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("classifier"), [])
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["reasoning_effort"], "low")
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])

    def test_explicit_profile_alias_and_header_select_request_policy(self):
        r = self.completion(model="test-model::quick-chat", max_tokens=8000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 2048)
        r = self.client.post("/v1/chat/completions", headers={"X-LLM-Profile": "quick-chat"},
                             json={"model": "test-model", "messages": [{"role": "user", "content": "header mode"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 2048)
        r = self.client.post("/v1/chat/completions",
                             json={"model": "test-model", "messages": [
                                 {"role": "system", "content": "AMIEBL_PROFILE:quick-chat\nAgent instructions."},
                                 {"role": "user", "content": "marker mode"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 2048)
        self.assertEqual({x["profile_id"] for x in self.records()}, {"quick-chat"})

    def test_vscode_preserve_mode_keeps_manual_tools_and_prompt(self):
        spec = importlib.util.spec_from_file_location("vscode_integration_test", ROOT / "backend" / "vscode_integration.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        source = "---\nname: Custom Coding\ntools: [read]\ncustom-field: keep-me\n---\n\nAMIEBL_PROFILE:old-profile\n\nMy hand edited prompt.\n"
        result = module.preserve_agent_profile_marker(source, "coding")
        self.assertIn("tools: [read]", result)
        self.assertIn("custom-field: keep-me", result)
        self.assertIn("My hand edited prompt.", result)
        self.assertIn("AMIEBL_PROFILE:coding", result)
        self.assertNotIn("AMIEBL_PROFILE:old-profile", result)
    def test_vscode_preview_exposes_physical_models_and_profile_agents(self):
        r = self.api("GET", "/manager/vscode/preview")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["model_count"], 1)
        self.assertEqual(body["agent_count"], 2)
        self.assertTrue(all("agent_name" in p for p in body["profiles"]))
    def test_external_mtp_draft_and_cpu_thread_limit_reach_engine_args(self):
        draft = self.run_dir / "mtp-draft.gguf"
        draft.write_bytes(b"GGUF")
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model["cpu_threads"] = 3
        model["mtp"] = True
        model["mtp_source"] = "external"
        model["mtp_draft_path"] = str(draft)
        model["mtp_draft_max"] = 2
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.load()
        argv = self.events("start")[-1]["argv"]
        self.assertIn("--spec-type", argv)
        self.assertIn("draft-mtp", argv)
        self.assertIn("--spec-draft-model", argv)
        self.assertIn(str(draft), argv)
        self.assertEqual(argv[argv.index("-t") + 1], "3")
        self.assertEqual(argv[argv.index("-tb") + 1], "3")
    def test_unknown_model_is_rejected_without_loading(self):
        r = self.completion(model="not-a-real-model")
        self.assertGreaterEqual(r.status_code, 400, r.text)
        self.assertEqual(self.events("start"), [])


    def test_auto_classifier_ambiguous_request_uses_profile_default(self):
        self.load()
        self.auto_profile()
        response = self.completion("介紹一下章魚")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [])
        self.assertEqual(len(self.events("post")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：採用模式預設")
        self.assertEqual(rec["effort"], "medium")
        self.assertEqual(rec["thinking_budget"], 1536)

    def test_cancel_queued_request_never_reaches_engine(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(self.completion, "FAKE_SLOW first", True)
            first = eventually(lambda: next((x for x in self.records() if x["phase"] in ("prompt", "thinking", "generating")), None))
            queued = pool.submit(self.completion, "queued second")
            second = eventually(lambda: next((x for x in self.records() if x["id"] != first["id"] and x["phase"] == "queued"), None))
            r = self.api("POST", f"/manager/requests/{second['id']}/cancel", json={})
            self.assertEqual(r.status_code, 200, r.text)
            queued.result(timeout=8)
            self.api("POST", f"/manager/requests/{first['id']}/cancel", json={})
            active.result(timeout=8)
        cancelled = next(x for x in self.records() if x["id"] == second["id"])
        self.assertEqual(cancelled["phase"], "cancelled")
        self.assertTrue(cancelled["cancel_confirmed"])
        self.assertEqual(len(self.events("post")), 1)

    def test_cancel_before_response_headers(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.completion, "FAKE_PREHEADER task")
            rec = eventually(lambda: next(iter(self.records()), None))
            eventually(lambda: bool(self.events("post")))
            r = self.api("POST", f"/manager/requests/{rec['id']}/cancel", json={})
            self.assertEqual(r.status_code, 200, r.text)
            job.result(timeout=8)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "cancelled"), None))
        self.assertTrue(rec["cancel_confirmed"])
        eventually(lambda: self.get("/manager/status")["active_count"] == 0)

    def test_cancel_during_model_loading(self):
        (self.run_dir / "ready-delay").write_text("5", encoding="utf-8")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.completion, "cancel during loading")
            rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "loading"), None))
            r = self.api("POST", f"/manager/requests/{rec['id']}/cancel", json={})
            self.assertEqual(r.status_code, 200, r.text)
            job.result(timeout=3)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "cancelled"), None))
        self.assertTrue(rec["cancel_confirmed"])
        self.assertEqual(self.events("post"), [])

    def test_cancel_active_stream(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.completion, "FAKE_SLOW task", True)
            rec = eventually(lambda: next((x for x in self.records() if x["phase"] in ("thinking", "generating")), None))
            r = self.api("POST", f"/manager/requests/{rec['id']}/cancel", json={})
            self.assertEqual(r.status_code, 200, r.text)
            job.result(timeout=8)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "cancelled"), None))
        self.assertTrue(rec["cancel_confirmed"])


    def test_auto_classifier_ignores_agent_system_keywords(self):
        self.load()
        self.auto_profile()
        body = {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "Always perform debugging, planning, architecture analysis and root cause investigation."},
                {"role": "user", "content": "hello"},
            ],
            "stream": False,
        }
        response = self.client.post("/v1/chat/completions", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [])
        self.assertEqual(len(self.events("post")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertFalse(forwarded["chat_template_kwargs"]["enable_thinking"])
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：直接回答")

    def test_client_disconnection_cancels_active_stream(self):
        with self.client.stream("POST", "/v1/chat/completions", json={"model": "test-model", "messages": [{"role": "user", "content": "FAKE_SLOW disconnected"}], "stream": True}) as response:
            self.assertEqual(response.status_code, 200)
            iterator = response.iter_lines()
            self.assertTrue(next(iterator).startswith("data:"))
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "cancelled"), None))
        self.assertTrue(rec["cancel_confirmed"])
        eventually(lambda: self.get("/manager/status")["active_count"] == 0)

    def test_client_disconnection_before_headers_cancels_engine(self):
        body = json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "FAKE_PREHEADER disconnected"}], "stream": False}).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
            request = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
            sock.sendall(request)
            eventually(lambda: bool(self.events("post")))
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "cancelled"), None), 6)
        self.assertTrue(rec["cancel_confirmed"])
        eventually(lambda: self.get("/manager/status")["active_count"] == 0)

    def test_zero_idle_timeout_disables_auto_unload(self):
        self.save(idle_minutes=0)
        self.load()
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            self.assertEqual(self.get("/manager/status")["state"], "ready")
            time.sleep(0.1)

    def test_model_zero_idle_timeout_overrides_global_unload(self):
        config = self.get("/manager/config")
        config["idle_minutes"] = 0.01
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model["idle_minutes"] = 0
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.load()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.assertEqual(self.get("/manager/status")["state"], "ready")
            time.sleep(0.1)
    def test_idle_unloads_and_observations_do_not_reset_timer(self):
        self.save(idle_minutes=0.025)
        self.load()
        eventually(lambda: self.get("/manager/status")["state"] == "unloaded", timeout=8)
        self.assertEqual(len(self.events("start")), 1)

    def test_unload_during_work_defers_until_request_finishes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.completion, "FAKE_SLOW deferred unload", True)
            rec = eventually(lambda: next((x for x in self.records() if x["phase"] in ("thinking", "generating")), None))
            response = self.api("POST", "/manager/unload", json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["deferred"])
            self.assertNotEqual(self.get("/manager/status")["state"], "unloaded")
            self.assertFalse(job.done())
            self.api("POST", f"/manager/requests/{rec['id']}/cancel", json={})
            job.result(timeout=8)
        eventually(lambda: self.get("/manager/status")["state"] == "unloaded")

    def test_keep_loaded_overrides_idle_timeout_until_released(self):
        self.save(idle_minutes=0.015)
        response = self.api("POST", "/manager/keep-loaded", json={"model_id": "test-model", "keep_loaded": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.load()
        deadline = time.monotonic() + 1.8
        while time.monotonic() < deadline:
            self.assertEqual(self.get("/manager/status")["state"], "ready")
            time.sleep(0.12)
        self.assertTrue(self.get("/manager/config")["models"][0]["keep_loaded"])
        self.api("POST", "/manager/keep-loaded", json={"model_id": "test-model", "keep_loaded": False})
        eventually(lambda: self.get("/manager/status")["state"] == "unloaded", timeout=8)

    def test_engine_error_does_not_retry_and_next_request_recovers(self):
        response = self.completion("FAKE_ENGINE_ERROR")
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.events("post")), 1)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "error"), None))
        self.assertTrue(rec["error"])
        response = self.completion("working next request")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.events("post")), 2)
        self.assertEqual(len(self.events("start")), 1)

    def test_scan_groups_split_shards_and_separates_projectors(self):
        for name in ("split-00001-of-00002.gguf", "split-00002-of-00002.gguf", "mmproj-test.gguf"):
            (self.model_dir / name).write_bytes(b"GGUF-fake")
        response = self.api("POST", "/manager/scan", json={})
        self.assertEqual(response.status_code, 200, response.text)
        names = {Path(item["path"]).name for item in response.json()["models"]}
        self.assertIn("split-00001-of-00002.gguf", names)
        self.assertNotIn("split-00002-of-00002.gguf", names)
        self.assertNotIn("mmproj-test.gguf", names)
        self.assertIn("mmproj-test.gguf", {Path(item["path"]).name for item in response.json()["projectors"]})
        self.assertEqual(self.events("start"), [])

    def test_config_validation_persistence_unknown_fields_export_and_import(self):
        current = self.get("/manager/config")
        changed = copy.deepcopy(current)
        changed["future_option"] = {"nested": "preserve me"}
        changed["models"][0]["future_model_option"] = "preserve too"
        r = self.api("PUT", "/manager/config", json=changed)
        self.assertEqual(r.status_code, 200, r.text)
        persisted = json.loads((self.data / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["future_option"], changed["future_option"])
        self.assertEqual(persisted["models"][0]["future_model_option"], "preserve too")
        exported = self.get("/manager/export")
        self.assertNotIn(self.token, json.dumps(exported))
        bad = copy.deepcopy(changed)
        bad["api_port"] = bad["engine_port"]
        r = self.api("PUT", "/manager/config", json=bad)
        self.assertGreaterEqual(r.status_code, 400, r.text)
        self.assertEqual(json.loads((self.data / "config.json").read_text(encoding="utf-8")), persisted)
        exported["idle_minutes"] = 22
        r = self.api("POST", "/manager/import", json=exported)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.get("/manager/config")["idle_minutes"], 22)

    def test_restart_recovers_saved_settings_and_completed_history_without_loading(self):
        self.save(idle_minutes=27, future_option={"preserve": True})
        response = self.completion("persistent history")
        self.assertEqual(response.status_code, 200, response.text)
        rec = eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.api("POST", "/manager/shutdown", json={})
        self.proc.wait(timeout=8)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "manager.py"), "--data-dir", str(self.data),
                                     "--port", str(self.port), "--engine-port", str(self.engine_port)],
                                    env=self.env, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        eventually(lambda: self.client.get("/health").status_code == 200, 12)
        restored = self.get("/manager/config")
        self.assertEqual(restored["idle_minutes"], 27)
        self.assertEqual(restored["future_option"], {"preserve": True})
        self.assertEqual((self.data / "admin-token").read_text(encoding="utf-8").strip(), self.token)
        self.assertTrue(any(item["id"] == rec["id"] and item["phase"] == "completed" for item in self.records()))
        self.assertEqual(self.get("/manager/status")["state"], "unloaded")
        self.assertEqual(len(self.events("start")), 1)

    def test_pending_status_distinguishes_manager_restart_from_model_reload(self):
        self.load()
        config = self.get("/manager/config")
        config["api_port"] = free_port()
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        status = self.get("/manager/status")
        self.assertTrue(status["pending_config"])
        self.assertTrue(status["pending_restart"])
        self.assertFalse(status["pending_model_reload"])

        config = self.get("/manager/config")
        config["api_port"] = self.port
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model["context"] = 4096
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        status = self.get("/manager/status")
        self.assertTrue(status["pending_config"])
        self.assertFalse(status["pending_restart"])
        self.assertTrue(status["pending_model_reload"])

    def test_model_load_settings_wait_for_explicit_reload_and_same_model_load_applies_them(self):
        self.load()
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(context=4096, cpu_threads=3, cache_type="q4_0", auto_fit=False, gpu_layers=-1)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        status = self.get("/manager/status")
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["pending_config"])
        self.assertEqual(len(self.events("start")), 1)

        # Re-loading the same model ID must not be treated as a no-op when
        # engine-affecting settings changed.
        response = self.api("POST", "/manager/load", json={"model_id": "test-model"})
        self.assertEqual(response.status_code, 200, response.text)
        eventually(lambda: len(self.events("start")) == 2 and self.get("/manager/status")["state"] == "ready")
        self.assertFalse(self.get("/manager/status")["pending_config"])
        argv = self.events("start")[-1]["argv"]
        self.assertEqual(argv[argv.index("-c") + 1], "4096")
        self.assertEqual(argv[argv.index("-t") + 1], "3")
        self.assertEqual(argv[argv.index("-tb") + 1], "3")
        self.assertEqual(argv[argv.index("-ctk") + 1], "q4_0")
        self.assertEqual(argv[argv.index("-ctv") + 1], "q4_0")
        self.assertEqual(argv[argv.index("-ngl") + 1], "-1")

    def test_pending_load_settings_auto_reload_before_next_inference(self):
        self.load()
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(context=4096, cpu_threads=2, cache_type="q8_0")
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.get("/manager/status")["pending_config"])
        self.assertEqual(len(self.events("start")), 1)

        r = self.completion(max_tokens=3000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.events("start")), 2)
        argv = self.events("start")[-1]["argv"]
        self.assertEqual(argv[argv.index("-c") + 1], "4096")
        self.assertEqual(argv[argv.index("-t") + 1], "2")
        self.assertEqual(argv[argv.index("-ctk") + 1], "q8_0")
        self.assertFalse(self.get("/manager/status")["pending_config"])
        forwarded = self.events("post")[-1]["body"]
        self.assertLessEqual(forwarded["max_tokens"], 3000)
    def test_default_profile_change_applies_without_engine_reload(self):
        self.load()
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model["default_profile_id"] = "quick-chat"
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.get("/manager/status")["pending_config"])
        r = self.completion(max_tokens=9999)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.events("start")), 1)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 2048)

    def test_vision_projector_reaches_engine_args(self):
        projector = self.run_dir / "mmproj-test.gguf"
        projector.write_bytes(b"GGUFfake-projector")
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(vision=True, mmproj=str(projector), auto_fit=False)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.load()
        argv = self.events("start")[-1]["argv"]
        self.assertEqual(argv[argv.index("--mmproj") + 1], str(projector))
        self.assertNotIn("--image-min-tokens", argv)
        self.assertNotIn("--no-reasoning-preserve", argv)

    def test_invalid_vision_projector_is_rejected_before_engine_start(self):
        projector = self.run_dir / "not-a-projector.gguf"
        projector.write_bytes(b"not-gguf")
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(vision=True, mmproj=str(projector), auto_fit=False)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.api("POST", "/manager/load", json={"model_id": "test-model"})
        self.assertEqual(response.status_code, 200, response.text)
        eventually(lambda: self.get("/manager/status")["state"] == "error")
        self.assertEqual(self.events("start"), [])
        self.assertIn("不是有效的 GGUF", self.get("/manager/status")["last_error"])
    def test_inactive_model_subsettings_do_not_create_false_pending_reload(self):
        self.load()
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        self.assertFalse(model["auto_fit"])
        self.assertFalse(model["mtp"])
        self.assertFalse(model["vision"])
        model["fit_target_mib"] = 9999
        model["fit_target_enabled"] = not model.get("fit_target_enabled", False)
        model["mtp_draft_path"] = "unused-draft.gguf"
        model["mtp_draft_max"] = 7
        model["mmproj"] = "unused-mmproj.gguf"
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.get("/manager/status")["pending_config"])
    def test_request_time_model_settings_apply_without_engine_reload(self):
        self.load()
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        model.update(temperature=0.17, top_p=0.77, top_k=23, min_p=0.03)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.get("/manager/status")["pending_config"])
        r = self.completion()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.events("start")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["temperature"], 0.17)
        self.assertEqual(forwarded["top_p"], 0.77)
        self.assertEqual(forwarded["top_k"], 23)
        self.assertEqual(forwarded["min_p"], 0.03)

    def test_changing_model_path_clears_stale_reasoning_capabilities(self):
        other = self.model_dir / "other-model.gguf"
        other.write_bytes(b"GGUFfake-test-only")
        config = self.get("/manager/config")
        model = next(x for x in config["models"] if x["id"] == "test-model")
        self.assertEqual(model["reasoning_capability"], "toggle")
        model["path"] = str(other)
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        saved = next(x for x in response.json()["models"] if x["id"] == "test-model")
        self.assertEqual(saved["reasoning_capability"], "unknown")
        self.assertEqual(saved["reasoning_efforts"], [])
        self.assertFalse(saved["reasoning_supported"])

    def test_management_requires_token_and_does_not_leak_it(self):
        for method, path in (("GET", "/manager/config"), ("GET", "/manager/status"), ("POST", "/manager/load"), ("POST", "/manager/shutdown"), ("GET", "/manager/export")):
            r = self.client.request(method, path, json={} if method == "POST" else None)
            self.assertIn(r.status_code, (401, 403), (path, r.text))
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.events("start"), [])
        r = self.client.get("/manager/config", headers={"X-Manager-Token": "wrong-token"})
        self.assertIn(r.status_code, (401, 403))
        self.assertNotIn(self.token, json.dumps(self.get("/manager/logs")))

    def test_invalid_config_is_rejected_atomically(self):
        original = self.get("/manager/config")
        invalid = []
        for key, value in (("api_port", -1), ("engine_port", 65536), ("idle_minutes", -1),
                           ("default_model_id", "missing"), ("default_profile_id", "missing")):
            item = copy.deepcopy(original)
            item[key] = value
            invalid.append((key, item))
        for label, key, value in (("context", "context", -1), ("GPU", "gpu_layers", -9),
                                  ("profile reference", "default_profile_id", "missing")):
            item = copy.deepcopy(original)
            item["models"][0][key] = value
            invalid.append((label, item))
        for key, value in (("thinking_mode", "surprise"), ("reasoning_level", "ultra"), ("budget_mode", "mystery"), ("thinking_budget", -1), ("max_tokens", 0)):
            item = copy.deepcopy(original)
            item["profiles"][0][key] = value
            invalid.append((key, item))
        for list_name in ("models", "profiles"):
            item = copy.deepcopy(original)
            item[list_name].append(copy.deepcopy(item[list_name][0]))
            invalid.append(("duplicate " + list_name, item))
        for label, item in invalid:
            with self.subTest(label=label):
                response = self.api("PUT", "/manager/config", json=item)
                self.assertIn(response.status_code, (400, 422), response.text)
                self.assertEqual(self.get("/manager/config"), original)
                self.assertEqual(json.loads((self.data / "config.json").read_text(encoding="utf-8")), original)

    def test_log_retention_change_prunes_existing_request_body_files_immediately(self):
        body_dir = self.data / "request-bodies"
        body_dir.mkdir(exist_ok=True)
        old_file = body_dir / "old.json"
        old_file.write_text('{"old": true}', encoding="utf-8")
        old_time = time.time() - 3 * 86400
        os.utime(old_file, (old_time, old_time))
        config = self.get("/manager/config")
        config["log_retention_days"] = 1
        response = self.api("PUT", "/manager/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(old_file.exists())
    def test_prompt_content_is_absent_from_default_logs_and_request_summaries(self):
        sentinel = "PRIVATE_REQUEST_CONTENT_7e3c9a"
        response = self.completion(sentinel)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(sentinel, json.dumps(self.records()))
        self.assertNotIn(sentinel, json.dumps(self.get("/manager/logs")))

    def test_paused_manager_rejects_inference_without_loading(self):
        r = self.api("POST", "/manager/accepting", json={"accepting": False})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.completion()
        self.assertGreaterEqual(r.status_code, 400)
        self.assertFalse(self.get("/manager/status")["accepting"])
        self.assertEqual(self.events("start"), [])

    def test_external_engine_port_conflict_never_kills_external_process(self):
        external_log = (self.run_dir / "external.log").open("w", encoding="utf-8")
        external = subprocess.Popen([sys.executable, str(FAKE), "--port", str(self.engine_port)], env=self.env,
                                    stdout=external_log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(trust_env=False) as c:
                eventually(lambda: c.get(self.engine_base + "/health").status_code == 200)
                r = self.completion()
                self.assertGreaterEqual(r.status_code, 400, r.text)
                self.assertIsNone(external.poll())
                self.assertEqual(c.get(self.engine_base + "/health").status_code, 200)
                self.assertEqual(len(self.events("start")), 1)
                self.assertEqual(self.events("post"), [])
        finally:
            external.terminate()
            external.wait(timeout=5)
            external_log.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
