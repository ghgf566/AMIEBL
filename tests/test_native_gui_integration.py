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

    def test_native_editors_save_conflict_and_dirty_navigation(self):
        before = self.get("/manager/config")
        self.gui = subprocess.Popen(
            [str(GUI), "--data-dir", str(self.data), "--port", str(self.port),
             "--core", str(native.CORE), "--editor-test", "1"],
            env=self.env, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        self.gui.wait(timeout=90)
        self.assertEqual(self.gui.returncode, 0)
        result = self.result()
        self.assertTrue(result["ok"], result)
        for key in ("editor_fields", "dynamic_fields", "dirty_navigation", "conflict_preserved",
                    "unknown_fields_preserved", "optional_sampling", "profile_array_save",
                    "system_save", "profile_add_duplicate", "profile_delete_confirm",
                    "unicode_editor_search"):
            self.assertTrue(result[key], result)
        self.assertFalse(result["ui_parity_verified"])
        self.assertEqual(self.get("/manager/config"), before)
        self.assertIsNone(self.proc.poll())
        self.assertEqual(self.events(), [])
        for name in ("模型庫", "使用模式", "系統"):
            self.assertGreater((self.data / (name + ".png")).stat().st_size, 1000)

    def test_native_tray_close_single_instance_and_core_recovery(self):
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW.restype = ctypes.c_int
        config = self.get("/manager/config")
        config.update(close_to_tray=True, start_hidden=True)
        self.assertEqual(self.api("PUT", "/manager/config", json=config).status_code, 200)
        command = [str(GUI), "--data-dir", str(self.data), "--port", str(self.port),
                   "--core", str(native.CORE), "--lifecycle-test", "1"]
        self.gui = subprocess.Popen(command, env=self.env, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        ready = self.data / "lifecycle-ready.json"
        native.reference.eventually(lambda: ready.exists() or self.gui.poll() is not None, 30)
        self.assertIsNone(self.gui.poll(), self.result() if (self.data / "gui-e2e.json").exists() else "GUI exited")
        observed = json.loads(ready.read_text(encoding="utf-8"))
        self.assertTrue(observed["hidden_start"])
        hwnd = observed["hwnd"]
        self.assertTrue(user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE, real window handler
        native.reference.eventually(lambda: not user32.IsWindowVisible(hwnd), 10)
        (self.data / "lifecycle-hidden.txt").write_text("ready", encoding="utf-8")
        native.reference.eventually(lambda: (self.data / "lifecycle-hidden-confirmed.txt").exists(), 10)
        secondary = subprocess.Popen(command, env=self.env, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            secondary.wait(timeout=20)
            self.assertEqual(secondary.returncode, 0)
        finally:
            if secondary.poll() is None:
                secondary.terminate()
                secondary.wait(timeout=10)
        native.reference.eventually(lambda: (self.data / "lifecycle-revealed.txt").exists(), 10)
        self.assertTrue(user32.IsWindowVisible(hwnd))
        self.assertIsNone(self.proc.poll())  # second GUI must not stop the attached Core
        self.assertEqual(self.api("POST", "/manager/shutdown", json={}).status_code, 200)
        self.proc.wait(timeout=10)
        (self.data / "lifecycle-core-stopped.txt").write_text("ready", encoding="utf-8")
        self.gui.wait(timeout=40)
        result = self.result()
        self.assertTrue(result["ok"], result)
        for key in ("tray_registered", "close_to_background", "single_instance_reveal",
                    "reconnect_preserved_draft", "owned_recovery"):
            self.assertTrue(result[key], result)
        with self.assertRaises(Exception):
            self.client.get("/health")
        self.assertEqual(self.events(), [])

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
