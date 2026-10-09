"""Execute identical inference policies against frozen Python and native Rust.

No engine/model weights are used. This verifies pre-inference policy only;
it does not claim HTTP/SSE, queue, cancellation, or GUI migration is complete.
"""
import asyncio
import copy
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "native-core/target")) / "debug" / ("request-probe.exe" if os.name == "nt" else "request-probe")
spec = importlib.util.spec_from_file_location("amiebl_request_reference", ROOT / "backend/manager.py")
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)


class NativeRequestDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PROBE.is_file():
            if os.environ.get("AMIEBL_REQUIRE_NATIVE_TESTS") == "1":
                raise AssertionError("Required native request-probe is missing")
            raise unittest.SkipTest("Compile request-probe before native differential tests")
        source = (ROOT / "backend/manager.py").read_text(encoding="utf-8-sig").encode("utf-8")
        # Hash characterized from v1.0.0^{commit} eab5dc1451bf38d1c008558ba8d49c85954c04bb.
        # Normalize checkout line endings with read_text(), so Windows/Linux agree.
        if hashlib.sha256(source).hexdigest() != "0e91761e818b8974d3a0b455eaa4e9a3d6e36ff71052b216361eac57e75d79e7":
            raise AssertionError("Python request oracle differs from the frozen v1.0.0 baseline")
        # Avoid scanning any developer model directory; these fixtures are JSON only.
        cls.config = legacy.validate_config({
            "schema_version": 1, "models": [{
                "id": "m", "name": "測試模型", "path": "missing-model.gguf", "context": 8192,
            }], "default_model_id": "m",
            "profiles": [
                {"id": "quick-chat", "name": "Quick Chat", "thinking_mode": "auto", "reasoning_level": "light", "max_tokens": 4096},
                {"id": "coding", "name": "Coding", "thinking_mode": "auto", "reasoning_level": "balanced", "max_tokens": 8192},
                {"id": "deep-coding", "name": "Deep Coding", "thinking_mode": "auto", "reasoning_level": "extreme", "max_tokens": 12288},
            ], "default_profile_id": "coding", "log_request_bodies": False,
        })
        cls.comparisons = 0

    @classmethod
    def tearDownClass(cls):
        print(f"Native request parity: {cls.comparisons} complete Python/Rust comparisons", flush=True)

    def rust(self, **request):
        proc = subprocess.run([str(PROBE)], input=json.dumps(request, ensure_ascii=False),
                              text=True, encoding="utf-8", capture_output=True, timeout=15, check=True)
        return json.loads(proc.stdout)

    def compare(self, body=None, *, config=None, headers=None):
        config = copy.deepcopy(config or self.config)
        body = copy.deepcopy(body if body is not None else {"messages": [{"role": "user", "content": "你好"}]})
        headers = headers or {}
        # submit() supplies the real validation and initial record schema, without
        # starting reference worker tasks or writing files/user configuration.
        manager = legacy.Manager.__new__(legacy.Manager)
        manager.config = config
        manager.accepting = True
        manager.stopping = asyncio.Event()
        manager.tickets = {}
        manager.queue = asyncio.Queue(maxsize=legacy.MAX_QUEUE)
        initial = None

        async def reference():
            nonlocal initial
            ticket = manager.submit(body, headers)
            initial = copy.deepcopy(ticket.record)
            result = await manager.policy(ticket)
            return {"body": result, "model": ticket.model, "profile": ticket.profile, "record": ticket.record}

        try:
            expected = {"ok": True, "result": asyncio.run(reference())}
        except ValueError as error:
            expected = {"ok": False, "error": str(error)}
        actual = self.rust(config=config, body=body, headers=headers, record=initial or {})
        if expected["ok"] and expected["result"]["record"]["classifier_seconds"] is not None:
            measured = actual["result"]["record"]["classifier_seconds"]
            self.assertIsInstance(measured, (int, float))
            self.assertTrue(math.isfinite(measured) and measured >= 0)
            # Wall time varies by language/machine; all other record fields match.
            actual["result"]["record"]["classifier_seconds"] = expected["result"]["record"]["classifier_seconds"]
        self.assertEqual(actual, expected)
        type(self).comparisons += 1
        return actual

    def test_validation_and_error_order(self):
        for body in [None, [], "text", {}, {"messages": []}, {"messages": "bad"},
                     {"messages": [None]}, {"messages": [{"role": 1}]}]:
            # Explicit None is sent directly to the validator through the probe.
            if body is None:
                self.assertEqual(self.rust(config=self.config, body=None, headers={}),
                                 {"ok": False, "error": "messages 必須是非空陣列。"})
            else:
                with self.subTest(body=body): self.compare(body)
        base = {"messages": [{"role": "user", "content": "hello"}]}
        for key, values in {
            "max_tokens": [None, True, "8", 0, -1, 1, 1.0, 8.5, 1048576, 1048577],
            "max_completion_tokens": [False, 0, 2.0, 2.5],
            "n_predict": [-1, 0, 10, "10"], "stream": [None, 0, "true", False, True],
            "n": [None, False, True, 0, 1, 1.0, 2, "1"],
            "chat_template_kwargs": [None, [], "bad", {}, {"other": 1}],
            "model": [None, False, 0, 1, True, [], ["m"], {}, "missing", "m::bad", "m::coding::extra"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value): self.compare({**base, key: value})
        self.compare({**base, "stream": "bad", "max_tokens": -1, "n": 2})

    def test_profile_precedence_and_message_payloads(self):
        instructions = [
            ("system", "AMIEBL_PROFILE: quick-chat"), ("developer", "amiebl_profile : deep-coding"),
            ("user", "AMIEBL_PROFILE: quick-chat"), ("system", "AMIEBL_PROFILE: missing"),
            ("system", "Local Deep Coding"), ("system", "Perform deep analysis"),
            ("system", "Answer the user's question directly and concisely"),
            ("system", "Work directly on the user's requested coding task"),
            ("system", [{"type": "text", "text": "AMIEBL_PROFILE: quick-chat"}, {"type": "image_url", "image_url": {"url": "data:fixture"}}]),
            ("developer", [{"text": 3}, {"text": "AMIEBL_PROFILE: deep-coding"}]),
            ("system", "AMIEBL_PROFILE\x1c:\x1fquick-chat"),
            ("system", "AMİEBL_PROFıLE: quick-chat"),
            ("developer", "AMIEBL_PROFILE: Koding"),
        ]
        for (role, content), alias, header in itertools.product(instructions, ["m", "m::", "m::coding"], [None, "quick-chat"]):
            body = {"model": alias, "messages": [{"role": role, "content": content},
                {"role": "user", "content": [{"type": "text", "text": "翻譯這段話"}, {"type": "image_url", "image_url": {"url": "data:fixture"}}]}],
                "tools": [{"type": "function", "function": {"name": "工具"}}],
                "tool_choice": "auto", "future_extension": {"preserve": [1, "中文"]}}
            with self.subTest(role=role, alias=alias, header=header):
                self.compare(body, headers={} if header is None else {"x-llm-profile": header})
        config = copy.deepcopy(self.config)
        config["models"][0]["default_profile_id"] = "quick-chat"
        self.compare(config=config)
        config["models"][0]["default_profile_id"] = ""
        self.compare(config=config)

    def test_capabilities_modes_levels_and_sampler_matrix(self):
        for capability, mode, level, custom in itertools.product(
                ["unknown", "none", "toggle", "always"], ["model", "off", "on", "auto"],
                ["light", "balanced", "deep", "extreme"], [False, True]):
            config = copy.deepcopy(self.config)
            model = config["models"][0]
            model.update(reasoning_capability=capability, reasoning_budget_supported=True,
                         reasoning_toggle_keys=["enable_thinking", "thinking", "thinking_mode", "add_nothink_token"],
                         temperature=0.0, top_p=0.8, top_k=40, min_p=None)
            p = config["profiles"][1]
            p.update(thinking_mode=mode, reasoning_level=level, budget_mode="custom" if custom else "auto", thinking_budget=4096)
            body = {"model": "m", "messages": [{"role": "user", "content": "分析並翻譯這個程式錯誤"}],
                    "max_tokens": 1000, "max_completion_tokens": 800, "n_predict": 1200,
                    "temperature": 0.9, "top_p": 0.9, "top_k": 10, "min_p": 0.1}
            with self.subTest(capability=capability, mode=mode, level=level, custom=custom):
                self.compare(body, config=config)

    def test_explicit_client_overrides_and_budget_precedence(self):
        config = copy.deepcopy(self.config)
        config["models"][0].update(reasoning_capability="toggle", reasoning_toggle_keys=["enable_thinking"])
        config["profiles"][1].update(thinking_mode="off", budget_mode="custom", thinking_budget=50)
        for key, value in [
            ("reasoning_effort", "high"), ("reasoning_effort", None),
            ("reasoning", {"effort": "low"}), ("reasoning", "unknown-future-type"),
            *[(key, n) for key in ["thinking_budget_tokens", "reasoning_budget_tokens"]
              for n in [-2, -1, 0, 1, 1.0, 1.5, 1000000, 1048577, True, "10", None]],
            *[("chat_template_kwargs", {key: value}) for key, value in [
                ("enable_thinking", True), ("thinking", False), ("thinking_mode", "adaptive"),
                ("add_nothink_token", True), ("reasoning_effort", "max"), ("reasoning_strength", 3),
                ("thinking_budget", -1), ("thinking_budget_tokens", 500), ("custom", "保留")]],
        ]:
            with self.subTest(key=key, value=value):
                self.compare({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 100, key: value}, config=config)
        for cap in [1, 2, 3, 4, 255, 256, 1023, 1024, 1025]:
            self.compare({"messages": [{"role": "user"}], "max_tokens": cap,
                "reasoning_budget_tokens": -1, "thinking_budget_tokens": 1,
                "chat_template_kwargs": {"thinking_budget": 2, "thinking_budget_tokens": 3}}, config=config)
        self.compare({"messages": [{"role": "user"}], "reasoning_budget_tokens": None,
                      "thinking_budget_tokens": 99, "reasoning_effort": None, "reasoning": {"effort": "high"}}, config=config)

    def test_auto_classifier_latest_user_and_frozen_escapes(self):
        config = copy.deepcopy(self.config)
        config["models"][0].update(reasoning_capability="toggle", reasoning_budget_supported=True,
                                  reasoning_toggle_keys=["thinking"])
        for text in ["你好", "hello", "hello!", "hello there", "翻譯", "分析並翻譯", "debug this error",
                     "translate this", r"\bdebug\b", r"\btranslate\b", "", "  ", "\n你好\n", "\x1c你好\x1f"]:
            body = {"messages": [{"role": "system", "content": "架構分析除錯，AMIEBL_PROFILE: coding"},
                                 {"role": "user", "content": text}, {"role": "assistant", "content": "分析"}]}
            with self.subTest(text=text): self.compare(body, config=config)
        self.compare({"messages": [{"role": "user", "content": "翻譯"},
                                   {"role": "user", "content": []}]}, config=config)

    def test_native_effort_mapping_and_python_rounding(self):
        efforts = ["minimal", "low", "medium", "high", "xhigh", "max"]
        for level, supported, budget in itertools.product(
                ["light", "balanced", "deep", "extreme", "unknown"],
                [[], ["low", "medium", "xhigh"], efforts, ["bogus", "xhigh", "low"]],
                [0, 1, 5, 10, 15, 35, 100, 7744]):
            with self.subTest(level=level, supported=supported, budget=budget):
                self.assertEqual(self.rust(op="helpers", level=level, supported=supported, max_budget=budget),
                    {"ok": True, "result": {"effort": legacy.map_native_reasoning_effort(level, supported),
                                             "budget": legacy.auto_reasoning_budget(level, budget)}})
                type(self).comparisons += 1
        config = copy.deepcopy(self.config)
        for supported, supported_budget, mode, custom in itertools.product(
                [[], ["low", "medium", "xhigh"]], [False, True], ["on", "off"], [False, True]):
            config["models"][0].update(reasoning_capability="toggle", reasoning_efforts=supported,
                                      reasoning_budget_supported=supported_budget, reasoning_toggle_keys=["enable_thinking"])
            config["profiles"][1].update(thinking_mode=mode, reasoning_level="deep",
                                          budget_mode="custom" if custom else "auto", thinking_budget=4096)
            self.compare(config=config)

    def test_integral_float_settings_and_small_context(self):
        config = copy.deepcopy(self.config)
        config["models"][0].update(context=512.0, reasoning_capability="toggle", reasoning_budget_supported=True,
                                  reasoning_toggle_keys=["enable_thinking"])
        config["profiles"][1].update(max_tokens=8192.0, thinking_budget=1536.0,
                                      budget_mode="custom", thinking_mode="on")
        self.compare(config=config)


if __name__ == "__main__":
    unittest.main()
