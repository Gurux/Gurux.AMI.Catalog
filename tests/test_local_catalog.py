import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import build_catalog


class LocalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'Module test.zip'
        with zipfile.ZipFile(self.package, 'w') as archive:
            archive.writestr('module.txt', 'local module')
        self.source = {'id': 'Module', 'type': 'module', 'source': 'local',
                       'releases': [{'version': '1.0.0', 'package': self.package.name,
                                     'publishedAt': '2026-01-01T00:00:00Z'}]}
        self.output = self.root / 'site'

    def build(self, source=None, **options):
        return build_catalog({'items': [source or self.source]},
                             lambda repo: self.fail('Local sources must not query GitHub'),
                             source_directory=self.root, output_directory=self.output,
                             base_url='http://localhost:8000', **options)

    def test_copies_package_and_generates_downloadable_release(self):
        result = self.build()
        release = result['items'][0]['releases'][0]
        self.assertEqual(release['version'], '1.0.0')
        self.assertEqual(release['tagName'], 'v1.0.0')
        self.assertFalse(release['isPrerelease'])
        asset = release['assets'][0]
        self.assertTrue(asset['downloadUrl'].startswith('http://localhost:8000/packages/'))
        self.assertIn('Module%20test.zip', asset['downloadUrl'])
        self.assertEqual(asset['size'], self.package.stat().st_size)
        self.assertEqual(asset['digest'], 'sha256:' + hashlib.sha256(self.package.read_bytes()).hexdigest())
        self.assertEqual(next(self.output.rglob('*.zip')).read_bytes(), self.package.read_bytes())

    def test_preserves_exact_assembly_references(self):
        references = [{'name': 'Gurux.Host.dll', 'version': '1.2.3.4'}]
        self.source['releases'][0]['references'] = references
        self.assertEqual(self.build()['items'][0]['releases'][0]['references'], references)

    def test_generates_local_application_zip_and_release_metadata(self):
        from build_catalog import generate_catalog
        source = {**self.source, 'id': 'Example.Application', 'type': 'application',
                  'releases': [*self.source['releases'],
                               {'version': '2.0.0-beta.1', 'package': self.package.name,
                                'publishedAt': '2026-02-01T00:00:00Z',
                                'releaseNotes': 'Local application preview'}]}
        sources_path = self.root / 'applications.json'
        sources_path.write_text(json.dumps({'items': [source]}), encoding='utf-8')
        catalog_path = self.output / 'catalog.json'
        generate_catalog(sources_path, catalog_path, count=1, base_url='http://localhost:8000')
        item = json.loads(catalog_path.read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(item['id'], 'Example.Application')
        self.assertEqual(item['type'], 'application')
        self.assertEqual(len(item['releases']), 1)
        release = item['releases'][0]
        self.assertEqual(release['version'], '2.0.0-beta.1')
        self.assertEqual(release['tagName'], 'v2.0.0-beta.1')
        self.assertTrue(release['isPrerelease'])
        self.assertEqual(release['publishedAt'], '2026-02-01T00:00:00Z')
        self.assertEqual(release['releaseNotes'], 'Local application preview')
        asset = release['assets'][0]
        digest = hashlib.sha256(self.package.read_bytes()).hexdigest()
        self.assertEqual(asset['downloadUrl'],
                         f'http://localhost:8000/packages/{digest}/Module%20test.zip')
        self.assertEqual(asset['digest'], 'sha256:' + digest)
        self.assertEqual(asset['size'], self.package.stat().st_size)
        self.assertEqual((self.output / 'packages' / digest / self.package.name).read_bytes(),
                         self.package.read_bytes())
        generate_catalog(sources_path, catalog_path, count=1, include_prereleases=False,
                         base_url='http://localhost:8000')
        stable = json.loads(catalog_path.read_text(encoding='utf-8'))['items'][0]['releases']
        self.assertEqual([release['version'] for release in stable], ['1.0.0'])
        self.assertFalse(stable[0]['isPrerelease'])

    def test_local_releases_use_same_sorting_count_and_prerelease_rules(self):
        source = {**self.source, 'releases': [
            *self.source['releases'],
            {'version': '2.0.0-beta', 'package': self.package.name, 'isPrerelease': True,
             'publishedAt': '2026-02-01T00:00:00Z'}]}
        self.assertEqual(self.build(source, count=1)['items'][0]['releases'][0]['version'], '2.0.0-beta')
        stable = self.build(source, include_prereleases=False)
        self.assertEqual([r['version'] for r in stable['items'][0]['releases']], ['1.0.0'])

    def test_missing_and_invalid_packages_fail(self):
        for package in ('missing.zip', 'invalid.zip'):
            with self.subTest(package=package):
                (self.root / 'invalid.zip').write_text('not a zip')
                source = {**self.source, 'releases': [{'version': '1.0.0', 'package': package}]}
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.build(source)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            build_catalog({'items': [{**self.source, 'source': 'typo'}]}, lambda repo: [])

    def test_mixed_github_and_local_sources(self):
        github = {'id': 'Agent', 'type': 'agent', 'source': 'github',
                  'repository': 'Gurux/Agent', 'assetPattern': '*.zip', 'description': 'Test agent'}
        catalog = build_catalog({'items': [self.source, github]}, lambda repo: [
            {'tag_name': 'v2.0.0', 'published_at': '2026-02-01T00:00:00Z',
             'assets': [{'name': 'agent.zip', 'size': 42,
                         'browser_download_url': 'https://example.test/agent.zip'}]}],
            source_directory=self.root, output_directory=self.output, base_url='http://localhost:8000',
            fetch_commits=lambda repo, base, head: [])
        self.assertEqual([item['id'] for item in catalog['items']], ['Module', 'Agent'])
        self.assertEqual(catalog['items'][1]['releases'][0]['assets'][0]['downloadUrl'],
                         'https://example.test/agent.zip')

    def test_failed_generation_preserves_existing_catalog(self):
        from build_catalog import generate_catalog
        self.output.mkdir()
        catalog = self.output / 'catalog.json'
        catalog.write_text('original')
        sources = self.root / 'sources.json'
        source = {**self.source, 'releases': [{'version': '1.0.0', 'package': 'missing.zip'}]}
        sources.write_text(json.dumps({'items': [source]}))
        with self.assertRaises(FileNotFoundError):
            generate_catalog(sources, catalog, base_url='http://localhost:8000')
        self.assertEqual(catalog.read_text(), 'original')

    def test_server_serves_catalog_and_zip_with_matching_urls(self):
        from serve_catalog import create_server
        sources = self.root / 'sources.json'
        sources.write_text(json.dumps({'items': [self.source]}))
        server = create_server(sources, self.output, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://localhost:{server.server_port}/catalog.json'
            with urllib.request.urlopen(url) as response:
                catalog = json.load(response)
            asset = catalog['items'][0]['releases'][0]['assets'][0]
            with urllib.request.urlopen(asset['downloadUrl']) as response:
                self.assertEqual(response.read(), self.package.read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_resolves_packages_relative_to_sources_file(self):
        sources = self.root / 'sources.json'
        sources.write_text(json.dumps({'items': [self.source]}))
        script = Path(__file__).resolve().parents[1] / 'scripts/build_catalog.py'
        result = subprocess.run([sys.executable, str(script), '--sources', str(sources),
                                 '--output', str(self.output / 'catalog.json'),
                                 '--base-url', 'http://localhost:8000'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / 'catalog.json').read_text())['items'][0]['id'], 'Module')


if __name__ == '__main__':
    unittest.main()
