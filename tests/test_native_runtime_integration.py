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
CORE = ROOT / "native-core/target/debug" / ("amiebl-core.exe" if os.name == "nt" else "amiebl-core")
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
