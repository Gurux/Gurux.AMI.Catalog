import hashlib
import json
import shutil
import subprocess
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

    def test_git_roundtrip_preserves_script_integrity_bytes(self):
        repository = Path(__file__).resolve().parents[1]
        catalog = json.loads((repository / 'scripts/script-index.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'config', 'core.autocrlf', 'false'], check=True)
            shutil.copyfile(repository / '.gitattributes', root / '.gitattributes')
            expected = {}
            for release in catalog['items']:
                relative = 'scripts/' + release['path']
                data = (repository / relative).read_bytes()
                self.assertEqual(len(data), release['size'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), release['sha256'])
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                expected[relative] = data
            # Keep CRLF coverage even if a future catalog only publishes LF files.
            sentinel = 'scripts/files/regression/LineEndings.cs'
            (root / sentinel).parent.mkdir(parents=True)
            expected[sentinel] = b'// exact source bytes\r\nclass Script {}\r\n'
            (root / sentinel).write_bytes(expected[sentinel])
            subprocess.run(['git', '-C', str(root), 'add', '.gitattributes', 'scripts/files'], check=True)
            checkout = root / 'checkout'
            checkout.mkdir()
            subprocess.run(['git', '-C', str(root), 'checkout-index', '--all',
                            '--prefix=' + checkout.as_posix() + '/'], check=True)
            for relative, original in expected.items():
                staged = subprocess.run(['git', '-C', str(root), 'show', ':' + relative],
                                        check=True, capture_output=True).stdout
                self.assertEqual(staged, original, relative + ' was normalized by Git clean')
                self.assertEqual((checkout / relative).read_bytes(), original,
                                 relative + ' changed during Git checkout')
    def test_repository_contains_initial_index(self):
        index = Path(__file__).resolve().parents[1] / 'scripts/script-index.json'
        self.assertTrue(index.is_file(), 'Published script index must exist')
        catalog = json.loads(index.read_text(encoding='utf-8'))
        self.assertEqual(catalog['schemaVersion'], 1)
        self.assertIsInstance(catalog['items'], list)
