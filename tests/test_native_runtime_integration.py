"""Run the unchanged v1.0.0 HTTP/fake-engine assertions against the real Rust service.

Only the manager launch command changes. Fixtures, engine, request payloads,
assertions and cleanup remain shared with test_manager_integration.py.
"""
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
