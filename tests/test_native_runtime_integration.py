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


if __name__ == "__main__":
    unittest.main()
