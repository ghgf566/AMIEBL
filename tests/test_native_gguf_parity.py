"""Compare v1.0.0 Python GGUF inspection with native Rust on identical files.

All fixtures are tiny GGUF header/tensor-directory records; no model weights,
drivers, CUDA, or private user model files are needed.
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

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "native-core" / "target" / "debug" / (
    "gguf-probe.exe" if os.name == "nt" else "gguf-probe"
)

def import_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

legacy = import_module("amiebl_legacy_gguf_reference",ROOT / "backend" / "manager.py")
fixtures = import_module("amiebl_test_gguf_fixtures",ROOT / "tests" / "test_gguf_capabilities.py")


class NativeGGUFDifferentialTests(unittest.TestCase):
    def setUp(self):
        if not PROBE.is_file():
            self.skipTest("Rust GGUF probe is not built; CI compiles and runs this test.")

    def run_rust(self, op, **payload):
        proc = subprocess.run([str(PROBE)],
            input=json.dumps({"op":op,**payload},ensure_ascii=False),
            text=True,encoding="utf-8",capture_output=True,timeout=25,check=True)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"],result)
        return result["result"]

    def test_reasoning_templates(self):
        cases = [
            "",
            "No reasoning controls at all.",
            "{{ '<think>' }} <think> reasoning_content",
            "{% if not thinking is defined %}{% set thinking = false %}{% endif %}{{ '<think>' }}",
            """{% if enable_thinking is undefined or enable_thinking is true %}
            {% set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
            {% if resolved_reasoning_effort not in ('xhigh','medium','low') %}{{ raise_exception('bad') }}{% endif %}
            {{ '<think>' }}{% endif %}""",
            "{% set reasoning_effort = reasoning_effort|default('medium') %} {{ '<think>' }}",
            "Disabling thinking is not supported; {% if enable_thinking == false %}{{ raise_exception('bad') }}{% endif %}",
            """{% if thinking_mode in ['enabled','disabled','adaptive'] %}{% endif %}""",
            "Output: 'Keep your thinking brief'; no Jinja input variables.",
        ]
        for template in cases:
            with self.subTest(template=template[:40]):
                self.assertEqual(self.run_rust("reasoning",template=template),
                    legacy.analyze_reasoning_template(template))

    def test_gguf_files_and_split_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for layers, tensor in (
                (None,False),(None,True),(0,True),(2,False),(2,True),(4,True)
            ):
                name=f"model-{layers}-{tensor}.gguf"
                path=root / name
                fixtures.write_fixture(path,nextn_layers=layers,nextn_tensor=tensor)
                with self.subTest(model=name):
                    self.assertEqual(self.run_rust("inspect",path=str(path)),
                                     legacy.inspect_gguf_capabilities(str(path)))
            for text in (
                "{% if enable_thinking %}{{ '<think>' }}{% endif %}",
                "{% set reasoning_effort = reasoning_effort|default('high') %}",
                ""
            ):
                path=root / "model-template.gguf"
                fixtures.write_fixture(path,nextn_layers=2,nextn_tensor=True,chat_template=text)
                with self.subTest(template=text[:20]):
                    self.assertEqual(self.run_rust("inspect",path=str(path)),
                                     legacy.inspect_gguf_capabilities(str(path)))

            for named_count in (1,2):
                path=root / "model-named.gguf"
                fixtures.write_fixture(path,nextn_layers=None,nextn_tensor=False,
                    named_chat_template="{{ '<think>' }}",
                    second_named_chat_template="{{ enable_thinking }}" if named_count==2 else "")
                with self.subTest(named_count=named_count):
                    self.assertEqual(self.run_rust("inspect",path=str(path)),
                                     legacy.inspect_gguf_capabilities(str(path)))
            for invalid in (b"",b"not GGUF",b"GGUF\x05\x00\x00\x00"):
                path=root / "bad.gguf"
                path.write_bytes(invalid)
                self.assertEqual(self.run_rust("inspect",path=str(path)),
                                 legacy.inspect_gguf_capabilities(str(path)))
            absent = root / "does-not-exist.gguf"
            self.assertEqual(self.run_rust("inspect",path=str(absent)),
                             legacy.inspect_gguf_capabilities(str(absent)))

            first=root / "Qwen-00001-of-00002.gguf"
            second=root / "Qwen-00002-of-00002.gguf"
            fixtures.write_fixture(first,nextn_layers=3,nextn_tensor=False)
            fixtures.write_fixture(second,nextn_layers=None,nextn_tensor=True)
            self.assertEqual(self.run_rust("inspect",path=str(first)),
                             legacy.inspect_gguf_capabilities(str(first)))

    def test_capability_merge_unknown_fields_staleness_and_context_limit(self):
        model={
            "id":"qwen","context":32768,"mtp":True,"mtp_source":"native","mtp_draft_max":3,
            "reasoning_capability":"toggle","reasoning_efforts":["low","medium"],
            "reasoning_default_effort":"medium","reasoning_budget_supported":True,
            "reasoning_toggle_keys":["enable_thinking"],"reasoning_detection":"runtime",
            "reasoning_supported":True,"other_field":"preserved",
        }
        detected={
            "inspection_ok":True,"native_context":8192,"mtp_capability":"unavailable",
            "mtp_layers":0,"reasoning_capability":"unknown","reasoning_efforts":[],
            "reasoning_default_effort":"","reasoning_budget_supported":False,
            "reasoning_toggle_keys":[],"reasoning_detection":"pending",
        }
        for reset in (False,True):
            candidate = copy.deepcopy(model)
            legacy.apply_detected_model_capabilities(candidate,detected,
                reset_for_path_change=reset)
            self.assertEqual(self.run_rust("merge",model=model,detected=detected,
                reset_for_path_change=reset),candidate)
        transient=copy.deepcopy(detected)
        transient["inspection_ok"]=False
        candidate=copy.deepcopy(model)
        legacy.apply_detected_model_capabilities(candidate,transient)
        self.assertEqual(self.run_rust("merge",model=model,detected=transient,
            reset_for_path_change=False),candidate)

if __name__ == "__main__":
    unittest.main()
