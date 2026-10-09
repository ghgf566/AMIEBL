"""Engine management isolation, authenticated policy and optional official-package E2E."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import httpx

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "native-core/target")) / "debug/amiebl-core.exe"


class EngineManagerTests(unittest.TestCase):
    def setUp(self):
        if os.name != "nt" or not CORE.is_file():
            if os.environ.get("AMIEBL_REQUIRE_NATIVE_TESTS") == "1":
                self.fail("Required native Core is missing")
            self.skipTest("Windows native build required")
        self.tmp = tempfile.TemporaryDirectory(prefix="amiebl-engine-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "軟體 space"
        self.root.mkdir()
        shutil.copy2(CORE, self.root / "amiebl-core.exe")
        self.data = Path(self.tmp.name) / "data"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.log = (Path(self.tmp.name) / "core.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen([str(self.root / "amiebl-core.exe"), "--experimental-runtime", "--data-dir", str(self.data), "--port", str(self.port)], stdout=self.log, stderr=self.log, creationflags=subprocess.CREATE_NO_WINDOW)
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=60, trust_env=False)
        self.addCleanup(self.stop)
        for _ in range(200):
            try:
                if self.client.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if self.proc.poll() is not None:
                self.fail("Core exited: " + (Path(self.tmp.name) / "core.log").read_text(errors="replace"))
            time.sleep(.05)
        self.headers = {"X-Manager-Token": (self.data / "admin-token").read_text().strip()}

    def stop(self):
        if self.proc.poll() is None:
            try:
                self.client.post("/manager/shutdown", headers=getattr(self, "headers", {}), json={})
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.client.close()
        self.log.close()

    def api(self, method, endpoint="", payload=None):
        response = self.client.request(method, "/manager/engines" + endpoint, headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_authenticated_policy_preserves_external_config(self):
        self.assertEqual(self.client.get("/manager/engines").status_code, 401)
        before = self.client.get("/manager/config", headers=self.headers).json()
        status = self.api("GET")
        self.assertEqual(Path(status["root"]), self.root / "engines")
        self.assertEqual(status["policy"]["channel"], "stable")
        self.assertEqual(status["policy"]["mode"], "external")
        policy = dict(status["policy"], channel="preview", backend="cpu", update="off", pinned=True)
        self.api("POST", "/policy", policy)
        persisted = json.loads((self.root / "engines/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["policy"], policy)
        self.assertEqual(self.client.get("/manager/config", headers=self.headers).json(), before)
        invalid = self.client.post("/manager/engines/policy", headers=self.headers, json=dict(policy, channel="anything"))
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.api("GET")["policy"], policy)
        for endpoint in ["activate", "remove"]:
            response = self.client.post(f"/manager/engines/{endpoint}", headers=self.headers, json={"id": "../external"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api("POST", "/cancel", {})["job"]["state"], "idle")

    def test_server_status_is_independent_and_authenticated(self):
        self.assertEqual(self.client.get("/manager/engines/runtime").status_code, 401)
        policy = dict(self.api("GET")["policy"], mode="managed", update="off")
        self.api("POST", "/policy", policy)
        server = self.api("GET", "/runtime")
        self.assertEqual(server["state"], "not_installed")
        self.assertIsNone(server["pid"])
        self.assertFalse(server["owned"])
        self.api("POST", "/stop", {})
        self.assertEqual(self.api("GET", "/runtime")["state"], "not_installed")
        self.assertEqual(self.client.get("/health").status_code, 200)

    @unittest.skipUnless(os.environ.get("AMIEBL_TEST_ENGINE_DOWNLOAD") == "1", "Explicit official-package network acceptance")
    def test_official_cpu_channels_install_switch_rollback(self):
        for channel in ["stable", "preview"]:
            policy = dict(self.api("GET")["policy"], channel=channel, backend="cpu", update="off", mode="external")
            self.api("POST", "/policy", policy)
            checked = self.api("POST", "/check", {})
            candidate = checked["candidate"]
            self.assertEqual(candidate["channel"], channel)
            self.assertTrue(all(a["sha256"] for a in candidate["assets"]))
            self.api("POST", "/install", {})
            for _ in range(360):
                status = self.api("GET")
                if status["job"]["state"] in ["installed", "failed", "cancelled"]:
                    break
                time.sleep(.5)
            self.assertEqual(status["job"]["state"], "installed", status)
            self.api("POST", "/activate", {"id": candidate["id"]})
            connection = self.client.get("/manager/connection", headers=self.headers).json()
            self.assertTrue(connection["engine_exists"], connection)
            self.assertTrue(connection["ok"], connection)
        status = self.api("GET")
        self.assertEqual(len(status["packages"]), 2)
        self.assertEqual(self.api("POST", "/rollback", {})["active"], status["previous"])
        inactive = status["active"]
        package_dir = self.root / "engines/llama.cpp/versions" / inactive
        server = next(package_dir.rglob("llama-server.exe"))
        original = server.read_bytes()
        server.write_bytes(b"modified")
        modified = self.client.post("/manager/engines/activate", headers=self.headers, json={"id": inactive})
        self.assertEqual(modified.status_code, 400)
        self.assertEqual(self.api("GET")["active"], status["previous"])
        server.write_bytes(original)
        unknown = self.root / "engines/llama.cpp/versions" / inactive / "user-file.txt"
        unknown.write_text("preserve", encoding="utf-8")
        denied = self.client.post("/manager/engines/remove", headers=self.headers, json={"id": inactive})
        self.assertEqual(denied.status_code, 400)
        self.assertTrue(unknown.exists())
        unknown.unlink()
        self.api("POST", "/remove", {"id": inactive})
        self.assertFalse(unknown.parent.exists())
        evidence = os.environ.get("AMIEBL_ENGINE_EVIDENCE")
        if evidence:
            Path(evidence).write_text(json.dumps(self.api("GET"), ensure_ascii=False, indent=2), encoding="utf-8")

    def test_real_gui_engine_policy_controls(self):
        gui = Path(os.environ.get("AMIEBL_NATIVE_OUTPUT_DIR", ROOT / "build/native-gui/Release")) / "AMIEBL.Native.exe"
        if not gui.is_file():
            if os.environ.get("AMIEBL_REQUIRE_GUI_TESTS") == "1":
                self.fail("Required GUI is missing")
            self.skipTest("GUI build required")
        proc = subprocess.Popen([str(gui), "--data-dir", str(self.data), "--port", str(self.port), "--core", str(self.root / "amiebl-core.exe"), "--engine-test", "1"], creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc.wait(timeout=90)
            self.assertEqual(proc.returncode, 0)
            result = json.loads((self.data / "gui-e2e.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["engine_controls"])
            self.assertEqual(self.api("GET")["policy"], result["policy"])
            evidence = os.environ.get("AMIEBL_GUI_EVIDENCE_DIR")
            if evidence:
                Path(evidence).mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.data / "engine-management.png", Path(evidence) / "engine-management.png")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    @unittest.skipUnless(os.environ.get("AMIEBL_TEST_ENGINE_DOWNLOAD") == "1", "Explicit official-package network acceptance")
    def test_automatic_download_and_idle_activation(self):
        policy = dict(self.api("GET")["policy"], channel="stable", backend="cpu", mode="managed", update="auto")
        self.api("POST", "/policy", policy)
        for _ in range(240):
            status = self.api("GET")
            if status["active"] is not None or status["job"]["state"] == "failed":
                break
            time.sleep(.5)
        self.assertIsNotNone(status["active"], status)
        self.assertEqual(status["job"]["state"], "installed")
        self.assertIsNone(status["pending"])
        self.assertEqual(status["packages"][0]["channel"], "stable")
        self.api("POST", "/policy", dict(policy, pinned=True))
        self.assertTrue(self.api("GET")["policy"]["pinned"])


if __name__ == "__main__":
    unittest.main()
