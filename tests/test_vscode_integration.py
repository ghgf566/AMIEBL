import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import vscode_integration as integration


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models = self.root / 'chatLanguageModels.json'
        self.agents = self.root / 'agents'
        self.agents.mkdir()
        self.config = {'api_port': 8080, 'default_model_id': 'qwen', 'models': [
            {'id':'qwen','name':'Qwen','context':65536,'vision':True,'mmproj':'mmproj.gguf'}],
            'profiles':[{'id':'coding','name':'Coding','max_tokens':8192}]}
        self.loc = patch.object(integration, 'locations', return_value=(self.models, self.agents))
        self.loc.start()
        self.addCleanup(self.loc.stop)

    def test_jsonc_preserves_strings_and_strips_comments(self):
        source = '[ // a comment\n {"url":"http://localhost", "secret":"a/*b*/,]\\\"",},]'
        result = integration.read_jsonc(source)
        self.assertEqual(result[0]['url'], 'http://localhost')
        self.assertEqual(result[0]['secret'], 'a/*b*/,]"')

    def test_merge_backups_preserves_credentials_and_tools(self):
        providers = [{'name':'Other','vendor':'other','secret':'unchanged'},
            {'name':'llama.cpp','vendor':'customendpoint','apiKey':'retain-me','models':[{'id':'unrelated'}]}]
        self.models.write_text(json.dumps(providers), encoding='utf-8')
        agent = self.agents / 'local-coding.agent.md'
        original = '---\nname: Local Coding\ntools: [execute, custom/tool]\nmodel: "old custom endpoint"\n---\n\nKeep these instructions.\n'
        agent.write_text(original, encoding='utf-8')
        result = integration.apply(self.config, self.root / 'data')
        actual = json.loads(self.models.read_text(encoding='utf-8'))
        self.assertEqual(actual[0], providers[0])
        self.assertEqual(actual[1]['apiKey'], 'retain-me')
        self.assertEqual([m['id'] for m in actual[1]['models']], ['unrelated','qwen','qwen::coding'])
        self.assertEqual(actual[1]['models'][-1]['maxOutputTokens'], 8192)
        source = agent.read_text(encoding='utf-8')
        self.assertIn('tools: [execute, custom/tool]', source)
        self.assertIn('Keep these instructions.', source)
        self.assertNotRegex(source, r"(?m)^model:\s*")
        self.assertEqual(len(result['backups']), 2)
        self.assertEqual(Path(result['backups'][1]).read_text(encoding='utf-8'), original)
        self.assertNotIn('retain-me', json.dumps(result))

    def test_removes_only_previously_managed_aliases(self):
        integration.apply(self.config, self.root / 'data')
        second = copy.deepcopy(self.config)
        second['profiles'] = [{'id':'quick-chat','name':'Quick','max_tokens':4096}]
        integration.apply(second, self.root / 'data')
        entries = json.loads(self.models.read_text(encoding='utf-8'))[0]['models']
        self.assertEqual([m['id'] for m in entries], ['qwen', 'qwen::quick-chat'])

    def test_invalid_agent_does_not_partially_write_settings(self):
        self.models.write_text('[]', encoding='utf-8')
        (self.agents / 'local-coding.agent.md').write_text('not yaml', encoding='utf-8')
        with self.assertRaises(ValueError):
            integration.apply(self.config, self.root / 'data')
        self.assertEqual(self.models.read_text(encoding='utf-8'), '[]')

    def test_preview_does_not_touch_any_configuration(self):
        self.models.write_text('[]', encoding='utf-8')
        result = integration.preview(self.config, self.root / 'data')
        self.assertEqual(result['model_count'], 2)
        self.assertEqual(self.models.read_text(encoding='utf-8'), '[]')
        self.assertEqual(list(self.agents.iterdir()), [])
        self.assertFalse((self.root / 'data').exists())

    def test_invalid_profile_path_never_creates_files(self):
        self.models.write_text('[]', encoding='utf-8')
        config = copy.deepcopy(self.config)
        config['profiles'][0]['id'] = '../escape'
        with self.assertRaises(ValueError):
            integration.apply(config, self.root / 'data')
        self.assertEqual(self.models.read_text(encoding='utf-8'), '[]')
        self.assertEqual(list(self.agents.iterdir()), [])

    def test_write_failure_rolls_back_settings_and_agents(self):
        original_settings = '[{"name":"Other","vendor":"other"}]'
        self.models.write_text(original_settings, encoding='utf-8')
        agent = self.agents / 'local-coding.agent.md'
        original_agent = '---\nname: Local Coding\ntools: [read]\n---\nMy prompt.\n'
        agent.write_text(original_agent, encoding='utf-8')
        original_writer = integration.atomic_write
        injected = False

        def fail_agent_once(path, content):
            nonlocal injected
            if path == agent and not injected:
                injected = True
                raise OSError('fixture write failure')
            return original_writer(path, content)

        with patch.object(integration, 'atomic_write', side_effect=fail_agent_once):
            with self.assertRaises(OSError):
                integration.apply(self.config, self.root / 'data')
        self.assertEqual(self.models.read_text(encoding='utf-8'), original_settings)
        self.assertEqual(agent.read_text(encoding='utf-8'), original_agent)


if __name__ == '__main__':
    unittest.main()
