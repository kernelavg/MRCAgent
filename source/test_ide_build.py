import os
import time
import unittest
from unittest.mock import patch
import db
import ide_build
import tools


class IDETests(unittest.TestCase):
    def setUp(self):
        self.pid = db.create_project('ide-test-owner', 'IDE test', 'test')['id']
        self.root = tools.workspace_for(self.pid)
        self.sdk = patch.dict(os.environ, {'ANDROID_HOME': str(self.root / 'sdk')})
        self.sdk.start()
        self.addCleanup(self.sdk.stop)
        self.addCleanup(lambda: tools.drop_workspace(self.pid))
        self.addCleanup(lambda: db.delete_project(self.pid))

    def prepare(self, body):
        wrapper = self.root / 'gradle' / 'wrapper'
        wrapper.mkdir(parents=True)
        (wrapper / 'gradle-wrapper.jar').write_bytes(b'test fixture, not a real wrapper')
        (self.root / 'gradlew').write_text(body, encoding='utf-8')

    def await_build(self, bid):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            row = ide_build.get(self.pid, bid)
            if row['status'] != 'running': return row
            time.sleep(.05)
        self.fail('Сборка не закончилась')

    @unittest.skipIf(os.name == 'nt', 'POSIX mock wrapper for Linux test')
    def test_real_process_mock_android_artifact(self):
        self.prepare("""#!/bin/sh
python3 - <<'INNER'
import pathlib, zipfile
p=pathlib.Path('app/build/outputs/apk/debug/app-debug.apk'); p.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(p,'w') as z: z.writestr('AndroidManifest.xml','mock')
print('BUILD TEST OK')
INNER
""")
        build=ide_build.start(self.pid,'android');row=self.await_build(build['id'])
        self.assertEqual(row['status'],'done',row)
        self.assertEqual(row['artifacts'],['app/build/outputs/apk/debug/app-debug.apk'])
        self.assertIn('BUILD TEST OK',row['log'])

    @unittest.skipIf(os.name == 'nt', 'POSIX mock wrapper for Linux test')
    def test_no_artifact_is_failure(self):
        self.prepare('#!/bin/sh\necho no-artifact\nexit 0\n')
        build=ide_build.start(self.pid,'android');row=self.await_build(build['id'])
        self.assertEqual(row['status'],'error');self.assertIn('артефакт',row['message'])

    @unittest.skipIf(os.name == 'nt', 'POSIX mock wrapper for Linux test')
    def test_cancel_and_single_running(self):
        self.prepare('#!/bin/sh\necho waiting\nsleep 30\n')
        build=ide_build.start(self.pid,'android')
        with self.assertRaises(ValueError):ide_build.start(self.pid,'android')
        self.assertTrue(ide_build.stop(self.pid,build['id']))
        row=self.await_build(build['id']);self.assertEqual(row['status'],'cancelled')

    def test_diagnostics_missing_prerequisites(self):
        self.assertFalse(ide_build.diagnostics(self.pid,'android')['ready'])
        self.assertFalse(ide_build.diagnostics(self.pid,'bogus')['ready'])

    def test_github_workflow_does_not_overwrite(self):
        self.prepare('fixture')
        result=ide_build.export_workflow(self.pid,'android')
        file=self.root / result['path'];self.assertIn('assembleDebug',file.read_text())
        file.write_text('user custom workflow')
        with self.assertRaises(ValueError):ide_build.export_workflow(self.pid,'android')
        self.assertEqual(file.read_text(),'user custom workflow')

    def test_export_skips_secrets_and_personal_workspaces(self):
        import io
        import zipfile
        (self.root / 'app.py').write_text('test')
        (self.root / '.env').write_text('do not export')
        (self.root / 'local.properties').write_text('local path')
        (self.root / 'agents').mkdir()
        (self.root / 'agents' / 'private.txt').write_text('private')
        (self.root / '.github' / 'workflows').mkdir(parents=True)
        (self.root / '.github' / 'workflows' / 'mrcide-build.yml').write_text('workflow')
        with zipfile.ZipFile(io.BytesIO(ide_build.export_project(self.pid))) as z:
            self.assertIn('app.py', z.namelist())
            self.assertIn('.github/workflows/mrcide-build.yml', z.namelist())
            self.assertNotIn('.env', z.namelist())
            self.assertNotIn('local.properties', z.namelist())
            self.assertNotIn('agents/private.txt', z.namelist())

    def test_invalid_exe_is_not_artifact(self):
        (self.root / 'dist').mkdir()
        (self.root / 'dist' / 'broken.exe').write_bytes(b'MZ corrupted')
        self.assertEqual(ide_build._find_artifacts(self.root,'windows',time.time()-1),[])

    def test_ios_scheme_injection_rejected(self):
        (self.root / 'App.xcodeproj').mkdir()
        with self.assertRaises(ValueError):ide_build.export_workflow(self.pid,'ios_simulator','App; echo nope')


if __name__ == '__main__':unittest.main()
