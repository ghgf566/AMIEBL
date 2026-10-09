"""Isolated HTTP smoke tests for migration-only Rust Core preview.

This server is deliberately NOT the full replacement for Python. The preview
MUST refuse any real AMIEBL data folder and production port. In particular
these tests do not assert that inference, cancellation, GUI, or engine handling
has migrated.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
SERVER=ROOT/"native-core"/"target"/"debug"/(
    "amiebl-core-preview.exe" if os.name=="nt" else "amiebl-core-preview"
)


def http(base, endpoint, *, token=None, origin=None):
    headers={}
    if token is not None: headers["X-Manager-Token"]=token
    if origin is not None: headers["Origin"]=origin
    request=urllib.request.Request(base+endpoint,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=5) as reply:
            return reply.status,json.loads(reply.read()),dict(reply.headers)
    except urllib.error.HTTPError as error:
        return error.code,json.loads(error.read()),dict(error.headers)

class NativePreviewSmokeTests(unittest.TestCase):
    def setUp(self):
        if not SERVER.exists():self.skipTest("Compile Rust migration preview first")
    def test_isolated_http_health_models_and_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/"new-isolated-user-data"
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            self.assertNotEqual(port,8080)
            process=subprocess.Popen(
                [str(SERVER),"--data-dir",str(data),"--port",str(port)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
            )
            self.addCleanup(lambda: process.poll() is None and process.terminate())
            try:
                base=f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail(f"Preview exited with status {process.returncode}")
                    try:
                        status,body,_=http(base,"/health")
                        if status==200:break
                    except (OSError,TimeoutError):
                        pass
                    time.sleep(.075)
                else:
                    self.fail("Rust preview failed to bind within timeout")
                self.assertEqual(body["app"],"local-model-manager")
                self.assertEqual(body["version"],"1.1.0-preview")
                self.assertEqual((data/"config.json").exists(),True)
                token=(data/"admin-token").read_text().strip()
                self.assertGreaterEqual(len(token),24)
                self.assertEqual(http(base,"/manager/config")[0],401)
                self.assertEqual(http(base,"/manager/export",token="wrong")[0],401)
                self.assertEqual(http(base,"/manager/config",token=token,
                                      origin="https://unknown.example")[0],403)
                status,conf,headers=http(base,"/manager/config",token=token)
                self.assertEqual(status,200)
                self.assertEqual(conf["schema_version"],1)
                self.assertIn("X-AMIEBL-Migration-Preview",headers)
                status,export,_=http(base,"/manager/export",token=token)
                self.assertEqual(status,200)
                self.assertEqual(export,conf)
                status,models,_=http(base,"/v1/models")
                self.assertEqual(status,200)
                self.assertEqual(models,{"object":"list","data":[]})
                self.assertEqual(http(base,"/v1/chat/completions")[0],404)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

    def test_preview_rejects_production_port_and_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            command=[str(SERVER),"--data-dir",str(data),"--port","8080"]
            invalid=subprocess.run(command,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,timeout=8)
            self.assertNotEqual(invalid.returncode,0)
            (data/"config.json").write_text("{}",encoding="utf-8")
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            invalid=subprocess.run([str(SERVER),"--data-dir",str(data),
                "--port",str(port)],stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,timeout=8)
            self.assertNotEqual(invalid.returncode,0)
            self.assertEqual((data/"config.json").read_text(), "{}")

if __name__=="__main__":unittest.main()
