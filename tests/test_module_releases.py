import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import build_catalog, build_rss


class ModuleReleaseTests(unittest.TestCase):
    def source(self, name='SMTP'):
        return {'id': f'Gurux.AMI.{name}', 'type': 'module', 'description': 'Module',
                'repository': 'Gurux/Gurux.DLMS.AMI.Modules',
                'tagPrefix': f'Gurux.AMI.{name}/v', 'assetPattern': f'Gurux.AMI.{name}-*.zip'}

    def release(self, name, version, day, **extra):
        return {'tag_name': f'Gurux.AMI.{name}/v{version}',
                'published_at': f'2026-10-{day:02}T12:00:00Z', 'assets': [], **extra}

    def test_independent_versions_baselines_count_and_rss_notes(self):
        releases = [self.release('SMTP', '1.0.0', 1),
                    self.release('Other', '8.0.0', 2),
                    self.release('SMTP', '2.0.0-rc1', 3, prerelease=True),
                    self.release('Other', '9.0.0', 4),
                    self.release('SMTP', '2.0.0', 5, body='- fix(security): SMTP authentication'),
                    self.release('SMTP', '3.0.0', 6, draft=True)]
        with patch('build_catalog.fetch_release_commits', return_value=['fix(security): auth']) as commits:
            catalog = build_catalog({'items': [self.source(), self.source('Other')]},
                                    lambda repo: releases, count=1, include_prereleases=False)
        smtp, other = catalog['items']
        self.assertEqual([r['version'] for r in smtp['releases']], ['2.0.0'])
        self.assertEqual([r['version'] for r in other['releases']], ['9.0.0'])
        commits.assert_not_called()
        self.assertTrue(smtp['releases'][0]['isSecurityUpdate'])
        self.assertFalse(other['releases'][0]['isSecurityUpdate'])
        self.assertIn('fix(security): SMTP authentication', ET.fromstring(build_rss(catalog)).findtext('channel/item/description'))

    def test_invalid_prefix_fails(self):
        for prefix in ('', 1, None):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                build_catalog({'items': [{**self.source(), 'tagPrefix': prefix}]}, lambda repo: [])

    def test_no_matching_tag_has_no_releases_or_commit_requests(self):
        with patch('build_catalog.fetch_release_commits') as commits:
            result = build_catalog({'items': [self.source()]},
                                   lambda repo: [self.release('Other', '1.0.0', 1)])
        self.assertEqual(result['items'][0]['releases'], [])
        commits.assert_not_called()


class CompatibilityMetadataTests(unittest.TestCase):
    source = ModuleReleaseTests.source
    release = ModuleReleaseTests.release
    def fixture(self):
        source = {**self.source(), 'compatibilityMetadata': True}
        release = self.release('SMTP', '1.0.0', 1)
        name = 'Gurux.AMI.SMTP-1.0.0.compatibility.json'
        url = 'https://github.com/Gurux/Gurux.DLMS.AMI.Modules/releases/download/Gurux.AMI.SMTP%2Fv1.0.0/' + name
        release['assets'] = [
            {'name': 'Gurux.AMI.SMTP-1.0.0.zip', 'state': 'uploaded', 'browser_download_url': 'https://example.com/module.zip', 'size': 10},
            {'name': name, 'state': 'uploaded', 'browser_download_url': url, 'size': 100}]
        metadata = {'schemaVersion': 1, 'id': source['id'], 'version': '1.0.0',
                    'targetFramework': 'net10.0',
                    'references': [{'name': 'Gurux.DLMS.AMI.Server.dll', 'version': '1.2.3.4'}]}
        return source, release, metadata

    def build(self, source, release, metadata):
        return build_catalog({'items': [source]}, lambda repo: [release],
                             fetch_metadata=lambda url: metadata)['items'][0]['releases'][0]

    def test_sidecar_references_become_catalog_requirements(self):
        source, release, metadata = self.fixture()
        self.assertEqual(self.build(source, release, metadata)['references'], metadata['references'])

    def test_missing_duplicate_or_unpublished_metadata_fails_closed(self):
        for mode in ('missing', 'duplicate', 'unpublished'):
            source, release, metadata = self.fixture()
            if mode == 'missing':
                release['assets'].pop()
            elif mode == 'duplicate':
                release['assets'].append(dict(release['assets'][1]))
            else:
                release['assets'][1]['state'] = 'new'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.build(source, release, metadata)

    def test_untrusted_metadata_url_is_rejected_before_fetch(self):
        for url in ('http://github.com/x', 'https://evil.example/metadata',
                    'https://github.com/Other/Repo/releases/download/v1/file',
                    'https://github.com/Gurux/Gurux.DLMS.AMI.Modules/releases/download/wrong/file'):
            source, release, metadata = self.fixture()
            release['assets'][1]['browser_download_url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.build(source, release, metadata)

    def test_invalid_metadata_fails_closed(self):
        for field, value in [('schemaVersion', True), ('schemaVersion', 2), ('id', 'Other'),
                             ('version', '2.0.0'), ('targetFramework', None),
                             ('targetFramework', 'net9.0'), ('references', []),
                             ('references', [{'name': '../host.dll', 'version': '1.2.3.4'}]),
                             ('references', [{'name': 'Host.dll', 'version': '1.2.3.4'},
                                             {'name': 'host.dll', 'version': '2.0.0.0'}])]:
            source, release, metadata = self.fixture()
            metadata[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.build(source, release, metadata)

    def test_release_without_installable_zip_needs_no_metadata(self):
        source, release, metadata = self.fixture()
        release['assets'] = []
        self.assertNotIn('references', self.build(source, release, metadata))


    def test_download_is_bounded_and_does_not_forward_github_token(self):
        from build_catalog import fetch_compatibility_metadata, METADATA_MAX_BYTES
        source, release, metadata = self.fixture()
        url = release['assets'][1]['browser_download_url']
        with patch.dict('os.environ', {'GH_TOKEN': 'secret'}), patch(
                'build_catalog.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(metadata).encode())) as opened:
            self.assertEqual(fetch_compatibility_metadata(url), metadata)
        request = opened.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(opened.call_args.kwargs['timeout'], 15)
        with patch('build_catalog.urllib.request.urlopen',
                   return_value=io.BytesIO(b' ' * (METADATA_MAX_BYTES + 1))):
            with self.assertRaisesRegex(ValueError, 'size limit'):
                fetch_compatibility_metadata(url)

    def test_missing_sidecar_preserves_published_catalog_and_feed(self):
        from build_catalog import generate_catalog
        source, release, metadata = self.fixture()
        release['assets'].pop()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, catalog, feed = root / 'sources.json', root / 'catalog.json', root / 'rss.xml'
            config.write_text(json.dumps({'items': [source]}))
            catalog.write_text('published catalog')
            feed.write_text('published feed')
            with patch('build_catalog.fetch_releases', return_value=[release]):
                with self.assertRaises(ValueError):
                    generate_catalog(config, catalog)
            self.assertEqual(catalog.read_text(), 'published catalog')
            self.assertEqual(feed.read_text(), 'published feed')

    def test_unpublished_zip_is_not_installable(self):
        source, release, metadata = self.fixture()
        release['assets'] = [release['assets'][0]]
        release['assets'][0]['state'] = 'new'
        result = self.build(source, release, metadata)
        self.assertEqual(result['assets'], [])
        self.assertNotIn('references', result)
