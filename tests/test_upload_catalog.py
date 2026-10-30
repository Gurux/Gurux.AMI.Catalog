import concurrent.futures
import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlparse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serve_catalog import create_server


def package_bytes(name='module.txt', content='local package'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), content)
    return stream.getvalue()


class UploadCatalogTests(unittest.TestCase):
    def test_upload_preserves_declared_ami_range(self):
        status, catalog = self.post(amiVersionRange='[4.1,5.0)')
        self.assertEqual(status, 201)
        self.assertEqual(catalog['items'][-1]['releases'][0]['amiVersionRange'], '[4.1,5.0)')
        self.stop()
        self.start()
        item = next(item for item in self.catalog()['items'] if item['id'] == 'New.Module')
        self.assertEqual(item['releases'][0]['amiVersionRange'], '[4.1,5.0)')

    def test_uploaded_release_updates_rss(self):
        status, _ = self.post(product='Feed.Module', version='9.0.0', name='Feed product')
        self.assertEqual(status, 201)
        items = ET.parse(self.output / 'rss.xml').findall('channel/item')
        self.assertEqual(items[0].findtext('title'), 'Feed product 9.0.0')

    def test_upload_preserves_references_after_restart(self):
        references = [{'name': 'Gurux.Host.dll', 'version': '1.2.3.4'}]
        status, catalog = self.post(references=json.dumps(references))
        self.assertEqual(status, 201)
        item = next(item for item in catalog['items'] if item['id'] == 'New.Module')
        self.assertEqual(item['releases'][0]['references'], references)
        self.stop()
        self.start()
        item = next(item for item in self.catalog()['items'] if item['id'] == 'New.Module')
        self.assertEqual(item['releases'][0]['references'], references)

    def test_invalid_references_are_rejected(self):
        for value in ['{}', 'null', '[{"name":"../Host.dll","version":"1.2.3.4"}]',
                      '[{"name":"Host.dll","version":"1.2"}]']:
            with self.subTest(value=value):
                self.assertEqual(self.post(references=value)[0], 400)

    def test_register_missing_localization_owner(self):
        self.start()
        body = json.dumps({'schemaVersion': 1, 'owner': 'Smtp', 'ownerType': 'module',
                           'version': '1.0.0', 'defaultCulture': 'en',
                           'resources': {'en': {'Title': 'Mail'}}}).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('POST', '/localization/localization-index.json', body=body,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = json.loads(response.read())
        self.assertEqual(response.status, 201)
        item = result['items'][0]
        self.assertEqual(item['owner'], 'Smtp')
        release = item['releases'][0]
        self.assertEqual(release['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual((self.output / 'localization' / release['path']).read_bytes(), body)
        connection.request('POST', '/localization/localization-index.json', body=body,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(len(json.loads(response.read())['items']), 1)

    def test_publish_new_localization_version_and_reject_changed_existing_version(self):
        self.start()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        for version, title, expected in [('1.0.0', 'Mail', 201), ('1.0.1', 'New Mail', 201),
                                         ('1.0.1', 'New Mail', 200), ('1.0.1', 'Changed', 409),
                                         ('1.0.1+build.1', 'Changed', 409), ('1.0.1.0', 'Changed', 409)]:
            body = json.dumps({'schemaVersion': 1, 'owner': 'Smtp', 'ownerType': 'module',
                               'version': version, 'defaultCulture': 'en',
                               'resources': {'en': {'Title': title}}}).encode()
            connection.request('POST', '/localization/localization-index.json', body=body,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, expected)
        index = json.loads((self.output / 'localization/localization-index.json').read_text())
        self.assertEqual([r['version'] for r in index['items'][0]['releases']], ['1.0.0', '1.0.1'])
        saved = json.loads((self.output / 'localization/packages/Smtp/1.0.1.json').read_text())
        self.assertEqual(saved['resources']['en']['Title'], 'New Mail')

    def test_localization_default_culture_is_case_insensitive(self):
        self.start()
        body = json.dumps({'schemaVersion': 1, 'owner': 'Smtp', 'ownerType': 'module',
                           'version': '1.0.0', 'defaultCulture': 'EN',
                           'resources': {'en': {'Title': 'Mail'}}}).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('POST', '/localization/localization-index.json', body=body,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 201)

    def test_localization_description_defaults_and_preserves_custom_text(self):
        self.start()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        for owner, kind, description, expected in [
            ('Smtp', 'module', None, 'Localized resources for Smtp module'),
            ('App', 'application', '  ', 'Localized resources for App application'),
            ('Custom', 'module', 'My translations', 'My translations')]:
            body = json.dumps({'schemaVersion': 1, 'owner': owner, 'ownerType': kind,
                               'version': '1.0.0', 'defaultCulture': 'en', 'description': description,
                               'resources': {'en': {'Title': 'Mail'}}}).encode()
            connection.request('POST', '/localization/localization-index.json', body=body,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 201)
            item = next(i for i in result['items'] if i['owner'] == owner)
            self.assertEqual(item['description'], expected)
            self.assertEqual(item['releases'][0]['description'], expected)
            self.assertEqual((self.output / 'localization' / item['releases'][0]['path']).read_bytes(), body)

    def test_second_server_cannot_bind_the_same_port(self):
        with self.assertRaises(OSError):
            create_server(self.sources, self.output, port=self.server.server_port)

    def test_health_identifies_running_server(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('GET', '/health')
        response = connection.getresponse()
        result = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['version'])
        self.assertTrue(result['scriptPath'].endswith('serve_catalog.py'))
        self.assertTrue(result['startedAt'])

    def test_invalid_localization_metadata_never_changes_index(self):
        package = {'schemaVersion': 1, 'owner': 'Smtp', 'ownerType': 'module',
                   'version': '1.0.0', 'defaultCulture': 'en', 'resources': {'en': {'Title': 'Mail'}}}
        bodies = [json.dumps({**package, 'ownerVersionRange': 'invalid'}).encode(),
                  json.dumps({**package, 'resources': {'en': {'Title': 'Mail'}, 'EN': {'Title': 'Mail'}}}).encode(),
                  json.dumps({**package, 'resources': {'en': {'   ': 'Mail'}}}).encode(),
                  json.dumps(package).replace('"Title": "Mail"', '"Title":"Mail","Title":"Other"').encode()]
        for body in bodies:
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
            self.addCleanup(connection.close)
            connection.request('POST', '/localization/localization-index.json', body=body,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 400)
            self.assertFalse((self.output / 'localization/localization-index.json').exists())

    def test_validator_execution_failure_is_service_unavailable(self):
        from types import SimpleNamespace
        package = {'schemaVersion': 1, 'owner': 'Smtp', 'ownerType': 'module',
                   'version': '1.0.0', 'defaultCulture': 'en', 'resources': {'en': {'Title': 'Mail'}}}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        with patch('serve_catalog.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='Runtime missing')):
            connection.request('POST', '/localization/localization-index.json', body=json.dumps(package).encode(),
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 503)
        self.assertFalse((self.output / 'localization/localization-index.json').exists())

    def test_invalid_localization_does_not_create_index(self):
        self.start()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('POST', '/localization/localization-index.json', body=b'{"owner":"../bad"}',
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        self.assertFalse((self.output / 'localization' / 'localization-index.json').exists())

    def setUp(self):
        descriptions = patch('build_catalog.fetch_description', return_value='')
        descriptions.start()
        self.addCleanup(descriptions.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources.json'
        self.output = self.root / 'site'
        self.old_package = self.root / 'original.zip'
        self.old_package.write_bytes(package_bytes())
        self.original = {'customSetting': {'keep': True}, 'items': [
            {'id': 'Original', 'type': 'module', 'source': 'local', 'custom': 42,
             'releases': [{'version': '1.0.0', 'package': self.old_package.name,
                           'publishedAt': '2026-01-01T00:00:00Z'}]}]}
        self.sources.write_text(json.dumps(self.original), encoding='utf-8')
        self.start()

    def start(self, **options):
        self.server = create_server(self.sources, self.output, port=0, **options)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None

    def post(self, data=None, path='/catalog.json', headers=None, **metadata):
        metadata = {'product': 'New.Module', 'type': 'module', 'version': '0.0.1-local',
                    'filename': 'New.Module.0.0.1-local.zip', **metadata}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', path + '?' + urlencode(metadata),
                           body=package_bytes() if data is None else data,
                           headers=headers or {'Content-Type': 'application/zip'})
        response = connection.getresponse()
        body = response.read()
        result = (response.status, json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else body)
        connection.close()
        return result

    def catalog(self):
        return json.loads((self.output / 'catalog.json').read_text(encoding='utf-8'))

    def test_uploads_module_application_and_agent_with_downloadable_digest(self):
        data = package_bytes()
        for kind in ('module', 'application', 'agent'):
            with self.subTest(kind=kind):
                status, catalog = self.post(data, product='New.' + kind, type=kind, name='Display name')
                self.assertEqual(status, 201)
                self.assertEqual(catalog['schemaVersion'], 1)
                item = next(item for item in catalog['items'] if item['id'] == 'New.' + kind)
                self.assertEqual(item['type'], kind)
                self.assertEqual(item['name'], 'Display name')
                release = item['releases'][0]
                self.assertEqual(release['version'], '0.0.1-local')
                self.assertTrue(release['isPrerelease'])
                self.assertIn('+00:00', release['publishedAt'])
                asset = release['assets'][0]
                self.assertEqual(urlparse(asset['downloadUrl']).netloc, f'localhost:{self.server.server_port}')
                self.assertEqual(asset['digest'], 'sha256:' + hashlib.sha256(data).hexdigest())
                self.assertEqual(asset['size'], len(data))
                with urllib.request.urlopen(asset['downloadUrl']) as response:
                    self.assertEqual(response.read(), data)
        self.assertEqual(self.catalog(), catalog)
        saved = json.loads(self.sources.read_text(encoding='utf-8'))
        self.assertEqual(saved['customSetting'], {'keep': True})
        self.assertEqual(saved['items'][0], self.original['items'][0])

    def test_duplicate_is_idempotent_conflict_preserves_history_and_restart(self):
        self.assertEqual(self.post(product='Original', version='2.0.0')[0], 201)
        original_catalog = self.catalog()
        original_sources = self.sources.read_bytes()
        self.assertEqual(self.post(product='Original', version='2.0.0')[0], 200)
        self.assertEqual(self.sources.read_bytes(), original_sources)
        self.assertEqual(self.catalog(), original_catalog)
        status, error = self.post(package_bytes(content='different'), product='Original', version='2.0.0')
        self.assertEqual(status, 409)
        self.assertIn('version', error['error'].lower())
        self.assertEqual(self.sources.read_bytes(), original_sources)
        self.assertEqual(self.catalog(), original_catalog)
        self.stop()
        self.start()
        item = next(item for item in self.catalog()['items'] if item['id'] == 'Original')
        self.assertEqual([r['version'] for r in item['releases']], ['2.0.0', '1.0.0'])
        with urllib.request.urlopen(item['releases'][0]['assets'][0]['downloadUrl']) as response:
            self.assertEqual(response.read(), package_bytes())

    def test_rejects_invalid_zip_members_and_metadata_without_writes(self):
        before = self.sources.read_bytes()
        before_catalog = (self.output / 'catalog.json').read_bytes()
        compressed = io.BytesIO()
        with zipfile.ZipFile(compressed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.txt', 'package contents' * 100)
        damaged = bytearray(compressed.getvalue())
        damaged[38:41] = b'\xff\xff\xff'
        symlink = io.BytesIO()
        with zipfile.ZipFile(symlink, 'w') as archive:
            entry = zipfile.ZipInfo('link')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, '../outside')
        cases = [(b'not ZIP', {}), (package_bytes('../escape.dll'), {}),
                 (bytes(damaged), {}), (symlink.getvalue(), {}),
                 (package_bytes('C:/escape.dll'), {}), (package_bytes('..\\escape.dll'), {}),
                 (package_bytes('/escape.dll'), {}), (package_bytes('file:stream'), {}),
                 (package_bytes(), {'filename': '../module.zip'}),
                 (package_bytes(), {'filename': 'C:\\module.zip'}),
                 (package_bytes(), {'filename': 'module.txt'}),
                 (package_bytes(), {'filename': 'NUL.zip'}),
                 (package_bytes(), {'filename': 'trailing .zip.'}),
                 (package_bytes(), {'product': ''}), (package_bytes(), {'product': '../module'}),
                 (package_bytes(), {'version': 'not-version'}),
                 (package_bytes(), {'type': 'configuration'}),
                 (package_bytes(), {'prerelease': 'yes'})]
        for data, metadata in cases:
            with self.subTest(metadata=metadata, data=data[:20]):
                self.assertEqual(self.post(data, **metadata)[0], 400)
                self.assertEqual(self.sources.read_bytes(), before)
                self.assertEqual((self.output / 'catalog.json').read_bytes(), before_catalog)
        self.assertEqual(self.post(path='/other.json')[0], 404)

    def test_requires_content_length_and_zip_content_type(self):
        self.assertEqual(self.post(headers={'Content-Type': 'text/plain'})[0], 415)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.putrequest('POST', '/catalog.json?' + urlencode({'product': 'X', 'type': 'module',
                               'version': '1.0.0', 'filename': 'X.zip'}))
        connection.putheader('Content-Type', 'application/zip')
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 411)
        response.read()
        connection.close()

    def test_rejects_type_or_github_conflicts_preserving_remote_catalog(self):
        self.assertEqual(self.post(product='Original', type='application')[0], 409)
        self.stop()
        sources = json.loads(self.sources.read_text(encoding='utf-8'))
        sources['items'].append({'id': 'Remote', 'type': 'module', 'repository': 'Example/Remote',
                                 'assetPattern': '*.zip'})
        self.sources.write_text(json.dumps(sources), encoding='utf-8')
        with patch('build_catalog.fetch_release_commits', return_value=[]), patch('build_catalog.fetch_releases', return_value=[
                {'tag_name': 'v1.2.3', 'published_at': '2026-01-02T00:00:00Z',
                 'assets': [{'name': 'remote.zip', 'size': 5,
                             'browser_download_url': 'https://example.test/remote.zip'}]}]):
            self.start()
        remote = self.catalog()['items'][1]
        with patch('build_catalog.fetch_releases', side_effect=AssertionError('No network during registration')):
            self.assertEqual(self.post(product='Remote')[0], 409)
            self.assertEqual(self.post()[0], 201)
        self.assertEqual(self.catalog()['items'][1], remote)

    def test_concurrent_uploads_preserve_both_releases(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.post, version=version) for version in ('1.0.0', '2.0.0')]
            self.assertEqual([future.result()[0] for future in futures], [201, 201])
        saved = json.loads(self.sources.read_text(encoding='utf-8'))
        uploaded = next(item for item in saved['items'] if item['id'] == 'New.Module')
        self.assertEqual({r['version'] for r in uploaded['releases']}, {'1.0.0', '2.0.0'})
        catalog_item = next(item for item in self.catalog()['items'] if item['id'] == 'New.Module')
        self.assertEqual({r['version'] for r in catalog_item['releases']}, {'1.0.0', '2.0.0'})

    def test_rejects_filename_excluded_by_existing_asset_pattern(self):
        sources = json.loads(self.sources.read_text(encoding='utf-8'))
        sources['items'][0]['assetPattern'] = 'Original*.zip'
        self.sources.write_text(json.dumps(sources), encoding='utf-8')
        before = self.sources.read_bytes()
        self.assertEqual(self.post(product='Original', version='2.0.0', filename='Other.zip')[0], 400)
        self.assertEqual(self.sources.read_bytes(), before)
        status, catalog = self.post(product='Original', version='2.0.0', filename='Original.2.zip')
        self.assertEqual(status, 201)
        self.assertEqual(catalog['items'][0]['releases'][0]['assets'][0]['name'], 'Original.2.zip')
        self.assertEqual(json.loads(self.sources.read_text())['items'][0]['assetPattern'], 'Original*.zip')

    def test_registration_respects_count_and_stable_only_but_keeps_source_history(self):
        self.stop()
        self.start(count=1, include_prereleases=False)
        self.assertEqual(self.post(product='Original', version='2.0.0', prerelease='false')[0], 201)
        self.assertEqual(self.post(product='Original', version='3.0.0-beta')[0], 201)
        item = self.catalog()['items'][0]
        self.assertEqual([r['version'] for r in item['releases']], ['2.0.0'])
        self.assertEqual(len(json.loads(self.sources.read_text())['items'][0]['releases']), 3)

    def test_failed_catalog_commit_rolls_back_source_and_catalog(self):
        import os
        before_sources = self.sources.read_bytes()
        before_catalog = (self.output / 'catalog.json').read_bytes()
        before_feed = (self.output / 'rss.xml').read_bytes()
        replace = os.replace

        def fail_catalog(source, destination):
            if Path(destination) == self.output / 'catalog.json':
                raise OSError('simulated catalog replace failure')
            return replace(source, destination)

        with patch('os.replace', side_effect=fail_catalog):
            status, error = self.post()
        self.assertEqual(status, 500)
        self.assertIn('error', error)
        self.assertEqual(self.sources.read_bytes(), before_sources)
        self.assertEqual((self.output / 'catalog.json').read_bytes(), before_catalog)
        self.assertEqual((self.output / 'rss.xml').read_bytes(), before_feed)


if __name__ == '__main__':
    unittest.main()
