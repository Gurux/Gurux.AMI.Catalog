"""Exercise the real shared C# publication engine across clean output directories."""
import concurrent.futures
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_catalog import CatalogRegistry, UploadError, localization_validator
from build_catalog import generate_catalog


class LanguagePackagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'catalog-sources.json'
        self.sources.write_text(json.dumps({'items': [{'id': 'Gurux.AMI.Localization', 'type': 'configuration',
            'repository': 'Gurux/Gurux.AMI.Catalog', 'indexUrl': 'https://example.org/localization/localization-index.json'}]}), encoding='utf-8')
        self.output = self.root / '_site'
        self.output.mkdir()
        self.registry = CatalogRegistry(self.sources, self.output, 10, True, 'http://localhost:8000', {})

    def snapshot(self, fi='Posti', culture='fi', owner='Gurux.AMI.SMTP', kind='module', **metadata):
        return json.dumps({'schemaVersion': 2, 'packages': [
            {'schemaVersion': 2, 'owner': owner, 'ownerType': kind, 'culture': name,
             'version': '1.0.0', 'defaultCulture': 'en', 'resources': resources, **metadata}
            for name, resources in [('en', {'Title': 'Mail'}), (culture, {'Title': fi})]]}).encode()

    def publish(self, body):
        return self.registry.register_localization(body)

    def index(self):
        return self.root / 'localization/localization-index.json'

    def test_initial_noop_independent_versions_and_restart_from_durable_history(self):
        self.publish(self.snapshot())
        before = self.index().read_bytes()
        stamp = self.index().stat().st_mtime_ns
        self.publish(self.snapshot())
        self.assertEqual(before, self.index().read_bytes())
        self.assertEqual(stamp, self.index().stat().st_mtime_ns)
        self.publish(self.snapshot(fi='Kirje'))
        releases = json.loads(self.index().read_text())['items'][0]['releases']
        self.assertEqual([(r['culture'], r['version']) for r in releases], [('en', '1.0.0'), ('fi', '1.0.0'), ('fi', '1.0.1')])
        shutil.rmtree(self.output)
        clean = self.root / 'clean-ci'
        with patch('build_catalog.fetch_description', return_value=''):
            generate_catalog(self.sources, clean / 'catalog.json')
        self.assertEqual(self.index().read_bytes(), (clean / 'localization/localization-index.json').read_bytes())
        for release in releases:
            data = (clean / 'localization' / release['path']).read_bytes()
            self.assertEqual(release['size'], len(data))
            self.assertEqual(release['sha256'], hashlib.sha256(data).hexdigest())
        self.publish(self.snapshot(fi='Kirje'))
        self.assertEqual(3, len(json.loads(self.index().read_text())['items'][0]['releases']))

    def test_concurrent_and_repeated_publication_is_idempotent(self):
        self.publish(self.snapshot())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.publish, [self.snapshot(fi='Kirje')] * 4))
        self.assertEqual(3, len(json.loads(self.index().read_text())['items'][0]['releases']))

    def test_invalid_batch_does_not_publish_any_language(self):
        self.publish(self.snapshot())
        before = self.index().read_bytes()
        invalid = self.snapshot(fi='Changed').replace(b'"Title": "Changed"', b'"Title": 42')
        with self.assertRaises(UploadError):
            self.publish(invalid)
        self.assertEqual(before, self.index().read_bytes())
        self.assertFalse((self.root / 'localization/packages/Gurux.AMI.SMTP/fi/1.0.1.json').exists())

    def test_application_and_regional_culture_share_publication_rules(self):
        self.publish(self.snapshot(culture='fi-FI', owner='GXDLMSDirector', kind='application'))
        releases = json.loads(self.index().read_text())['items'][0]['releases']
        self.assertEqual(['en', 'fi-FI'], [r['culture'] for r in releases])
        self.assertTrue(all(r['version'] == '1.0.0' for r in releases))

    def test_pages_missing_package_fails_without_replacing_catalog(self):
        self.publish(self.snapshot())
        old_catalog = self.output / 'catalog.json'
        old_catalog.write_bytes(b'previous catalog')
        (self.root / 'localization/packages/Gurux.AMI.SMTP/fi/1.0.0.json').unlink()
        with self.assertRaises((ValueError, RuntimeError, FileNotFoundError)):
            generate_catalog(self.sources, old_catalog)
        self.assertEqual(b'previous catalog', old_catalog.read_bytes())

    def test_pages_copy_uses_one_index_snapshot_during_concurrent_publication(self):
        from localization_catalog import copy_history, run_tool
        self.publish(self.snapshot())
        before = self.index().read_bytes()
        request = self.root / 'request.json'
        request.write_bytes(self.snapshot(fi='Kirje'))
        original_copy = shutil.copytree
        def copy_then_publish(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            publication = run_tool(['publish-snapshot', '--snapshot', request, '--index', self.index()])
            self.assertEqual(0, publication.returncode, publication.stderr)
            return result
        destination = self.root / 'concurrent-pages'
        with patch('localization_catalog.shutil.copytree', side_effect=copy_then_publish):
            copy_history(self.index().parent, destination)
        self.assertEqual(before, (destination / 'localization-index.json').read_bytes())
        for release in json.loads((destination / 'localization-index.json').read_text())['items'][0]['releases']:
            self.assertTrue((destination / release['path']).is_file())

    def test_source_cli_publishes_and_downloads_through_http_server(self):
        from serve_catalog import create_server
        from localization_catalog import run_tool
        with patch('build_catalog.fetch_description', return_value=''):
            server = create_server(self.sources, self.output, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            source = self.root / 'translations'
            source.mkdir()
            (source / 'fi-FI.json').write_text('{"Title":"Laite"}', encoding='utf-8')
            address = f'http://127.0.0.1:{server.server_port}/catalog.json'
            arguments = ['publish-sources', '--source', source, '--owner', 'GXDLMSDirector', '--owner-type', 'application',
                         '--default-culture', 'en', '--catalog-url', address, '--json']
            first = run_tool(arguments)
            self.assertEqual(0, first.returncode, first.stderr)
            repeat = run_tool(arguments)
            self.assertEqual(0, repeat.returncode, repeat.stderr)
            self.assertFalse(json.loads(repeat.stdout)['languages'][0]['changed'])
            download = run_tool(['download', '--catalog-url', address, '--owner', 'GXDLMSDirector', '--culture', 'fi-FI', '--output', self.root / 'downloads', '--json'])
            self.assertEqual(0, download.returncode, download.stderr)
            package = json.loads(Path(json.loads(download.stdout)['path']).read_text(encoding='utf-8'))
            self.assertEqual('fi-FI', package['culture'])
            self.assertEqual({'Title': 'Laite'}, package['resources'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_git_roundtrip_preserves_exact_legacy_package_bytes(self):
        from localization_catalog import run_tool
        body = b'{\r\n"schemaVersion":1,"owner":"App","ownerType":"application","version":"1.0.0","defaultCulture":"en","resources":{"en":{"Title":"Mail"}}\r\n}'
        self.publish(body)
        self.publish(self.snapshot())
        attributes = Path(__file__).resolve().parents[1] / '.gitattributes'
        shutil.copy2(attributes, self.root / '.gitattributes')
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True)
        git('init')
        git('config', 'core.autocrlf', 'true')
        git('add', '--', '.gitattributes', 'localization/localization-index.json', 'localization/packages')
        git('-c', 'user.name=Localization Test', '-c', 'user.email=localization@example.test', 'commit', '-m', 'Retain localization history')
        checkout = self.root / 'fresh-checkout'
        subprocess.run(['git', '-c', 'core.autocrlf=true', 'clone', str(self.root), str(checkout)], check=True, capture_output=True)
        self.assertEqual(body, (checkout / 'localization/packages/App/1.0.0.json').read_bytes())
        result = run_tool(['validate-store', '--index', checkout / 'localization/localization-index.json'])
        self.assertEqual(0, result.returncode, result.stderr)
        request = self.root / 'fresh-ci-request.json'
        request.write_bytes(self.snapshot())
        repeat = run_tool(['publish-snapshot', '--snapshot', request, '--index', checkout / 'localization/localization-index.json', '--json'])
        self.assertEqual(0, repeat.returncode, repeat.stderr)
        self.assertTrue(all(not language['changed'] for language in json.loads(repeat.stdout)['languages']))
        request.write_bytes(self.snapshot(fi='Kirje'))
        change = run_tool(['publish-snapshot', '--snapshot', request, '--index', checkout / 'localization/localization-index.json', '--json'])
        self.assertEqual(0, change.returncode, change.stderr)
        self.assertEqual([('en', '1.0.0'), ('fi', '1.0.1')], [(language['culture'], language['version']) for language in json.loads(change.stdout)['languages']])

    def test_publication_mirror_never_overwrites_conflicting_immutable_bytes(self):
        self.publish(self.snapshot())
        mirror = self.output / 'localization/packages/Gurux.AMI.SMTP/fi/1.0.0.json'
        mirror.write_bytes(b'conflicting package')
        with self.assertRaises(ValueError):
            self.publish(self.snapshot())
        self.assertEqual(b'conflicting package', mirror.read_bytes())
        self.assertIn('fi', self.index().read_text())
