import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import generate_catalog, build_catalog


class ModuleIconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources.json'
        self.output = self.root / '_site/catalog.json'
        self.source = {'id': 'Gurux.AMI.SMTP', 'type': 'module', 'name': 'SMTP',
                       'description': 'Email', 'repository': 'Gurux/Modules',
                       'assetPattern': '*.zip', 'tagPrefix': 'Gurux.AMI.SMTP/v',
                       'iconPath': 'Gurux.AMI.SMTP/assets/Smtp.svg',
                       'iconUrl': 'https://example.org/catalog/assets/Gurux.AMI.SMTP/Smtp.svg'}
        self.releases = [{'tag_name': 'Gurux.AMI.SMTP/v1.0.0', 'draft': False, 'prerelease': False,
                          'published_at': '2026-11-02T12:00:00Z', 'assets': []}]
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg"><title>SMTP</title></svg>'

    def generate(self, fetch=None):
        self.sources.write_text(json.dumps({'items': [self.source]}), encoding='utf-8')
        with patch('build_catalog.fetch_releases', return_value=self.releases):
            return generate_catalog(self.sources, self.output, fetch_icon=fetch or (lambda _: self.svg))

    def test_icon_url_is_preserved_in_catalog_metadata(self):
        catalog = build_catalog({'items': [self.source]}, lambda _: self.releases)
        self.assertEqual(catalog['items'][0]['iconUrl'], self.source['iconUrl'])

    def test_downloads_tagged_icon_without_local_copy_and_updates_on_next_release(self):
        def fetch(url):
            self.assertEqual(url, 'https://raw.githubusercontent.com/Gurux/Modules/refs/tags/Gurux.AMI.SMTP/v1.0.0/Gurux.AMI.SMTP/assets/Smtp.svg')
            return self.svg
        catalog = self.generate(fetch)
        icon = self.output.parent / 'assets/Gurux.AMI.SMTP/Smtp.svg'
        self.assertEqual(icon.read_bytes(), self.svg)
        self.assertEqual(catalog['items'][0]['iconUrl'], self.source['iconUrl'] + '?v=' + hashlib.sha256(self.svg).hexdigest()[:16])
        previous_url = catalog['items'][0]['iconUrl']
        self.releases[0]['tag_name'] = 'Gurux.AMI.SMTP/v1.0.1'
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg"><title>New SMTP</title></svg>'
        catalog = self.generate()
        self.assertEqual(icon.read_bytes(), self.svg)
        self.assertNotEqual(previous_url, catalog['items'][0]['iconUrl'])

    def test_stable_release_is_preferred_over_newer_prerelease(self):
        self.releases.insert(0, {**self.releases[0], 'tag_name': 'Gurux.AMI.SMTP/v2.0.0-rc1',
                                'prerelease': True, 'published_at': '2026-11-03T12:00:00Z'})
        def fetch(url):
            self.assertIn('/v1.0.0/', url)
            return self.svg
        self.generate(fetch)

    def test_prerelease_only_uses_prerelease_icon(self):
        self.releases[0].update(tag_name='Gurux.AMI.SMTP/v1.0.0-rc1', prerelease=True)
        def fetch(url):
            self.assertIn('/v1.0.0-rc1/', url)
            return self.svg
        self.generate(fetch)

    def test_missing_release_omits_icon_without_downloading(self):
        self.releases = []
        def fetch(_):
            self.fail('No published release should not download an icon')
        self.assertNotIn('iconUrl', self.generate(fetch)['items'][0])

    def test_failed_download_preserves_prior_catalog_feed_and_icon(self):
        self.generate()
        icon = self.output.parent / 'assets/Gurux.AMI.SMTP/Smtp.svg'
        originals = {p: p.read_bytes() for p in (self.output, self.output.with_name('rss.xml'), icon)}
        def fetch(_):
            raise OSError('404')
        with self.assertRaises(OSError):
            self.generate(fetch)
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_failed_catalog_replacement_rolls_back_icons_and_cleans_temporary_files(self):
        self.generate()
        icon = self.output.parent / 'assets/Gurux.AMI.SMTP/Smtp.svg'
        originals = {p: p.read_bytes() for p in (self.output, self.output.with_name('rss.xml'), icon)}
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg"><title>Updated</title></svg>'
        replace = Path.replace
        def fail_catalog(path, target):
            if path == self.output.with_suffix('.tmp'):
                raise OSError('Simulated catalog replacement failure')
            return replace(path, target)
        with patch.object(Path, 'replace', fail_catalog):
            with self.assertRaises(OSError):
                self.generate()
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(list(self.output.parent.rglob('*.tmp')), [])

    def test_manually_copied_stale_icon_does_not_override_automatic_icon(self):
        local = self.root / 'assets/Gurux.AMI.SMTP/Smtp.svg'
        local.parent.mkdir(parents=True)
        local.write_bytes(b'<svg><title>Old manually copied icon</title></svg>')
        self.generate()
        icon = self.output.parent / 'assets/Gurux.AMI.SMTP/Smtp.svg'
        self.assertEqual(icon.read_bytes(), self.svg)

    def test_invalid_paths_urls_and_image_bytes_are_rejected(self):
        for path in ('../Smtp.svg', '/Smtp.svg', 'assets/../Smtp.svg', 'assets\\Smtp.svg', 'assets/a.html'):
            with self.subTest(path=path):
                self.source['iconPath'] = path
                with self.assertRaises(ValueError):
                    self.generate()
        self.source['iconPath'] = 'Gurux.AMI.SMTP/assets/Smtp.svg'
        for data in (b'', b'<html/>', b'<svg>', b'x' * (1024 * 1024 + 1), b'<!DOCTYPE svg><svg/>'):
            with self.subTest(data=data[:20]):
                with self.assertRaises(ValueError):
                    self.generate(lambda _: data)
        self.source['iconUrl'] = 'https://example.org/wrong.svg'
        with self.assertRaises(ValueError):
            self.generate()
