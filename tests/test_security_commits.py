import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_catalog as catalog


class SecurityCommitTests(unittest.TestCase):
    def setUp(self):
        descriptions = patch('build_catalog.fetch_description', return_value='')
        descriptions.start()
        self.addCleanup(descriptions.stop)

    source = {'id': 'Module', 'type': 'module', 'repository': 'Gurux/Module', 'assetPattern': '*.zip'}

    @staticmethod
    def release(tag, date, **fields):
        return {'tag_name': tag, 'published_at': date, 'assets': [], **fields}

    def build(self, releases, lookup, **options):
        return catalog.build_catalog({'items': [self.source]}, lambda repo: releases,
                                     fetch_commits=lookup, **options)['items'][0]['releases']

    def test_security_prefix_sets_both_fields_and_preserves_release_notes(self):
        release = self.release('v1.0.0', '2026-01-01T00:00:00Z', body='Published notes')
        for message, expected in [('fix(security): validate signatures', True),
                                  ('fix(security): validate signatures\n\nDetails', True),
                                  ('fix: security improvements', False),
                                  ('docs: describe fix(security): convention', False),
                                  ('fix: ordinary change\n\nfix(security): example', False),
                                  ('Fix(Security): wrong case', False)]:
            with self.subTest(message=message):
                result = self.build([release], lambda repo, base, head: [message])[0]
                self.assertEqual(result['priority'], 'Critical' if expected else 'Normal')
                self.assertEqual(result['isSecurityUpdate'], expected)
                self.assertEqual(result['releaseNotes'], 'Published notes')

    def test_count_filter_does_not_lose_previous_stable_tag_or_beta_fix(self):
        releases = [self.release('v3.0.0', '2026-04-01T00:00:00Z', draft=True),
                    self.release('v2.0.0', '2026-03-01T00:00:00Z'),
                    self.release('v2.0.0-beta.1', '2026-02-01T00:00:00Z', prerelease=True),
                    self.release('v1.0.0', '2026-01-01T00:00:00Z')]
        calls = []
        def lookup(repo, base, head):
            calls.append((repo, base, head))
            return ['fix(security): fixed in beta'] if base == 'v1.0.0' else []
        result = self.build(releases, lookup, count=1, include_prereleases=False)
        self.assertEqual(calls, [('Gurux/Module', 'v1.0.0', 'v2.0.0')])
        self.assertTrue(result[0]['isSecurityUpdate'])

    def test_first_release_checks_history_at_its_tag_and_lookup_is_cached(self):
        sources = {'items': [self.source, {**self.source, 'id': 'AnotherModule'}]}
        calls = []
        def lookup(repo, base, head):
            calls.append((repo, base, head))
            return ['fix(security): prevent bypass']
        result = catalog.build_catalog(sources, lambda repo: [self.release('v1', '2026-01-01T00:00:00Z')],
                                       fetch_commits=lookup)
        self.assertEqual(calls, [('Gurux/Module', None, 'v1')])
        self.assertTrue(all(item['releases'][0]['isSecurityUpdate'] for item in result['items']))

    def test_commit_lookup_failure_does_not_overwrite_existing_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / 'sources.json'
            sources.write_text(json.dumps({'items': [self.source]}))
            output = root / 'catalog.json'
            output.write_text('previous catalog')
            with patch.object(catalog, 'fetch_releases', return_value=[self.release('v1', '2026-01-01T00:00:00Z')]), \
                 patch.object(catalog, 'fetch_release_commits', side_effect=RuntimeError('HTTP 403')):
                with self.assertRaises(RuntimeError):
                    catalog.generate_catalog(sources, output)
            self.assertEqual(output.read_text(), 'previous catalog')

    def test_local_package_uses_configured_repository_and_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'module.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('module.txt', 'fixture')
            source = {**self.source, 'source': 'local', 'releases': [
                {'version': '1.0.0', 'package': str(package), 'publishedAt': '2026-01-01T00:00:00Z'}]}
            calls = []
            def lookup(repo, base, head):
                calls.append((repo, base, head))
                return ['fix(security): check access']
            result = catalog.build_catalog({'items': [source]}, lambda repo: self.fail('No release fetch'),
                fetch_commits=lookup, source_directory=root, output_directory=root / 'out', base_url='http://localhost:8000')
            self.assertEqual(calls, [('Gurux/Module', None, 'v1.0.0')])
            self.assertEqual(result['items'][0]['releases'][0]['priority'], 'Critical')

    def test_fetches_paginated_comparison_and_encodes_tags(self):
        first = {'commits': [{'commit': {'message': 'fix: ordinary'}} for _ in range(100)]}
        second = {'commits': [{'commit': {'message': 'fix(security): validate'}}]}
        with patch('urllib.request.urlopen', side_effect=[BytesIO(json.dumps(first).encode()),
                                                         BytesIO(json.dumps(second).encode())]) as http:
            messages = catalog.fetch_release_commits('Gurux/Module', 'release/v1', 'release/v2')
        self.assertEqual(messages[-1], 'fix(security): validate')
        self.assertIn('/compare/release%2Fv1...release%2Fv2?', http.call_args_list[0].args[0].full_url)
        self.assertIn('page=2', http.call_args_list[1].args[0].full_url)

    def test_first_tag_history_and_invalid_responses(self):
        with patch('urllib.request.urlopen', return_value=BytesIO(b'[{"commit":{"message":"fix(security): first"}}]')) as http:
            self.assertEqual(catalog.fetch_release_commits('Gurux/Module', None, 'v1'), ['fix(security): first'])
        self.assertIn('/commits?sha=v1&', http.call_args.args[0].full_url)
        for body in [b'{}', b'[{"commit":{}}]']:
            with self.subTest(body=body), patch('urllib.request.urlopen', return_value=BytesIO(body)):
                with self.assertRaises(ValueError):
                    catalog.fetch_release_commits('Gurux/Module', None, 'v1')


if __name__ == '__main__':
    unittest.main()
