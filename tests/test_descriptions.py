import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_catalog as catalog


class DescriptionTests(unittest.TestCase):
    def source(self, **values):
        return dict(id='SMTP', type='module', repository='Gurux/Modules',
                    assetPattern='*.zip', **values)

    def test_explicit_description_skips_network(self):
        with patch.object(catalog, 'github_json', side_effect=AssertionError('No metadata lookup')):
            item = catalog.build_catalog({'items': [self.source(description='Custom')]}, lambda _: [])['items'][0]
        self.assertEqual(item['description'], 'Custom')

    def test_empty_descriptions_use_metadata_and_cache_shared_repository(self):
        sources = [self.source(description=value) for value in (None, '', '  ')]
        for number, source in enumerate(sources):
            source['id'] = str(number)
        import base64
        response = {'encoding': 'base64', 'content': base64.b64encode(b'{"description": "Module description"}').decode()}
        with patch.object(catalog, 'github_json', return_value=response) as get:
            items = catalog.build_catalog({'items': sources}, lambda _: [])['items']
        self.assertEqual([item['description'] for item in items], ['Module description'] * 3)
        self.assertEqual(get.call_count, 1)

    def test_subdirectory_metadata_path_is_encoded(self):
        import base64
        response = {'encoding': 'base64', 'content': base64.b64encode(
            b'{"description": "SMTP notifications"}').decode()}
        with patch.object(catalog, 'github_json', return_value=response) as get:
            item = catalog.build_catalog({'items': [self.source(metadataPath='SMTP module/catalog-info.json')]}, lambda _: [])['items'][0]
        self.assertEqual(item['description'], 'SMTP notifications')
        self.assertEqual(get.call_args.args[0], 'https://api.github.com/repos/Gurux/Modules/contents/SMTP%20module/catalog-info.json')

    def test_missing_root_file_uses_repository_description(self):
        missing = HTTPError('url', 404, 'Not Found', {}, None)
        with patch.object(catalog, 'github_json', side_effect=[missing, {'description': 'Repository description'}]):
            self.assertEqual(catalog.fetch_description('Gurux/Modules'), 'Repository description')

    def test_empty_metadata_uses_repository_description(self):
        import base64
        response = {'encoding': 'base64', 'content': base64.b64encode(b'{"description": " "}').decode()}
        with patch.object(catalog, 'github_json', side_effect=[response, {'description': None}]):
            self.assertEqual(catalog.fetch_description('Gurux/Modules'), '')

    def test_missing_configuration_repository_keeps_index_with_empty_description(self):
        source = dict(id='Localization', type='configuration',
                      repository='Gurux/Missing',
                      indexUrl='http://localhost:8000/localization/index.json')
        missing = HTTPError('url', 404, 'Not Found', {}, None)
        self.addCleanup(missing.close)
        with patch.object(catalog, 'github_json', side_effect=missing):
            item = catalog.build_catalog({'items': [source]}, lambda _: [])['items'][0]
        self.assertEqual(item['description'], '')
        self.assertEqual(item['releases'][0]['assets'][0]['downloadUrl'], source['indexUrl'])

    def test_repository_fallback_api_errors_are_not_silenced(self):
        for code in (401, 403, 500):
            with self.subTest(code=code):
                missing = HTTPError('url', 404, 'Not Found', {}, None)
                failure = HTTPError('url', code, 'API error', {}, None)
                self.addCleanup(missing.close)
                self.addCleanup(failure.close)
                with patch.object(catalog, 'github_json', side_effect=[missing, failure]):
                    with self.assertRaises(HTTPError):
                        catalog.fetch_description('Gurux/Modules')

    def test_api_errors_are_not_silenced(self):
        denied = HTTPError('url', 403, 'Forbidden', {}, None)
        with patch.object(catalog, 'github_json', side_effect=denied):
            with self.assertRaises(HTTPError):
                catalog.fetch_description('Gurux/Modules')

    def test_invalid_metadata_fails(self):
        with patch.object(catalog, 'github_json', return_value={'encoding': 'base64', 'content': 'bm90IGpzb24='}):
            with self.assertRaises(ValueError):
                catalog.fetch_description('Gurux/Modules')

    def test_invalid_paths_fail_before_network(self):
        for path in ('../catalog-info.json', '/catalog-info.json', '', None, 'SMTP\\catalog-info.json'):
            with self.subTest(path=path), patch.object(catalog, 'github_json', side_effect=AssertionError('No network')):
                with self.assertRaises(ValueError):
                    catalog.fetch_description('Gurux/Modules', path)

    def test_distinct_modules_have_distinct_descriptions(self):
        import base64
        sources = [self.source(metadataPath='Smtp/catalog-info.json'),
                   {**self.source(metadataPath='Modbus/catalog-info.json'), 'id': 'Modbus'}]
        responses = [{'encoding': 'base64', 'content': base64.b64encode(
            ('{"description": "' + name + '"}').encode()).decode()} for name in ('SMTP', 'Modbus')]
        with patch.object(catalog, 'github_json', side_effect=responses):
            items = catalog.build_catalog({'items': sources}, lambda _: [])['items']
        self.assertEqual([item['description'] for item in items], ['SMTP', 'Modbus'])
