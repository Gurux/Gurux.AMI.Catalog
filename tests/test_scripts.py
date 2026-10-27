import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from script_catalog import register_script

class ScriptCatalogTests(unittest.TestCase):
    def test_publish_immutable_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = b'// example'
            manifest = dict(schemaVersion=1, id='ReadMeter', version='1.0.0', fileName='ReadMeter.cs',
                description='Example', author='Gurux', license='GPL-2.0-only', amiVersionRange='[1.0.0,2.0.0)',
                sha256=hashlib.sha256(content).hexdigest(), size=len(content))
            payload = dict(manifest=manifest, content=base64.b64encode(content).decode())
            self.assertEqual(register_script(root, payload)[0], 201)
            self.assertEqual(register_script(root, payload)[0], 200)
            index = json.loads((root / 'scripts/script-index.json').read_text())
            self.assertEqual((root / 'scripts' / index['items'][0]['path']).read_bytes(), content)
            payload['manifest']['description'] = 'Changed metadata'
            with self.assertRaises(ValueError): register_script(root, payload)
            payload['manifest']['fileName'] = '../unsafe.cs'
            with self.assertRaises(ValueError): register_script(root, payload)

    def test_invalid_hash_and_binary_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = dict(schemaVersion=1, id='Test', version='1.0.0', fileName='Test.csx',
                description='Example', author='Gurux', license='MIT', amiVersionRange='*', sha256='0'*64, size=1)
            with self.assertRaises(ValueError): register_script(Path(temp), dict(manifest=manifest, content='/w=='))

class ScriptHttpTests(unittest.TestCase):
    def test_cli_publish_download_update_and_reject(self):
        import subprocess
        import threading
        import urllib.request
        from serve_catalog import create_server, localization_validator
        try:
            tool = localization_validator()
        except RuntimeError:
            self.skipTest('Set GURUX_LOCALIZATION_VALIDATOR or build Gurux.Updater.Tool for CLI integration')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / 'sources.json'
            sources.write_text(json.dumps({'items': [dict(id='Test.Config', type='configuration', repository='Gurux/Test', description='Test configuration', indexUrl='https://example.com/index.json')]}))
            with create_server(sources, root / 'site', port=0) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://localhost:{server.server_port}/scripts/script-index.json'
                    # Register the index through the existing shared-catalog mechanism.
                    from build_catalog import generate_catalog
                    data = json.loads(sources.read_text())
                    data['items'].append(dict(id='Gurux.AMI.Scripts', type='configuration', repository='Gurux/Gurux.AMI.Catalog', description='AMI scripts', indexUrl=url))
                    sources.write_text(json.dumps(data))
                    server.registry.catalog = generate_catalog(sources, root / 'site/catalog.json')
                    def cli(*args, success=True):
                        p = subprocess.run(['dotnet', str(tool), 'script', *args], capture_output=True, text=True, timeout=30)
                        self.assertEqual(p.returncode == 0, success, p.stdout + p.stderr)
                        return json.loads(p.stdout) if success else p.stderr
                    script = root / 'Test.cs'
                    manifest = root / 'Test.json'
                    def publish(version, text):
                        content = text.encode()
                        script.write_bytes(content)
                        manifest.write_text(json.dumps(dict(schemaVersion=1, id='Test', version=version, fileName='Test.cs', description='HTTP test', author='Gurux', license='MIT', amiVersionRange='[1.0.0,2.0.0)', sha256=hashlib.sha256(content).hexdigest(), size=len(content))))
                        cli('publish', '--manifest', str(manifest), '--file', str(script), '--catalog-url', url)
                    publish('1.0.0', '// original')
                    self.assertEqual(cli('list', '--catalog-url', f'http://localhost:{server.server_port}/catalog.json')[0]['version'], '1.0.0')
                    # Direct publication must use the same NuGet validation as consumers.
                    for changes in ({'version': '2147483648.0'}, {'amiVersionRange': '[2.0,1.0)'}):
                        invalid = json.loads(manifest.read_text())
                        invalid.update(changes)
                        body = json.dumps(dict(manifest=invalid, content=base64.b64encode(script.read_bytes()).decode())).encode()
                        request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(request, timeout=30)
                        self.assertEqual(caught.exception.code, 400)
                        caught.exception.close()
                    self.assertEqual(len(cli('list', '--catalog-url', url)), 1)
                    destination = root / 'installed.cs'
                    cli('download', '--catalog-url', url, '--id', 'Test', '--output', str(destination), '--ami-version', '1.0.0')
                    destination.write_text('// user edit')
                    self.assertTrue(cli('status', '--file', str(destination))['modified'])
                    publish('1.1.0', '// updated')
                    cli('update', '--file', str(destination), '--ami-version', '1.0.0', success=False)
                    self.assertEqual(destination.read_text(), '// user edit')
                    backup = cli('reject', '--file', str(destination))['backup']
                    self.assertEqual(Path(backup).read_text(), '// user edit')
                    self.assertEqual(destination.read_text(), '// original')
                    self.assertTrue(cli('update', '--file', str(destination), '--ami-version', '1.0.0')['updated'])
                    self.assertEqual(destination.read_text(), '// updated')
                    self.assertFalse(cli('update', '--file', str(destination), '--ami-version', '1.0.0')['updated'])
                    cli('download', '--catalog-url', url, '--id', 'Test', '--output', str(destination), '--ami-version', '1.0.0', success=False)
                    destination.write_text('// explicitly discarded edits')
                    publish('1.2.0', '// third version')
                    self.assertTrue(cli('update', '--file', str(destination), '--ami-version', '1.0.0', '--discard-local-changes')['updated'])
                    self.assertEqual(destination.read_text(), '// third version')
                    self.assertTrue(any(p.read_text() == '// explicitly discarded edits' for p in root.glob('installed.cs.*.bak')))
                    destination.unlink()
                    cli('reject', '--file', str(destination))
                    self.assertEqual(destination.read_text(), '// third version')
                    # Uploaded releases survive construction of a new server registry.
                    server.shutdown()
                    thread.join(timeout=5)
                    with create_server(sources, root / 'site', port=0) as restarted:
                        index = json.loads((root / 'site/scripts/script-index.json').read_text())
                        self.assertEqual(len(index['items']), 3)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
