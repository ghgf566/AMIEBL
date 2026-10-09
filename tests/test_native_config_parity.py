"""Differential schema test against the actual v1.0.0 Python reference.

This intentionally executes the old Python code as an oracle, sends identical
JSON to the new native Rust config-probe, and compares the *entire* result or
rejection message. No tests may be removed just because the port is difficult.

When the native executable is not built yet the legacy regression pass skips
this class; the migration CI builds and explicitly reruns it separately.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "native-core" / "target" / "debug" / ("config-probe.exe" if os.name == "nt" else "config-probe")

spec = importlib.util.spec_from_file_location("amiebl_legacy_config_reference", ROOT / "backend" / "manager.py")
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)


class NativeConfigDifferentialTests(unittest.TestCase):
    def setUp(self):
        if not PROBE.is_file():
            self.skipTest("Native Rust probe not compiled; migration CI builds and reruns explicitly.")

    def compare(self, input_config, startup=False):
        request = copy.deepcopy(input_config)
        try:
            candidate = legacy.normalize_startup_config(request) if startup else request
            expected = {"ok": True, "result": legacy.validate_config(candidate)}
        except ValueError as exc:
            expected = {"ok": False, "error": str(exc)}
        proc = subprocess.run(
            [str(PROBE)] + (["--startup"] if startup else []),
            input=json.dumps(input_config, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=True,
        )
        actual = json.loads(proc.stdout)
        self.assertEqual(actual, expected)

    def test_full_schema_parity(self):
        with tempfile.TemporaryDirectory() as root:
            env = {"LMM_MODEL_DIR": str(Path(root) / "nonexistent-model-directory"),
                   "LMM_ENGINE_DIR": str(Path(root) / "llama.cpp")}
            with patch.dict(os.environ, env):
                base = legacy.default_config()
                scenarios = [("fresh", base, False)]

                future = copy.deepcopy(base)
                future["some_future_extension"] = {"preserve_me": [1, "中文"]}
                future["profiles"][0]["novel_custom_field"] = True
                scenarios.append(("unknown_fields", future, False))

                for label, mutation in (
                    ("invalid_api_port", lambda c: c.update(api_port=1023)),
                    ("same_ports", lambda c: c.update(engine_port=8080)),
                    ("bad_schema", lambda c: c.update(schema_version=2)),
                    ("invalid_retention", lambda c: c.update(log_retention_days=0)),
                    ("bad_start_hidden", lambda c: c.update(start_hidden="no")),
                    ("invalid_model_dirs", lambda c: c.update(model_dirs=[1, 2])),
                    ("invalid_engine_dir", lambda c: c.update(engine_dir="")),
                    ("bad_default_profile", lambda c: c.update(default_profile_id="missing")),
                    ("empty_profiles", lambda c: c.update(profiles=[])),
                ):
                    candidate = copy.deepcopy(base)
                    mutation(candidate)
                    scenarios.append((label, candidate, False))

                profile = copy.deepcopy(base)
                profile["profiles"][0]["agent_sync_mode"] = "managed"
                profile["profiles"][0]["agent_tools"] = ["execute", "read", "search"]
                scenarios.append(("agent_managed", profile, False))

                for label, mutation in (
                    ("bad_agent_tool", lambda p: p.update(agent_tools=["no spaces"])),
                    ("bad_agent_name", lambda p: p.update(agent_name="  ")),
                    ("bad_thinking_mode", lambda p: p.update(thinking_mode="unknown")),
                    ("bad_budget", lambda p: p.update(budget_mode="custom", thinking_budget=999999)),
                    ("fractional_cap", lambda p: p.update(max_tokens=4096.5)),
                    ("float_cap", lambda p: p.update(max_tokens=4096.0)),
                ):
                    candidate = copy.deepcopy(base)
                    mutation(candidate["profiles"][0])
                    scenarios.append((label, candidate, False))

                legacy_profile = copy.deepcopy(base)
                p = legacy_profile["profiles"][0]
                p.pop("reasoning_level")
                p.pop("budget_mode")
                p["effort"] = "high"
                scenarios.append(("legacy_profile", legacy_profile, False))

                base_model = copy.deepcopy(base)
                base_model["models"] = [
                    {"id": "m", "name": "Qwen test", "path": "C:/Models/test.gguf",
                     "context": 8192, "cpu_threads": 0, "vision": False,
                     "mmproj": "C:/Models/mmproj-F16.gguf", "mtp": False,
                     "mtp_source": "external", "mtp_draft_path": "C:/Models/draft.gguf",
                     "native_context": 32768, "future_setting": "retained"}
                ]
                base_model["default_model_id"] = "m"
                scenarios.append(("model_with_inactive_optional_paths", base_model, False))

                for label, mutation in (
                    ("invalid_context", lambda m: m.update(context=256)),
                    ("over_native_context", lambda m: m.update(context=65536)),
                    ("fractional_gpu_layers", lambda m: m.update(gpu_layers=1.5)),
                    ("missing_projector", lambda m: m.update(vision=True, mmproj="")),
                    ("bad_mtp_source", lambda m: m.update(mtp_source="unsupported")),
                    ("mtp_without_external_draft", lambda m: m.update(mtp=True, mtp_source="external", mtp_draft_path="")),
                    ("invalid_kv_cache", lambda m: m.update(cache_type="fp8")),
                    ("bad_reasoning", lambda m: m.update(reasoning_capability="???")),
                ):
                    candidate = copy.deepcopy(base_model)
                    mutation(candidate["models"][0])
                    scenarios.append((label, candidate, False))

                scenarios.append(("repaired_old_disk_context", base_model, True))

                for label, candidate, startup in scenarios:
                    with self.subTest(label=label):
                        self.compare(candidate, startup=startup)


if __name__ == "__main__":
    unittest.main()
