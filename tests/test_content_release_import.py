import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_catalog import localization_validator
from import_content_releases import import_releases
from build_catalog import generate_catalog


class ContentReleaseImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.validator = str(localization_validator())
        languages = self.root / 'source/localization'
        resources = self.root / 'source/templates'
        languages.mkdir(parents=True)
        resources.mkdir()
        (languages / 'fi-FI.json').write_text('{"Title":"Laite"}', encoding='utf-8')
        (resources / 'default.txt').write_text('Exact resource\n', encoding='utf-8')
        sources = self.root / 'content-sources.json'
        sources.write_text(json.dumps({'sources': [
            {'kind': 'localization', 'directory': str(languages), 'owner': 'App', 'ownerType': 'application', 'defaultCulture': 'en'},
            {'kind': 'resources', 'directory': str(resources), 'owner': 'App', 'ownerType': 'application', 'name': 'templates'}]}), encoding='utf-8')
        history = self.root / 'history.json'
        history.write_text('[]', encoding='utf-8')
        output = subprocess.check_output(['dotnet', self.validator, 'content', 'prepare', '--sources', str(sources), '--history', str(history), '--output', str(self.root / 'artifacts')], text=True)
        self.prepared = json.loads(output)
        self.manifests = {item['tag']: json.loads(Path(item['manifest']).read_text(encoding='utf-8')) for item in self.prepared}
        self.allowlist = self.root / 'content-release-sources.json'
        self.allowlist.write_text('{"repositories":["Owner/Source"]}', encoding='utf-8')
        self.calls = []
        self.draft = False
        self.corrupt = False
        self.catalog = self.root / 'catalog'

    def runner(self, arguments):
        arguments = list(map(str, arguments))
        self.calls.append(arguments)
        if arguments[0] == 'dotnet':
            return subprocess.check_output(arguments, text=True)
        if arguments[:3] == ['gh', 'api', 'repos/Owner/Source/releases']:
            return json.dumps([[self.release(tag) for tag in self.manifests]])
        if arguments[:2] == ['gh', 'api'] and '/releases/tags/' in arguments[2]:
            from urllib.parse import unquote
            return json.dumps(self.release(unquote(arguments[2].split('/releases/tags/')[1])))
        if arguments[:2] == ['gh', 'api'] and '/git/ref/tags/' in arguments[2]:
            from urllib.parse import unquote
            return json.dumps({'object': {'type': 'tag', 'sha': unquote(arguments[2].split('/git/ref/tags/')[1])}})
        if arguments[:2] == ['gh', 'api'] and '/git/tags/' in arguments[2]:
            tag = arguments[2].split('/git/tags/')[1]
            return json.dumps({'message': json.dumps(self.manifests[tag])})
        if arguments[:3] == ['gh', 'release', 'download']:
            tag = arguments[3]
            item = next(item for item in self.prepared if item['tag'] == tag)
            name = arguments[arguments.index('--pattern') + 1]
            target = Path(arguments[arguments.index('--dir') + 1]) / name
            data = Path(item['manifest'] if name == 'release-manifest.json' else item['package']).read_bytes()
            target.write_bytes(data + b'corruption' if self.corrupt and name != 'release-manifest.json' else data)
            return ''
        raise AssertionError(arguments)

    def release(self, tag):
        return {'tag_name': tag, 'draft': self.draft, 'prerelease': False, 'assets': [
            {'name': name, 'state': 'uploaded'} for name in ('release-manifest.json', self.manifests[tag]['package'])]}

    def test_published_release_import_is_idempotent_and_pages_contains_both_package_paths(self):
        import_releases(self.allowlist, self.catalog, self.validator, runner=self.runner)
        before = (self.catalog / 'localization/localization-index.json').read_bytes()
        for tag in self.manifests:
            import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', tag, runner=self.runner)
        self.assertEqual(before, (self.catalog / 'localization/localization-index.json').read_bytes())
        configuration = self.catalog / 'catalog-sources.json'
        configuration.write_text(json.dumps({'items': [
            {'id': 'Localization', 'type': 'configuration', 'repository': 'Owner/Source', 'indexUrl': 'https://example.test/localization/localization-index.json'},
            {'id': 'Resources', 'type': 'configuration', 'repository': 'Owner/Source', 'indexUrl': 'https://example.test/resources/resource-index.json'}]}), encoding='utf-8')
        with patch('build_catalog.fetch_description', return_value=''):
            generate_catalog(configuration, self.root / '_site/catalog.json')
        for manifest in self.manifests.values():
            self.assertTrue((self.root / '_site' / manifest['kind'] / manifest['path']).is_file())

    def test_draft_or_unregistered_dispatch_does_not_change_catalog(self):
        with self.assertRaisesRegex(ValueError, 'not registered'):
            import_releases(self.allowlist, self.catalog, self.validator, 'Other/Repository', 'localization/App/fi-FI/v1.0.0', runner=self.runner)
        self.assertEqual([], self.calls)
        self.draft = True
        with self.assertRaisesRegex(ValueError, 'successfully published'):
            import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', 'localization/App/fi-FI/v1.0.0', runner=self.runner)
        self.assertFalse(self.catalog.exists())

    def test_corrupted_release_does_not_enter_catalog(self):
        self.corrupt = True
        with self.assertRaises(subprocess.CalledProcessError):
            import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', 'localization/App/fi-FI/v1.0.0', runner=self.runner)
        self.assertFalse((self.catalog / 'localization/localization-index.json').exists())
