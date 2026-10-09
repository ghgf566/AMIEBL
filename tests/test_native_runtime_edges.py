"""Identical additional live HTTP scenarios for Python and Rust, without mocks.

The fake engine and reference fixture remain unchanged. load_tests restricts
this module to these extra scenarios; the 64 inherited tests run elsewhere.
"""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch
import test_native_runtime_integration as native


class EdgeScenarios:
    def setUp(self):
        launch = subprocess.Popen

        def isolated_logs(command, *args, **kwargs):
            env = kwargs.get("env")
            if env and "LMM_FAKE_EVENTS" in env:
                root = Path(env["LMM_FAKE_EVENTS"]).parent / "vscode-logs"
                root.mkdir(exist_ok=True)
                self.agent_log = root / "agent.log"
                self.agent_log.write_text("aborting session historical\n", encoding="utf-8")
                env["VSCODE_LOG_ROOT"] = str(root)
                env["VSCODE_ABORT_LOG_WATCH"] = "1"
            return launch(command, *args, **kwargs)

        with patch.object(subprocess, "Popen", side_effect=isolated_logs):
            super().setUp()
        # Ensure initial discovery skips the pre-existing event.
        time.sleep(0.6)

    def active(self):
        return native.reference.eventually(lambda: next((r for r in self.records() if r["phase"] in ("prompt", "thinking", "generating")), None))

    def assert_cancelled_and_idle(self, ident):
        row = native.reference.eventually(lambda: next((r for r in self.records() if r["id"] == ident and r["phase"] == "cancelled"), None))
        self.assertTrue(row["cancel_confirmed"])
        with native.reference.httpx.Client(trust_env=False) as client:
            native.reference.eventually(lambda: not any(s.get("is_processing") for s in client.get(self.engine_base + "/slots").json()))

    def test_abort_tail_skips_history_and_stale_event_then_handles_partial_line(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.completion, "FAKE_SLOW abort tail", True)
            rec = self.active()
            stale = (datetime.datetime.now() - datetime.timedelta(seconds=30)).isoformat(sep=" ")
            with self.agent_log.open("a", encoding="utf-8") as log:
                log.write(stale + " cancelling session stale\n")
            time.sleep(0.7)
            self.assertNotEqual(next(r for r in self.records() if r["id"] == rec["id"])["phase"], "cancelled")
            with self.agent_log.open("a", encoding="utf-8") as log:
                log.write("canceling sess")
            time.sleep(0.4)
            self.assertFalse(any(r["phase"] == "cancelled" for r in self.records()))
            with self.agent_log.open("a", encoding="utf-8") as log:
                log.write("ion\n")
            job.result(timeout=8)
        self.assert_cancelled_and_idle(rec["id"])

    def test_abort_new_log_tail_cancels_active_but_preserves_queued_request(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            job = pool.submit(self.completion, "FAKE_SLOW rotating log", True)
            rec = self.active()
            queued = pool.submit(self.completion, "queued survives abort")
            native.reference.eventually(lambda: any(r["phase"] == "queued" for r in self.records()))
            (self.agent_log.parent / "agent-new.log").write_text("session aborted\n", encoding="utf-8")
            job.result(timeout=10)
            self.assertEqual(queued.result(timeout=10).status_code, 200)
        self.assert_cancelled_and_idle(rec["id"])
        self.assertTrue(any(r["phase"] == "completed" for r in self.records()))
        self.assertEqual(len(self.events("post")), 2)

    def test_crashed_owned_engine_is_observed_and_next_request_reloads_once(self):
        self.load()
        pid = self.get("/manager/status")["pid"]
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(1, False, pid)
            self.assertTrue(handle)
            try:
                self.assertTrue(kernel.TerminateProcess(handle, 7))
            finally:
                kernel.CloseHandle(handle)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
        native.reference.eventually(lambda: self.get("/manager/status")["state"] == "error")
        self.assertEqual(self.completion("recover after crash").status_code, 200)
        self.assertEqual(len(self.events("start")), 2)
        self.assertEqual(len(self.events("post")), 1)

    def test_cancel_releases_slot_before_next_ticket_is_forwarded(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            job = pool.submit(self.completion, "FAKE_SLOW occupied slot", True)
            rec = self.active()
            queued = pool.submit(self.completion, "next slot owner")
            native.reference.eventually(lambda: any(r["phase"] == "queued" for r in self.records()))
            self.api("POST", f"/manager/requests/{rec['id']}/cancel", json={})
            job.result(timeout=8)
            self.assertEqual(queued.result(timeout=8).status_code, 200)
        self.assert_cancelled_and_idle(rec["id"])
        self.assertTrue(any(e["kind"] == "delete" and "/v1/stream" in e["path"] for e in self.events()))
        self.assertEqual(len(self.events("post")), 2)


class PythonEdges(EdgeScenarios, native.reference.ManagerIntegration):
    pass


class RustEdges(EdgeScenarios, native.NativeRuntimeIntegration):
    pass


def load_tests(loader, tests, pattern):
    names = sorted(n for n in vars(EdgeScenarios) if n.startswith("test_"))
    return unittest.TestSuite(cls(name) for cls in (PythonEdges, RustEdges) for name in names)
