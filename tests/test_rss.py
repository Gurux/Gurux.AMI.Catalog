import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import build_rss, generate_catalog


class CatalogRssTests(unittest.TestCase):
    def catalog(self):
        return {'generatedAt': '2026-10-27T12:00:00Z', 'items': [
            {'id': 'Module', 'name': 'Module & tools', 'description': 'JSON description <text>',
             'releases': [{'version': str(day), 'publishedAt': f'2026-10-{day:02}T12:00:00Z',
                           'releaseUrl': f'https://example.com/releases/{day}', 'releaseNotes': 'Release notes'}
                          for day in range(1, 16)]},
            {'id': 'Agent', 'name': 'Agent', 'description': 'Agent description',
             'releases': [{'version': '2.0', 'publishedAt': '2026-10-26T12:00:00+02:00'}]}]}

    def test_ten_latest_releases_across_products_newest_first(self):
        rss = ET.fromstring(build_rss(self.catalog(), 'https://catalog.example/ami/'))
        items = rss.findall('channel/item')
        self.assertEqual(len(items), 10)
        self.assertEqual(items[0].findtext('title'), 'Agent 2.0')
        self.assertEqual(items[1].findtext('title'), 'Module & tools 15')
        self.assertEqual(items[-1].findtext('title'), 'Module & tools 7')
        self.assertEqual(items[0].findtext('pubDate'), 'Mon, 26 Oct 2026 10:00:00 GMT')
        self.assertEqual(rss.findtext('channel/link'), 'https://catalog.example/ami/')

    def test_json_description_and_release_notes_are_escaped_and_guids_stable(self):
        catalog = self.catalog()
        xml = build_rss(catalog)
        self.assertIn('JSON description &lt;text&gt;', xml)
        item = ET.fromstring(xml).findall('channel/item')[1]
        self.assertIn('JSON description <text>', item.findtext('description'))
        self.assertIn('Release notes', item.findtext('description'))
        self.assertEqual(item.findtext('link'), 'https://example.com/releases/15')
        self.assertEqual(item.find('guid').get('isPermaLink'), 'false')
        guid = item.findtext('guid')
        catalog['generatedAt'] = '2026-11-01T12:00:00Z'
        self.assertEqual(ET.fromstring(build_rss(catalog)).findall('channel/item')[1].findtext('guid'), guid)

    def test_empty_feed_and_missing_descriptions_and_unsafe_links(self):
        self.assertEqual(ET.fromstring(build_rss({'items': []})).findall('channel/item'), [])
        catalog = {'items': [{'id': 'Empty', 'releases': [
            {'version': '1.0', 'releaseUrl': 'javascript:alert(1)'},
            {'version': '2.0', 'releaseUrl': 'https://user:password@example.com'}]}]}
        items = ET.fromstring(build_rss(catalog)).findall('channel/item')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.find('link') is None for item in items))

    def test_catalog_generation_writes_rss_beside_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / 'sources.json'
            sources.write_text('{"items":[]}', encoding='utf-8')
            output = root / '_site' / 'catalog.json'
            with patch('build_catalog.build_catalog', return_value=self.catalog()):
                generate_catalog(sources, output)
            self.assertEqual(len(json.loads(output.read_text(encoding='utf-8'))['items']), 2)
            self.assertEqual(len(ET.parse(output.with_name('rss.xml')).findall('channel/item')), 10)

    def test_failed_catalog_build_preserves_previous_json_and_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / 'sources.json'
            sources.write_text('{"items":[]}', encoding='utf-8')
            output = root / 'catalog.json'
            output.write_text('old JSON', encoding='utf-8')
            feed = root / 'rss.xml'
            feed.write_text('old RSS', encoding='utf-8')
            with patch('build_catalog.build_catalog', side_effect=ValueError('invalid catalog')):
                with self.assertRaises(ValueError):
                    generate_catalog(sources, output)
            self.assertEqual(output.read_text(encoding='utf-8'), 'old JSON')
            self.assertEqual(feed.read_text(encoding='utf-8'), 'old RSS')

    def test_failed_publication_preserves_previous_json_and_feed(self):
        for destination in ('rss.xml', 'catalog.json'):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                sources = root / 'sources.json'
                sources.write_text('{"items":[]}', encoding='utf-8')
                output = root / 'catalog.json'
                feed = root / 'rss.xml'
                output.write_text('old JSON', encoding='utf-8')
                feed.write_text('old RSS', encoding='utf-8')
                replace = Path.replace
                failed = False

                def fail_once(source, target):
                    nonlocal failed
                    if Path(target).name == destination and not failed:
                        failed = True
                        raise OSError('publication failed')
                    return replace(source, target)

                with patch('build_catalog.build_catalog', return_value=self.catalog()), patch.object(Path, 'replace', fail_once):
                    with self.assertRaises(OSError):
                        generate_catalog(sources, output)
                self.assertEqual(output.read_text(encoding='utf-8'), 'old JSON')
                self.assertEqual(feed.read_text(encoding='utf-8'), 'old RSS')
                self.assertEqual(list(root.glob('*.tmp')), [])
