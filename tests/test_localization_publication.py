import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import generate_catalog


class LocalizationPublicationTests(unittest.TestCase):
    def test_publishes_index_and_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / 'catalog-sources.json'
            sources.write_text(json.dumps({'items': [{'id': 'Gurux.AMI.Localization',
                'type': 'configuration', 'repository': 'Gurux/Gurux.AMI.Localization',
                'indexUrl': 'https://gurux.github.io/Gurux.AMI.Catalog/localization/localization-index.json'}]}), encoding='utf-8')
            localization = root / 'localization'
            package = localization / 'packages/Core/1.0.0.json'
            package.parent.mkdir(parents=True)
            package.write_bytes(b'{"resources": {}}')
            index = localization / 'localization-index.json'
            index.write_text(json.dumps({'schemaVersion': 1, 'items': [
                {'owner': 'Core', 'releases': [{'path': 'packages/Core/1.0.0.json'}]}
            ]}), encoding='utf-8')
            output = root / '_site/catalog.json'
            with patch('build_catalog.fetch_description', return_value=''):
                generate_catalog(sources, output)
            self.assertEqual((output.parent / 'localization/localization-index.json').read_bytes(), index.read_bytes())
            self.assertEqual((output.parent / 'localization/packages/Core/1.0.0.json').read_bytes(), package.read_bytes())

    def test_repository_contains_valid_initial_index(self):
        index = Path(__file__).resolve().parents[1] / 'localization/localization-index.json'
        self.assertTrue(index.is_file(), 'Published localization index must exist')
        catalog = json.loads(index.read_text(encoding='utf-8'))
        self.assertEqual(catalog['schemaVersion'], 1)
        self.assertIsInstance(catalog['items'], list)
