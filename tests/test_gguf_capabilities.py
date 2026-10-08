from __future__ import annotations

import importlib.util
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("amiebl_manager_caps", ROOT / "backend" / "manager.py")
manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manager
assert SPEC.loader is not None
SPEC.loader.exec_module(manager)


def gguf_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def write_fixture(path: Path, *, nextn_layers: int | None, nextn_tensor: bool, context_length: int = 262144, chat_template: str = "", named_chat_template: str = "", second_named_chat_template: str = "") -> None:
    kv = []
    kv.append(gguf_string("general.architecture") + struct.pack("<I", 8) + gguf_string("qwen35"))
    kv.append(gguf_string("qwen35.context_length") + struct.pack("<I", 4) + struct.pack("<I", context_length))
    if chat_template:
        kv.append(gguf_string("tokenizer.chat_template") + struct.pack("<I", 8) + gguf_string(chat_template))
    if named_chat_template:
        kv.append(gguf_string("tokenizer.chat_template.reasoning") + struct.pack("<I", 8) + gguf_string(named_chat_template))
    if second_named_chat_template:
        kv.append(gguf_string("tokenizer.chat_template.tools") + struct.pack("<I", 8) + gguf_string(second_named_chat_template))
    if nextn_layers is not None:
        kv.append(
            gguf_string("qwen35.nextn_predict_layers")
            + struct.pack("<I", 4)
            + struct.pack("<I", nextn_layers)
        )

    tensor_name = "blk.24.nextn.eh_proj.weight" if nextn_tensor else "blk.0.attn_norm.weight"
    tensor = (
        gguf_string(tensor_name)
        + struct.pack("<I", 1)
        + struct.pack("<q", 1)
        + struct.pack("<I", 0)
        + struct.pack("<Q", 0)
    )

    payload = (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", 1)
        + struct.pack("<Q", len(kv))
        + b"".join(kv)
        + tensor
    )
    path.write_bytes(payload)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_text_retries_transient_replace_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            real_replace = manager.os.replace
            calls = 0

            def flaky_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    exc = PermissionError(13, "simulated transient lock", str(destination))
                    exc.winerror = 5
                    raise exc
                return real_replace(source, destination)

            with patch.object(manager.os, "replace", side_effect=flaky_replace):
                manager.atomic_text(target, "ok\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "ok\n")
            self.assertEqual(calls, 2)

class StartupMigrationTests(unittest.TestCase):
    def test_startup_normalization_clamps_legacy_context_without_weakening_put_validation(self):
        raw = {"models": [{"context": 32768, "native_context": 8192}]}
        normalized = manager.normalize_startup_config(raw)
        self.assertEqual(normalized["models"][0]["context"], 8192)
        self.assertEqual(raw["models"][0]["context"], 32768)

class CapabilityMergeTests(unittest.TestCase):
    def test_detection_clamps_context_and_resets_stale_reasoning_on_path_change(self):
        model = {
            "context": 32768,
            "mtp": True,
            "mtp_source": "native",
            "mtp_draft_max": 3,
            "reasoning_capability": "toggle",
            "reasoning_efforts": ["low", "medium", "xhigh"],
            "reasoning_default_effort": "xhigh",
            "reasoning_budget_supported": True,
            "reasoning_toggle_keys": ["enable_thinking"],
            "reasoning_detection": "runtime",
            "reasoning_supported": True,
        }
        detected = {
            "native_context": 8192,
            "mtp_capability": "unavailable",
            "mtp_layers": 0,
            "reasoning_capability": "unknown",
            "reasoning_efforts": [],
            "reasoning_default_effort": "",
            "reasoning_budget_supported": False,
            "reasoning_toggle_keys": [],
            "reasoning_detection": "pending",
        }
        manager.apply_detected_model_capabilities(model, detected, reset_for_path_change=True)
        self.assertEqual(model["context"], 8192)
        self.assertEqual(model["native_context"], 8192)
        self.assertFalse(model["mtp"])
        self.assertIsNone(model["mtp_draft_max"])
        self.assertEqual(model["reasoning_capability"], "unknown")
        self.assertEqual(model["reasoning_efforts"], [])
        self.assertFalse(model["reasoning_supported"])

    def test_reinspection_preserves_runtime_reasoning_when_template_is_ambiguous(self):
        model = {
            "context": 4096,
            "mtp": False,
            "mtp_source": "external",
            "mtp_draft_max": None,
            "reasoning_capability": "toggle",
            "reasoning_efforts": ["low", "medium"],
            "reasoning_default_effort": "medium",
            "reasoning_budget_supported": True,
            "reasoning_toggle_keys": ["enable_thinking"],
            "reasoning_detection": "runtime",
            "reasoning_supported": True,
        }
        detected = {
            "native_context": 0,
            "mtp_capability": "unavailable",
            "mtp_layers": 0,
            "reasoning_capability": "unknown",
            "reasoning_efforts": [],
            "reasoning_default_effort": "",
            "reasoning_budget_supported": False,
            "reasoning_toggle_keys": [],
            "reasoning_detection": "pending",
        }
        detected["inspection_ok"] = True
        manager.apply_detected_model_capabilities(model, detected, reset_for_path_change=False)
        self.assertEqual(model["reasoning_capability"], "toggle")
        self.assertEqual(model["reasoning_efforts"], ["low", "medium"])
        self.assertEqual(model["reasoning_detection"], "runtime")

class FitCommandTests(unittest.TestCase):
    def test_custom_fit_target_reaches_llama_fit_params(self):
        model = {
            "path": r"D:\\model\\test.gguf",
            "context": 65536,
            "cache_type": "q4_0",
            "fit_target_enabled": True,
            "fit_target_mib": 3072,
        }
        cmd = manager.build_fit_params_command(r"C:\\llama", model)
        self.assertEqual(cmd[cmd.index("-c") + 1], "65536")
        self.assertEqual(cmd[cmd.index("-ctk") + 1], "q4_0")
        self.assertEqual(cmd[cmd.index("-ctv") + 1], "q4_0")
        self.assertEqual(cmd[cmd.index("--fit-target") + 1], "3072")

    def test_native_fit_target_default_is_not_overridden(self):
        model = {
            "path": "model.gguf",
            "context": 32768,
            "cache_type": "q8_0",
            "fit_target_enabled": False,
            "fit_target_mib": 9999,
        }
        cmd = manager.build_fit_params_command(".", model)
        self.assertNotIn("--fit-target", cmd)

class FitParameterTests(unittest.TestCase):
    def test_gpu_auto_fit_reserve_is_only_passed_when_enabled(self):
        model = {"path": "main.gguf", "context": 65536, "cache_type": "q4_0",
                 "fit_target_enabled": True, "fit_target_mib": 3072}
        cmd = manager.build_fit_params_command("engine", model)
        self.assertIn("--fit-target", cmd)
        self.assertEqual(cmd[cmd.index("--fit-target") + 1], "3072")
        self.assertEqual(cmd[cmd.index("-c") + 1], "65536")
        self.assertEqual(cmd[cmd.index("-ctk") + 1], "q4_0")
        self.assertEqual(cmd[cmd.index("-ctv") + 1], "q4_0")
        model["fit_target_enabled"] = False
        self.assertNotIn("--fit-target", manager.build_fit_params_command("engine", model))
class FitOutputTests(unittest.TestCase):
    def test_parses_positive_gpu_layer_count(self):
        self.assertEqual(manager.parse_fit_gpu_layers("-c 65536 -ngl 28"), 28)

    def test_parses_all_gpu_layers_sentinel(self):
        self.assertEqual(manager.parse_fit_gpu_layers("-c 65536 -ngl -1"), -1)

class ReasoningTemplateTests(unittest.TestCase):
    def test_qwen_native_effort_and_toggle(self):
        template = """
        {% if enable_thinking is undefined or enable_thinking is true %}
        {% set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
        {% if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}{{ raise_exception('bad') }}{% endif %}
        {{ '<think>' }}
        {% else %}{{ '<think></think>' }}{% endif %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "toggle")
        self.assertEqual(result["reasoning_efforts"], ["low", "medium", "xhigh"])
        self.assertEqual(result["reasoning_default_effort"], "xhigh")
        self.assertTrue(result["reasoning_budget_supported"])
        self.assertIn("enable_thinking", result["reasoning_toggle_keys"])

    def test_deepseek_plain_thinking_kwarg(self):
        template = """
        {% if not thinking is defined %}{% set thinking = false %}{% endif %}
        {% if not thinking %}{{ '</think>' }}{% else %}{{ '<think>' }}{% endif %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "toggle")
        self.assertEqual(result["reasoning_efforts"], [])
        self.assertIn("thinking", result["reasoning_toggle_keys"])

    def test_reasoning_instruction_prose_is_not_a_thinking_kwarg(self):
        template = """
        {% set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
        {% set reasoning_instructions = 'Keep your thinking brief and focused.' %}
        {% if enable_thinking %}{{ '<think>' }}{% endif %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_toggle_keys"], ["enable_thinking"])

    def test_glm_enable_thinking_toggle(self):
        template = """
        <|assistant|>{{ '\\n<think></think>' if (enable_thinking is defined and not enable_thinking) else '' }}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "toggle")
        self.assertIn("enable_thinking", result["reasoning_toggle_keys"])

    def test_minimax_thinking_mode_toggle(self):
        template = """
        {% set think_begin_token = '<mm:think>' %}
        {% set think_end_token = '</mm:think>' %}
        {% if thinking_mode is defined %}
        {% if thinking_mode == "enabled" %}think
        {% elif thinking_mode == "disabled" %}direct
        {% elif thinking_mode == "adaptive" %}adaptive{% endif %}
        {% endif %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "toggle")
        self.assertIn("thinking_mode", result["reasoning_toggle_keys"])
        self.assertTrue(result["reasoning_budget_supported"])
        self.assertEqual(manager.apply_reasoning_toggle({}, ["thinking_mode"], True)["thinking_mode"], "enabled")
        self.assertEqual(manager.apply_reasoning_toggle({}, ["thinking_mode"], False)["thinking_mode"], "disabled")

    def test_reasoning_strength_is_treated_as_native_effort_protocol(self):
        template = """
        {% set rs = reasoning_strength if reasoning_strength is defined and reasoning_strength else 'high' %}
        {% if rs in ['low', 'medium', 'high', 'xhigh'] %}{{ '<think>' }}{% endif %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "always")
        self.assertEqual(result["reasoning_efforts"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(result["reasoning_default_effort"], "high")
    def test_gemma4_enable_thinking_channel(self):
        template = """
        {% set enable_thinking = enable_thinking | default(false) %}
        {% if enable_thinking %}{{ '<|think|>\\n' }}{% endif %}
        {% set thinking_text = message.get('reasoning') or message.get('reasoning_content') %}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "toggle")
        self.assertIn("enable_thinking", result["reasoning_toggle_keys"])
        self.assertTrue(result["reasoning_budget_supported"])
    def test_gemma_like_template_without_reasoning(self):
        template = "{% for message in messages %}{{ message.role }}: {{ message.content }}{% endfor %}"
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "none")
        self.assertEqual(result["reasoning_efforts"], [])
        self.assertFalse(result["reasoning_budget_supported"])

    def test_always_reasoning_template_does_not_offer_disable(self):
        template = """
        {% if enable_thinking is defined and enable_thinking is false %}
        {{ raise_exception('Disabling thinking is not supported.') }}
        {% endif %}
        {{ '<think>' }}
        """
        result = manager.analyze_reasoning_template(template)
        self.assertEqual(result["reasoning_capability"], "always")
        self.assertEqual(result["reasoning_toggle_keys"], [])

    def test_abstract_level_maps_to_native_efforts(self):
        supported = ["low", "medium", "xhigh"]
        self.assertEqual(manager.map_native_reasoning_effort("light", supported), "low")
        self.assertEqual(manager.map_native_reasoning_effort("balanced", supported), "medium")
        self.assertEqual(manager.map_native_reasoning_effort("deep", supported), "xhigh")
        self.assertEqual(manager.map_native_reasoning_effort("extreme", supported), "xhigh")

class GgufCapabilityTests(unittest.TestCase):
    def test_detects_native_mtp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mtp.gguf"
            write_fixture(path, nextn_layers=1, nextn_tensor=True)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "available")
            self.assertEqual(result["mtp_layers"], 1)
            self.assertEqual(result["gguf_architecture"], "qwen35")
            self.assertEqual(result["native_context"], 262144)
            self.assertEqual(result["mtp_tensor_count"], 1)

    def test_multiple_named_templates_without_default_remain_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ambiguous-templates.gguf"
            write_fixture(
                path,
                nextn_layers=None,
                nextn_tensor=False,
                named_chat_template="{% if enable_thinking %}{{ '<think>' }}{% endif %}",
                second_named_chat_template="{% for message in messages %}{{ message.content }}{% endfor %}",
            )
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["reasoning_capability"], "unknown")
            self.assertEqual(result["reasoning_detection"], "pending")
    def test_default_chat_template_wins_over_named_reasoning_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.gguf"
            write_fixture(
                path,
                nextn_layers=None,
                nextn_tensor=False,
                chat_template="{% for message in messages %}{{ message.content }}{% endfor %}",
                named_chat_template="{% if enable_thinking %}{{ '<think>' }}{% endif %} " * 10,
            )
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["reasoning_capability"], "none")
            self.assertEqual(result["reasoning_efforts"], [])
    def test_rejects_model_without_mtp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.gguf"
            write_fixture(path, nextn_layers=None, nextn_tensor=False)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "unavailable")
            self.assertEqual(result["mtp_layers"], 0)

    def test_detects_mtp_tensor_in_later_split_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "model-00001-of-00002.gguf"
            second = root / "model-00002-of-00002.gguf"
            write_fixture(first, nextn_layers=1, nextn_tensor=False)
            write_fixture(second, nextn_layers=1, nextn_tensor=True)
            result = manager.inspect_gguf_capabilities(str(first))
            self.assertEqual(result["mtp_capability"], "available")
            self.assertEqual(result["mtp_layers"], 1)
            self.assertEqual(result["mtp_tensor_count"], 1)
    def test_marks_declared_but_missing_mtp_as_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.gguf"
            write_fixture(path, nextn_layers=1, nextn_tensor=False)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "incomplete")


if __name__ == "__main__":
    unittest.main()
