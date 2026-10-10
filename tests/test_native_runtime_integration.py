"""Run shared v1.0.0 HTTP fixtures against Rust, with approved native contracts
and additional background-request regressions. The frozen reference stays intact.
"""
import concurrent.futures
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "native-core/target")) / "debug" / ("amiebl-core.exe" if os.name == "nt" else "amiebl-core")
spec = importlib.util.spec_from_file_location("native_runtime_reference_tests", ROOT / "tests/test_manager_integration.py")
reference = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reference
spec.loader.exec_module(reference)


class NativeRuntimeIntegration(reference.ManagerIntegration):
    def test_background_burst_keeps_bounded_queue_and_cancellation(self):
        self.load()
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            active = pool.submit(self.completion, "FAKE_PREHEADER active")
            reference.eventually(lambda: bool(self.events("post")))
            waiting = [pool.submit(self.completion, "background title") for _ in range(31)]
            reference.eventually(lambda: self.get("/manager/status")["queued_count"] == 31)
            rejected = self.completion("one more utility request")
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertEqual(len(self.records()), 32)
            for record in self.records():
                if record["phase"] == "queued":
                    response = self.api("POST", f"/manager/requests/{record['id']}/cancel", json={})
                    self.assertEqual(response.status_code, 200, response.text)
            for job in waiting:
                job.result(timeout=10)
            active.result(timeout=15)
        self.assertEqual(len(self.events("post")), 1)
        self.assertEqual(len(self.events("start")), 1)
        self.assertEqual(self.get("/manager/status")["queued_count"], 0)

    def test_first_agent_title_and_summary_share_loaded_single_slot(self):
        self.auto_profile()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.completion, "hello", True)
            reference.eventually(lambda: self.get("/manager/status")["active_count"] == 1)
            title = pool.submit(self.completion, "Generate a short title for this conversation", False)
            reference.eventually(lambda: self.get("/manager/status")["queued_count"] == 1)
            self.assertEqual(first.result(timeout=20).status_code, 200)
            self.assertEqual(title.result(timeout=20).status_code, 200)
        summary = self.client.post("/v1/chat/completions", json={
            "model": "test-model", "stream": False,
            "messages": [
                {"role": "system", "content": "Return <summary> text"},
                {"role": "user", "content": "Summarize the conversation history so far, paying special attention to the most recent agent commands and tool results that triggered this summarization. Structure your summary using the enhanced format provided in the system message."},
            ],
        })
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(len(self.events("start")), 1)
        self.assertEqual(len(self.events("post")), 3)
        self.assertFalse(self.events("post")[-1]["body"]["chat_template_kwargs"]["enable_thinking"])
        records = reference.eventually(lambda: self.records() if len(self.records()) == 3 and all(r["phase"] == "completed" for r in self.records()) else None)
        self.assertEqual(len(records), 3)
        starts = self.events("start")
        slot_index = starts[0]["argv"].index("-np")
        self.assertEqual(starts[0]["argv"][slot_index + 1], "1")

    def test_native_gui_protocol_is_additive_and_health_shape_stays_compatible(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-AMIEBL-Core-Protocol"], "1")
        self.assertEqual(set(response.json()), {"ok", "app", "version"})
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["app"], "local-model-manager")

    def setUp(self):
        if not CORE.is_file():
            if os.environ.get("AMIEBL_REQUIRE_NATIVE_TESTS") == "1":
                self.fail("Required amiebl-core executable is missing")
            self.skipTest("Build amiebl-core first")
        launch = subprocess.Popen

        def native_manager(command, *args, **kwargs):
            if len(command) > 1 and Path(command[1]) == ROOT / "backend/manager.py":
                command = [str(CORE), "--experimental-runtime", *command[2:]]
            return launch(command, *args, **kwargs)

        with patch.object(reference.subprocess, "Popen", side_effect=native_manager):
            super().setUp()

    # Approved native allocation/decision contracts. Preserve all original
    # transport, forwarded thinking, timing and single-inference assertions.
    def test_output_cap_honors_stricter_client_limit(self):
        r = self.completion(max_tokens=200)
        self.assertEqual(r.status_code, 200, r.text)
        forwarded = self.events("post")[-1]["body"]
        self.assertEqual(forwarded["max_tokens"], 200)
        r = self.completion(max_tokens=10000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.events("post")[-1]["body"]["max_tokens"], 2048)

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
        rec = reference.eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：一般請求；關閉思考：辨識為問候或單純語言處理")
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
        rec = reference.eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：一般請求；開啟思考：包含分析、技術或推導要求")
        self.assertEqual(rec["max_tokens"], 300)
        self.assertGreaterEqual(rec["thinking_budget"], 0)
        self.assertLess(rec["thinking_budget"], 300, "Final answer must retain generation space")
        self.assertLessEqual(rec["thinking_budget"], 512)

    def test_auto_classifier_ambiguous_request_uses_profile_default(self):
        self.load()
        self.auto_profile()
        response = self.completion("介紹一下章魚")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events("classifier"), [])
        self.assertEqual(len(self.events("post")), 1)
        forwarded = self.events("post")[-1]["body"]
        self.assertTrue(forwarded["chat_template_kwargs"]["enable_thinking"])
        rec = reference.eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：一般請求；未命中明確規則，沿用模式的思考強度與預算")
        self.assertEqual(rec["effort"], "medium")
        self.assertEqual(rec["thinking_budget"], 1536)

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
        rec = reference.eventually(lambda: next((x for x in self.records() if x["phase"] == "completed"), None))
        self.assertEqual(rec["decision"], "自動判斷：一般請求；關閉思考：辨識為問候或單純語言處理")

    def test_repeated_native_owned_port_reload_does_not_resend_inference(self):
        self.load()
        for iteration in range(20):
            config = self.get("/manager/config")
            config["models"][0]["context"] = 4096 if iteration % 2 == 0 else 8192
            response = self.api("PUT", "/manager/config", json=config)
            self.assertEqual(response.status_code, 200, response.text)
            response = self.completion("reload single owned slot")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(self.events("start")), iteration + 2)
            self.assertEqual(len(self.events("post")), iteration + 1)
            self.assertFalse(self.get("/manager/status")["pending_model_reload"])


if __name__ == "__main__":
    unittest.main()
