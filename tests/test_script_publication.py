import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import generate_catalog


class ScriptPublicationTests(unittest.TestCase):
    def test_publishes_index_and_source_without_build_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / 'catalog-sources.json'
            sources.write_text(json.dumps({'items': [{'id': 'Gurux.AMI.Scripts',
                'type': 'configuration', 'repository': 'Gurux/Gurux.AMI.Catalog',
                'indexUrl': 'https://gurux.github.io/Gurux.AMI.Catalog/scripts/script-index.json'}]}), encoding='utf-8')
            scripts = root / 'scripts'
            script = scripts / 'files/hash/Read.cs'
            script.parent.mkdir(parents=True)
            script.write_bytes(b'// published script')
            index = scripts / 'script-index.json'
            index.write_text(json.dumps({'schemaVersion': 1, 'items': [
                {'id': 'Read', 'path': 'files/hash/Read.cs'}]}), encoding='utf-8')
            (scripts / 'build_catalog.py').write_text('# private build tool')
            output = root / '_site/catalog.json'
            with patch('build_catalog.fetch_description', return_value=''):
                generate_catalog(sources, output)
            self.assertEqual((output.parent / 'scripts/script-index.json').read_bytes(), index.read_bytes())
            self.assertEqual((output.parent / 'scripts/files/hash/Read.cs').read_bytes(), script.read_bytes())
            self.assertFalse((output.parent / 'scripts/build_catalog.py').exists())

    def test_repository_contains_initial_index(self):
        index = Path(__file__).resolve().parents[1] / 'scripts/script-index.json'
        self.assertTrue(index.is_file(), 'Published script index must exist')
        catalog = json.loads(index.read_text(encoding='utf-8'))
        self.assertEqual(catalog['schemaVersion'], 1)
        self.assertIsInstance(catalog['items'], list)
