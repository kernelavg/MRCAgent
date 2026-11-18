"""Тесты обнаружения своего демона и повторного запуска, без реальной Windows GUI."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import app

ROOT = Path(__file__).resolve().parent


class StartupTests(unittest.TestCase):
    def test_find_daemon_ignores_foreign_instances(self):
        records = {8777: {'ok': True, 'instance': 'foreign'}, 8778: {'ok': True, 'instance': 'ours'}}
        with patch.object(app, 'my_instance', return_value='ours'), patch.object(app, 'health_info', side_effect=lambda p: records.get(p)):
            port, info = app.find_daemon_port(prefer=8777)
        self.assertEqual(port, 8778)
        self.assertEqual(info['instance'], 'ours')

    def test_stop_refuses_foreign_instance(self):
        with patch.object(app, 'my_instance', return_value='ours'), patch.object(app, 'health_info', return_value={'ok':True, 'instance':'other'}), patch.object(urllib.request, 'urlopen') as send:
            self.assertFalse(app.stop_daemon(12345))
            send.assert_not_called()

    def test_wait_for_correct_instance(self):
        with patch.object(app, 'health_info', return_value={'ok':True, 'instance':'other'}):
            self.assertFalse(app.wait_health(12345, seconds=0.01, instance='ours'))

    def test_background_reopen_and_stop(self):
        with tempfile.TemporaryDirectory(prefix='mrc-startup-') as folder:
            env = dict(os.environ); env['MS_DATA'] = folder; env['PYTHONIOENCODING'] = 'utf-8'; env['MRC_NONINTERACTIVE'] = '1'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            cmd = [sys.executable, str(ROOT / 'app.py')]
            def run(args):
                return subprocess.run(cmd + args, env=env, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=45)
            try:
                first = run(['--no-open', '--no-resume', '--port', str(port)])
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                with urllib.request.urlopen('http://127.0.0.1:%d/' % port, timeout=5) as resp:
                    self.assertIn(b'MRC Agent', resp.read())
                again = run(['--no-open', '--no-resume'])
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                self.assertIn('уже работает', again.stdout)
                self.assertEqual(Path(folder, 'daemon.port').read_text().strip(), str(port))
            finally:
                run(['--stop'])
            time.sleep(0.8)
            with self.assertRaises(Exception):
                urllib.request.urlopen('http://127.0.0.1:%d/api/health' % port, timeout=1)


if __name__ == '__main__':
    unittest.main()
