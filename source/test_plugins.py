"""Проверка импорта плагинов и управления ими, без внешних сетевых сервисов."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import db
import plugins


def archive(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return 'data:application/zip;base64,' + base64.b64encode(data.getvalue()).decode('ascii')


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = (db.DATA, db.DB_PATH, db._conn)
        db.DATA = Path(self.temp.name)
        db.DB_PATH = db.DATA / 'studio.db'
        db._conn = None
        db.connect()

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db.DATA, db.DB_PATH, db._conn = self.original
        self.temp.cleanup()

    def test_import_toggle_and_prompt(self):
        source = archive({
            'plugin/.claude-plugin/plugin.json': json.dumps({'name':'sample'}),
            'plugin/skills/check/SKILL.md':'# Инструкция для агента',
            'plugin/commands/check.md':'Проверь результат',
            'plugin/hooks/hooks.json':'{}',
            'plugin/.mcp.json':json.dumps({'mcpServers':{'demo':{'command':'python', 'args':['demo.py']}}}),
        })
        parsed = plugins.inspect_archive('sample.zip', source)
        self.assertEqual(2, len(parsed['docs']))
        self.assertEqual(1, len(parsed['servers']))
        self.assertTrue(parsed['warnings'])
        row = plugins.install('user', parsed)
        self.assertFalse(row['enabled'])
        self.assertEqual('', plugins.prompt('user'))
        self.assertEqual([], db.enabled_mcp_servers('user'))
        db.toggle_plugin(row['id'], True)
        self.assertIn('Инструкция для агента', plugins.prompt('user'))
        self.assertEqual(1, len(db.enabled_mcp_servers('user')))
        self.assertEqual('', plugins.prompt('other'))
        db.toggle_plugin(row['id'], False)
        self.assertEqual('', plugins.prompt('user'))
        self.assertEqual([], db.enabled_mcp_servers('user'))
        db.delete_plugin(row['id'])
        self.assertEqual([], db.plugin_servers(row['id']))

    def test_unsafe_zip_rejected(self):
        with self.assertRaises(ValueError):
            plugins.inspect_archive('x.zip', archive({'../outside.md':'text'}))

    def test_unresolved_paths_are_not_enabled(self):
        value = json.dumps({'mcpServers':{'x':{'command':'node','args':['${CLAUDE_PLUGIN_ROOT}/run.js']}}})
        payload = base64.b64encode(value.encode()).decode()
        with self.assertRaises(ValueError):
            plugins.inspect_archive('mcp.json', payload)

    def test_json_and_markdown(self):
        value = json.dumps({'mcpServers':{'x':{'url':'https://example.org/mcp','type':'http'}}})
        parsed = plugins.inspect_archive('mcp.json',base64.b64encode(value.encode()).decode())
        self.assertEqual('http', parsed['servers'][0]['transport'])
        md = plugins.inspect_archive('guide.md',base64.b64encode('Правила'.encode()).decode())
        self.assertEqual('Правила', md['docs'][0]['text'])


if __name__ == '__main__':
    unittest.main()
