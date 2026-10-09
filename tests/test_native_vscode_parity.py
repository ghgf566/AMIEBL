"""Differential verification of Rust VS Code migration against v1.0 Python.

Compare both output values and saved on-disk side effects, including preserves
of pre-existing custom providers, user credentials and Agent instructions.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import vscode_integration as legacy

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "native-core" / "target" / "debug" / (
    "vscode-probe.exe" if os.name == "nt" else "vscode-probe"
)

BASE = {
    "api_port": 8080, "default_model_id": "qwen",
    "models": [{"id": "qwen", "name": "Qwen", "context": 65536,
                "vision": True, "mmproj": "mmproj.gguf"}],
    "profiles": [{"id": "coding", "name": "Coding", "max_tokens": 8192,
                  "agent_name": "Local Coding", "agent_sync_mode": "preserve",
                  "agent_description": "Focused local coding assistant.",
                  "agent_tools": ["execute", "read", "edit", "search", "web"],
                  "agent_instructions": "Work directly on coding tasks."}]
}


class RustVSCodeDifferentialTests(unittest.TestCase):
    def setUp(self):
        if not PROBE.is_file():
            self.skipTest("Rust VS Code probe not built; CI builds and reruns this test.")

    def run_rust(self, op, **payload):
        r = subprocess.run(
            [str(PROBE)], input=json.dumps({"op": op, **payload}, ensure_ascii=False),
            text=True, capture_output=True, encoding="utf-8", timeout=15, check=True,
        )
        return json.loads(r.stdout)

    def compare_pure(self, op, expected, **payload):
        result = self.run_rust(op, **payload)
        self.assertEqual(result, {"ok": True, "result": expected})

    def test_jsonc_urls_comments_trailing_commas_and_utf8(self):
        for content in [
            '[ // comment\n{"url":"http://localhost","secret":"a/*b*/,]\\\"",},]',
            '[{"name":"中文 // comment", "value":"https://github.com/a/*b*/",},]',
            '[ /* first */ { "id" : 123, /* middle */ "other": [true, false,], } ]',
        ]:
            with self.subTest(text=content):
                self.compare_pure("jsonc", legacy.read_jsonc(content), text=content)
        malformed = '[/* unterminated'
        response = self.run_rust("jsonc", text=malformed)
        self.assertEqual(response, {"ok": False, "error": "VS Code 設定含有未結束的註解。"})

    def test_model_entries_all_reasoning_modes_and_profile_limits(self):
        for capability, reasoning_supported in [
            ("toggle", False), ("always", False), ("none", True),
            ("unknown", True), (None, True), (None, False)
        ]:
            conf = copy.deepcopy(BASE)
            conf["profiles"][0]["max_tokens"] = 1048576
            conf["models"][0]["context"] = 512
            conf["models"][0]["reasoning_supported"] = reasoning_supported
            if capability is not None:
                conf["models"][0]["reasoning_capability"] = capability
            with self.subTest(mode=capability, legacy=reasoning_supported):
                self.compare_pure(
                    "model_entries", legacy.model_entries(conf), config=conf
                )

    def test_preview_matches_full_python_result(self):
        conf = copy.deepcopy(BASE)
        conf["profiles"][0]["name"] = "全功能模式"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_file = root / "chatLanguageModels.json"
            agents_dir = root / "agents"
            with patch.object(legacy, "locations", return_value=(model_file, agents_dir)):
                expected = legacy.preview(conf, root / "data")
            self.compare_pure(
                "preview", expected, config=conf,
                models_file=str(model_file), agents_dir=str(agents_dir)
            )

    def test_agent_render_name_and_preserved_crlf(self):
        profile = copy.deepcopy(BASE["profiles"][0])
        profile["name"] = "全功能模式"
        self.compare_pure(
            "render_agent", legacy.render_agent(profile), profile=profile
        )
        self.compare_pure(
            "agent_name", legacy.agent_display_name(profile), profile=profile
        )
        cases = [
            "---\r\nname: Quick Chat\r\ntools: [execute, custom/tool]\r\ncustom-option: yes\r\n---\r\n\r\nAMIEBL_PROFILE:coding\r\n\r\nKeep instructions.\r\n",
            "---\nname: Old\nmodel: custom\ntools: [web]\n---\nMy text\n",
            "---\nname: Old\ntools: [read]\n---\nSome text\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                expected = legacy.preserve_agent_profile_marker(text, "coding", "全功能模式")
                self.compare_pure(
                    "preserve", expected, text=text,
                    profile_id="coding", display_name="全功能模式"
                )
        for text in (
            "---\nname: |\n  My custom name\ntools: [read]\n---\nPrompt.\n",
            "not yaml",
        ):
            with self.assertRaises(ValueError) as expected:
                legacy.preserve_agent_profile_marker(text, "coding", "Name")
            response = self.run_rust("preserve", text=text, profile_id="coding", display_name="Name")
            self.assertEqual(response, {"ok": False, "error": str(expected.exception)})

    def test_frontmatter_rewriter_preserves_model_and_header(self):
        cases = [
            "---\nname: Test\nmodel: \"old\"\nother: keep\n---\nText.\n",
            "---\r\nname: Test\r\nmodel: \"old\"\r\n---\r\nText.\r\n",
        ]
        for source in cases:
            for name in (None, "New Model"):
                with self.subTest(text=source, model=name):
                    expected = legacy.update_frontmatter(source, name)
                    self.compare_pure("frontmatter", expected, text=source, model_name=name)

    def compare_apply(self, source_json, agent_text=None, profiles=None):
        config = copy.deepcopy(BASE)
        if profiles is not None:
            config["profiles"] = profiles
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            roots = [Path(a), Path(b)]
            results = []
            saved = []
            for index, root in enumerate(roots):
                settings = root / "chatLanguageModels.json"
                agents = root / "agents"
                agents.mkdir()
                settings.write_text(source_json, encoding="utf-8")
                if agent_text is not None:
                    (agents / "local-coding.agent.md").write_bytes(agent_text.encode("utf-8"))
                data = root / "data"
                if index == 0:
                    with patch.object(legacy, "locations", return_value=(settings, agents)):
                        result = legacy.apply(config, data)
                else:
                    response = self.run_rust("apply", config=config,
                        models_file=str(settings), agents_dir=str(agents), data_dir=str(data))
                    self.assertTrue(response["ok"], response)
                    result = response["result"]
                results.append(result)
                saved.append({
                    "settings": json.loads(settings.read_text(encoding="utf-8")),
                    "agents": {p.name: p.read_bytes() for p in agents.glob("*.agent.md")},
                    "owned": json.loads((data / "vscode-owned-models.json").read_text(encoding="utf-8")),
                    "backup_files": sorted(
                        p.name for p in (data / "backups").rglob("*")
                        if p.is_file()
                    ),
                })
            self.assertEqual(saved[0], saved[1])
            self.assertEqual(results[0]["model_count"], results[1]["model_count"])
            self.assertEqual(results[0]["agent_count"], results[1]["agent_count"])
            self.assertEqual(len(results[0]["backups"]), len(results[1]["backups"]))
            self.assertEqual(results[0]["requires_reload"], results[1]["requires_reload"])
            self.assertEqual(results[0]["message"], results[1]["message"])
            self.assertEqual(config, copy.deepcopy(config))

    def test_apply_new_and_existing_custom_providers(self):
        self.compare_apply("[]")
        self.compare_apply(
            '[{"name":"Other","vendor":"other","secret":"unchanged"},'
            '{"name":"llama.cpp","vendor":"customendpoint","apiKey":"retain-me",'
            '"models":[{"id":"unrelated"},{"id":"qwen::coding"}]}]',
            agent_text="---\r\nname: Local Coding\r\ntools: [execute, custom/tool]\r\n---\r\n\r\nAMIEBL_PROFILE:coding\r\n\r\nMy instructions.\r\n",
        )

    def test_apply_managed_agent_and_unicode_profile(self):
        conf = copy.deepcopy(BASE["profiles"][0])
        conf.update(name="全功能模式", agent_sync_mode="managed",
                    agent_instructions="請保留使用者寫的內容。")
        self.compare_apply("[]", profiles=[conf])

if __name__ == "__main__":
    unittest.main()
