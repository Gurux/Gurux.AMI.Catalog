import sys
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
