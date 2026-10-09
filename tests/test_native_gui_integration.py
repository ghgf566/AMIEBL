"""Real C++ controls -> real Rust Core -> unchanged Fake Engine, isolated data.

No GUI or management endpoint is mocked. Native test mode invokes the actual
buttons through their automation peers, renders controls, and records checks.
"""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gui_native_runtime_fixture", ROOT / "tests/test_native_runtime_integration.py")
native = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = native
spec.loader.exec_module(native)
GUI = ROOT / "build/native-gui/Release/AMIEBL.Native.exe"


class NativeGuiIntegration(native.NativeRuntimeIntegration):
    def setUp(self):
        if os.name != "nt" or not GUI.is_file():
            if os.environ.get("AMIEBL_REQUIRE_GUI_TESTS") == "1":
                self.fail("Required native GUI executable is missing")
            self.skipTest("Build native Windows GUI before running integration")
        self.gui = None
        if self._testMethodName == "test_incompatible_reference_service_is_preserved":
            native.reference.ManagerIntegration.setUp(self)
        else:
            super().setUp()

    def tearDown(self):
        if self.gui is not None and self.gui.poll() is None:
            self.gui.terminate()  # GUI's owned job cleans its Core/engine children.
            self.gui.wait(timeout=10)
        output = os.environ.get("AMIEBL_GUI_EVIDENCE_DIR")
        if output:
            dest = Path(output) / self._testMethodName
            dest.mkdir(parents=True, exist_ok=True)
            for source in (self.events_file, self.run_dir / "manager.log"):
                if source.is_file():
                    shutil.copy2(source, dest / source.name)
            for pattern in ("*.png", "gui-e2e.json"):
                for source in self.data.glob(pattern):
                    shutil.copy2(source, dest / source.name)
        super().tearDown()

    def launch_gui(self):
        self.gui = subprocess.Popen(
            [str(GUI), "--data-dir", str(self.data), "--port", str(self.port),
             "--engine-port", str(self.engine_port), "--core", str(native.CORE), "--gui-test", "1"],
            env=self.env, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)

    def result(self):
        return json.loads((self.data / "gui-e2e.json").read_text(encoding="utf-8"))

    def test_native_controls_load_pause_keep_unload_and_cancel_owned_core(self):
        self.assertEqual(self.api("POST", "/manager/shutdown", json={}).status_code, 200)
        self.proc.wait(timeout=10)
        self.exercise_controls(owned=True)

    def test_incompatible_reference_service_is_preserved(self):
        before = self.get("/manager/config")
        self.launch_gui()
        self.gui.wait(timeout=20)
        self.assertEqual(self.gui.returncode, 0)
        self.assertFalse(self.result()["ok"])
        self.assertIn("協定 1", self.result()["error"])
        self.assertIsNone(self.proc.poll())
        self.assertTrue(self.get("/health")["ok"])
        self.assertEqual(self.get("/manager/config"), before)
        self.assertEqual(self.events(), [])

    def test_native_controls_preserve_attached_core_on_exit(self):
        self.exercise_controls(owned=False)
        self.assertIsNone(self.proc.poll())
        self.assertTrue(self.get("/health")["ok"])

    def exercise_controls(self, owned):
        self.launch_gui()

        def stage():
            if (self.data / "gui-e2e.json").exists():
                self.assertTrue(self.result()["ok"], self.result())
            self.assertIsNone(self.gui.poll(), "GUI exited before requesting inference")
            return (self.data / "gui-stage.txt").exists()

        native.reference.eventually(stage, 40)
        owned_pid = self.get("/manager/status")["pid"]
        self.assertIsNone(owned_pid)  # first load was explicitly unloaded by GUI
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(self.completion, "FAKE_SLOW native GUI cancellation", True)
            self.gui.wait(timeout=40)
            active.result(timeout=10)
        self.assertEqual(self.gui.returncode, 0)
        result = self.result()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["task_cancel_confirmed"])
        self.assertFalse(result["ui_parity_verified"])
        self.assertEqual(len(self.events("start")), 2)
        self.assertEqual(len(self.events("post")), 1)
        self.assertTrue(any(e["kind"] == "delete" for e in self.events()))
        for name in ("總覽", "導覽列精簡", "服務記錄展開", "任務作用中", "任務已取消"):
            self.assertGreater((self.data / (name + ".png")).stat().st_size, 1000)
        # Owned job and graceful shutdown must actually remove the service.
        if owned:
            with self.assertRaises(Exception):
                self.client.get("/health")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(NativeGuiIntegration(name) for name in vars(NativeGuiIntegration) if name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
