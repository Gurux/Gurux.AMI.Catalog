import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import build_catalog


class WasmReleaseTests(unittest.TestCase):
    def source(self):
        return {'id': 'Gurux.DLMS.AMI.Wasm', 'type': 'application', 'description': 'Static AMI',
                'repository': 'Gurux/Gurux.DLMS.AMI4', 'tagPrefix': 'Gurux.DLMS.AMI/v',
                'assetPattern': 'Gurux.DLMS.AMI.Wasm-*.zip', 'apiVersionMatchesRelease': True}

    def test_static_release_has_exact_api_compatibility_and_only_uploaded_matching_asset(self):
        release = {'tag_name': 'Gurux.DLMS.AMI/v4.1.2611.301', 'published_at': '2026-11-03T12:00:00Z',
                   'assets': [{'name': 'Gurux.DLMS.AMI.Wasm-4.1.2611.301.zip', 'size': 123,
                               'state': 'uploaded', 'browser_download_url': 'https://example.test/wasm.zip',
                               'digest': 'sha256:' + 'a' * 64},
                              {'name': 'Gurux.DLMS.AMI-4.1.2611.301.zip', 'size': 123, 'state': 'uploaded',
                               'browser_download_url': 'https://example.test/server.zip'}]}
        result = build_catalog({'items': [self.source()]}, lambda repo: [release])['items'][0]['releases'][0]
        self.assertEqual(result['amiVersionRange'], '[4.1.2611.301]')
        self.assertEqual([a['name'] for a in result['assets']], ['Gurux.DLMS.AMI.Wasm-4.1.2611.301.zip'])

    def test_missing_wasm_asset_does_not_advertise_deployable_release(self):
        release = {'tag_name': 'Gurux.DLMS.AMI/v4.1.2611.301', 'published_at': '2026-11-03T12:00:00Z', 'assets': []}
        self.assertEqual(build_catalog({'items': [self.source()]}, lambda repo: [release])['items'][0]['releases'], [])

    def test_missing_digest_is_not_published(self):
        release = {'tag_name': 'Gurux.DLMS.AMI/v4.1.2611.301', 'published_at': '2026-11-03T12:00:00Z',
                   'assets': [{'name': 'Gurux.DLMS.AMI.Wasm-4.1.2611.301.zip', 'size': 123, 'state': 'uploaded',
                               'browser_download_url': 'https://example.test/wasm.zip'}]}
        self.assertEqual(build_catalog({'items': [self.source()]}, lambda repo: [release])['items'][0]['releases'], [])
