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

    def test_legacy_content_requires_matching_annotated_manifest(self):
        for mode in ('lightweight', 'changed-annotation'):
            def runner(arguments):
                args = list(map(str, arguments))
                if '/git/ref/tags/' in args[2] and mode == 'lightweight':
                    return json.dumps({'object': {'type': 'commit', 'sha': 'commit'}})
                if '/git/tags/' in args[2] and mode == 'changed-annotation':
                    return json.dumps({'message': '{}'})
                return self.runner(args)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source',
                                'localization/App/fi-FI/v1.0.0', runner=runner)
        self.assertFalse(self.catalog.exists())

    def test_corrupted_release_does_not_enter_catalog(self):
        self.corrupt = True
        with self.assertRaises(subprocess.CalledProcessError):
            import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', 'localization/App/fi-FI/v1.0.0', runner=self.runner)
        self.assertFalse((self.catalog / 'localization/localization-index.json').exists())


class ContentTagRoutingTests(unittest.TestCase):
    def test_explicit_binary_or_malformed_tag_is_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'sources.json'
            config.write_text('{"repositories":["Owner/Source"]}')
            for tag in ('Gurux.AMI.SMTP/v1.0.0', 'Gurux.AMI.SMTP/assets/',
                        'Gurux.AMI.SMTP/localization/vbad', 'resources/App/unit/vbad'):
                calls = []
                with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'content tag'):
                    import_releases(config, directory, 'unused', 'Owner/Source', tag,
                                    runner=lambda args: calls.append(args))
                self.assertEqual(calls, [])


class ModuleContentReleaseImportTests(ContentReleaseImportTests):
    def setUp(self):
        super().setUp()
        (self.root / 'source/localization/en.json').write_text('{"Title":"Device"}')
        self.prepared = []
        for kind, directory in [('localization', 'localization'), ('resources', 'templates')]:
            source = {'kind': kind, 'directory': str(self.root / 'source' / directory),
                      'owner': 'Gurux.AMI.SMTP', 'ownerType': 'module',
                      'ownerVersionRange': '[1.0.0,2.0.0)', 'defaultCulture': 'en', 'name': 'asset'}
            config = self.root / (kind + '-module.json')
            config.write_text(json.dumps({'sources': [source]}))
            output = subprocess.check_output(['dotnet', self.validator, 'content', 'build-module',
                '--sources', str(config), '--kind', kind, '--version', '1.0.0',
                '--output', str(self.root / ('module-' + kind))], text=True)
            prepared = json.loads(output)
            self.prepared.extend(prepared if isinstance(prepared, list) else [prepared])
        self.manifests = {item['tag']: json.loads(Path(item['manifest']).read_text()) for item in self.prepared}

    def test_module_bundles_import_idempotently_without_reading_tag_annotations(self):
        import_releases(self.allowlist, self.catalog, self.validator, runner=self.runner)
        before = {p.relative_to(self.catalog): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()}
        import_releases(self.allowlist, self.catalog, self.validator, runner=self.runner)
        self.assertEqual(before, {p.relative_to(self.catalog): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()})
        self.assertFalse(any('/git/' in arg for call in self.calls for arg in call))
        for manifest in self.manifests.values():
            self.assertTrue((self.catalog / manifest['kind'] / manifest['path']).is_file())

    def test_module_manifest_type_or_identity_is_rejected_before_package_download(self):
        for field, value in [('modulePackage', False), ('ownerType', 'application'),
                             ('owner', 'Other'), ('kind', 'resources')]:
            manifest = self.manifests['Gurux.AMI.SMTP/localization/v1.0.0']
            original = dict(manifest)
            try:
                manifest[field] = value
                item = next(item for item in self.prepared if item['tag'] == manifest['tag'])
                Path(item['manifest']).write_text(json.dumps(manifest))
                self.calls.clear()
                with self.subTest(field=field), self.assertRaises(ValueError):
                    import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source',
                                    manifest['tag'], runner=self.runner)
                self.assertFalse(any('--pattern' in call and call[call.index('--pattern') + 1] != 'release-manifest.json' for call in self.calls))
            finally:
                manifest.clear()
                manifest.update(original)
                Path(item['manifest']).write_text(json.dumps(manifest))

    def test_existing_module_package_bytes_cannot_be_overwritten(self):
        import_releases(self.allowlist, self.catalog, self.validator, runner=self.runner)
        for manifest in self.manifests.values():
            package = self.catalog / manifest['kind'] / manifest['path']
            original = package.read_bytes()
            try:
                package.write_bytes(b'existing immutable bytes')
                with self.subTest(kind=manifest['kind']), self.assertRaises(subprocess.CalledProcessError):
                    import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', manifest['tag'], runner=self.runner)
                self.assertEqual(package.read_bytes(), b'existing immutable bytes')
            finally:
                package.write_bytes(original)

    def test_missing_module_compatibility_range_is_rejected_by_shared_validator(self):
        manifest = self.manifests['Gurux.AMI.SMTP/assets/v1.0.0']
        manifest.pop('ownerVersionRange')
        item = next(item for item in self.prepared if item['tag'] == manifest['tag'])
        Path(item['manifest']).write_text(json.dumps(manifest))
        with self.assertRaises(subprocess.CalledProcessError):
            import_releases(self.allowlist, self.catalog, self.validator, 'Owner/Source', manifest['tag'], runner=self.runner)
        self.assertFalse(self.catalog.exists())
        self.assertFalse(any('--pattern' in call and call[call.index('--pattern') + 1] != 'release-manifest.json' for call in self.calls))

    # Base fixtures use application-specific legacy tag literals.
    test_draft_or_unregistered_dispatch_does_not_change_catalog = None
    test_corrupted_release_does_not_enter_catalog = None
    test_legacy_content_requires_matching_annotated_manifest = None
