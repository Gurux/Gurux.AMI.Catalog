import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        descriptions = patch('build_catalog.fetch_description', return_value='')
        descriptions.start()
        self.addCleanup(descriptions.stop)
        commits = patch('build_catalog.fetch_release_commits', return_value=[])
        self.commit_lookup = commits.start()
        self.addCleanup(commits.stop)

    def test_localization_index_retains_asset_name_and_loopback_url(self):
        from build_catalog import build_catalog
        source = {'id': 'Gurux.AMI.Localization', 'type': 'configuration',
                  'repository': 'Gurux/Gurux.AMI.Localization',
                  'indexUrl': 'http://localhost:8000/localization/localization-index.json'}
        result = build_catalog({'items': [source]}, lambda repo: [])
        asset = result['items'][0]['releases'][0]['assets'][0]
        self.assertEqual(asset['name'], 'localization-index.json')
        self.assertEqual(asset['downloadUrl'], source['indexUrl'])

    def test_application_releases_preserve_metadata_and_filter_prereleases(self):
        from build_catalog import build_catalog
        source = {'id': 'Example.Application', 'type': 'application',
                  'name': 'Example application', 'repository': 'Example/Application',
                  'assetPattern': 'Application-*.zip'}
        releases = [
            {'tag_name': 'v1.0.0', 'published_at': '2026-01-01T00:00:00Z',
             'prerelease': False, 'assets': []},
            {'tag_name': 'v2.0.0-beta.1', 'published_at': '2026-02-01T00:00:00Z',
             'prerelease': True, 'html_url': 'https://github.com/Example/Application/releases/tag/v2.0.0-beta.1',
             'body': 'Application preview',
             'assets': [{'name': 'Application-2.0.0-beta.1.zip', 'size': 42,
                         'browser_download_url': 'https://example.test/application.zip',
                         'digest': 'sha256:abc'},
                        {'name': 'Application.pdb', 'size': 5,
                         'browser_download_url': 'https://example.test/application.pdb'}]},
            {'tag_name': 'v3.0.0', 'published_at': '2026-03-01T00:00:00Z',
             'draft': True, 'assets': []}]
        item = build_catalog({'items': [source]}, lambda repo: releases, count=1)['items'][0]
        self.assertEqual(item['type'], 'application')
        self.assertEqual(item['name'], 'Example application')
        self.assertEqual(len(item['releases']), 1)
        release = item['releases'][0]
        self.assertEqual(release['version'], '2.0.0-beta.1')
        self.assertEqual(release['tagName'], 'v2.0.0-beta.1')
        self.assertTrue(release['isPrerelease'])
        self.assertEqual(release['publishedAt'], '2026-02-01T00:00:00Z')
        self.assertEqual(release['releaseNotes'], 'Application preview')
        self.assertEqual(release['releaseUrl'], 'https://github.com/Example/Application/releases/tag/v2.0.0-beta.1')
        self.assertEqual(release['assets'], [{'name': 'Application-2.0.0-beta.1.zip',
                         'downloadUrl': 'https://example.test/application.zip', 'size': 42,
                         'digest': 'sha256:abc'}])
        stable = build_catalog({'items': [source]}, lambda repo: releases,
                               count=1, include_prereleases=False)['items'][0]['releases']
        self.assertEqual([release['version'] for release in stable], ['1.0.0'])

    def test_configuration_index(self):
        from build_catalog import build_catalog
        source = {'id': 'Gurux.DLMS.DeviceProfiles', 'type': 'configuration', 'repository': 'Gurux/Gurux.DLMS.DeviceProfiles', 'indexUrl': 'https://gurux.github.io/Gurux.DLMS.DeviceProfiles/manufacturers.json'}
        result = build_catalog({'items': [source]}, lambda repo: self.fail('No release lookup'))
        self.assertEqual(result['items'][0]['releases'][0]['assets'][0]['downloadUrl'], source['indexUrl'])
        with self.assertRaises(ValueError):
            build_catalog({'items': [{**source, 'indexUrl': 'http://insecure.test/index.json'}]}, lambda repo: [])

    def test_fetches_all_api_pages(self):
        from build_catalog import fetch_releases
        first = [{'tag_name': f'v1.0.{n}'} for n in range(100)]
        second = [{'tag_name': 'v2.0.0'}]
        with patch('urllib.request.urlopen', side_effect=[
                BytesIO(json.dumps(first).encode()), BytesIO(json.dumps(second).encode())]) as http:
            result = fetch_releases('Gurux/Module')
        self.assertEqual(len(result), 101)
        self.assertEqual(result[-1]['tag_name'], 'v2.0.0')
        self.assertIn('page=2', http.call_args_list[1].args[0].full_url)

    def test_catalog_generation(self):
        # Fails until the generator exists. These assertions protect the public contract.
        from build_catalog import build_catalog
        sources = {'items': [
            {'id': 'Module', 'type': 'module', 'repository': 'Gurux/Module', 'assetPattern': '*.zip'},
            {'id': 'Agent', 'type': 'agent', 'repository': 'Gurux/Agent', 'assetPattern': '*.zip'}
        ]}
        releases = [
            {'tag_name': 'v1.0.0', 'published_at': '2026-01-01T00:00:00Z', 'assets': []},
            {'tag_name': 'v2.0.0-beta.1', 'published_at': '2026-02-01T00:00:00Z', 'prerelease': True,
             'assets': [{'name': 'agent.zip', 'browser_download_url': 'https://github.com/Gurux/Agent/releases/download/v2/agent.zip', 'size': 42}]},
            {'tag_name': 'v3.0.0', 'draft': True, 'published_at': '2026-03-01T00:00:00Z', 'assets': []}
        ]
        result = build_catalog(sources, lambda repo: releases, 1, True)
        self.assertEqual(result['schemaVersion'], 1)
        self.assertEqual([x['type'] for x in result['items']], ['module', 'agent'])
        release = result['items'][1]['releases'][0]
        self.assertEqual(release['version'], '2.0.0-beta.1')
        self.assertTrue(release['isPrerelease'])
        self.assertEqual(release['assets'][0]['size'], 42)
        stable = build_catalog(sources, lambda repo: releases, 10, False)
        self.assertEqual(len(stable['items'][0]['releases']), 1)
        self.assertEqual(stable['items'][0]['releases'][0]['assets'], [])

    def test_invalid_or_duplicate_sources_fail(self):
        from build_catalog import build_catalog
        item = {'id': 'Module', 'type': 'module', 'repository': 'Gurux/Module', 'assetPattern': '*.zip'}
        with self.assertRaises(ValueError):
            build_catalog({'items': [item, item]}, lambda repo: [], 10, True)
        with self.assertRaises(ValueError):
            build_catalog({'items': []}, lambda repo: [], 10, True)
        with self.assertRaises(ValueError):
            build_catalog({'items': [{**item, 'repository': 'https://example.com'}]}, lambda repo: [], 10, True)

    def test_api_failure_is_not_published_as_empty_catalog(self):
        from build_catalog import build_catalog
        def fail(repo):
            raise RuntimeError('HTTP 403')
        with self.assertRaises(RuntimeError):
            build_catalog({'items': [{'id': 'M', 'type': 'module', 'repository': 'Gurux/M', 'assetPattern': '*.zip'}]}, fail, 10, True)


if __name__ == '__main__':
    unittest.main()
