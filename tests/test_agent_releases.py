import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_catalog import build_catalog


class AgentReleaseTests(unittest.TestCase):
    def test_core_application_excludes_agent_releases(self):
        sources = json.loads((ROOT / 'catalog-sources.json').read_text(encoding='utf-8'))
        source = next(s for s in sources['items'] if s['id'] == 'Gurux.DLMS.AMI')
        releases = [
            {'tag_name': 'Gurux.DLMS.AMI/v4.1.2611.301', 'published_at': '2026-11-03T12:00:00Z',
             'assets': [{'name': 'Gurux.DLMS.AMI-4.1.2611.301.zip', 'size': 123,
                         'browser_download_url': 'https://example.test/server.zip'}]},
            {'tag_name': 'Gurux.DLMS.AMI.Agent/v4.1.2611.999', 'published_at': '2026-11-03T13:00:00Z', 'assets': []}
        ]
        catalog = build_catalog({'items': [source]}, lambda repo: releases)
        core = catalog['items'][0]
        self.assertEqual(core['type'], 'application')
        self.assertEqual([r['version'] for r in core['releases']], ['4.1.2611.301'])
        self.assertEqual(core['releases'][0]['assets'][0]['downloadUrl'], 'https://example.test/server.zip')

    def test_registered_agent_selects_only_its_binary_releases(self):
        sources = json.loads((ROOT / 'catalog-sources.json').read_text(encoding='utf-8'))
        source = next(s for s in sources['items'] if s['id'] == 'Gurux.DLMS.AMI.Agent')

        def release(tag, filename, draft=False):
            return {'tag_name': tag, 'draft': draft, 'published_at': '2026-11-03T12:00:00Z',
                    'body': '- fix: restore agent connection',
                    'html_url': 'https://github.com/Gurux/Gurux.DLMS.AMI4/releases/tag/' + tag,
                    'assets': [{'name': filename, 'size': 123, 'state': 'uploaded',
                                'browser_download_url': 'https://example.test/' + filename,
                                'digest': 'sha256:abc'}]}

        releases = [
            release('Gurux.DLMS.AMI.Agent/v4.1.2611.301', 'Gurux.DLMS.AMI.Agent-4.1.2611.301.zip'),
            release('Gurux.DLMS.AMI.Shared/v4.1.2611.999', 'Gurux.DLMS.AMI.Shared.zip'),
            release('Gurux.DLMS.AMI.Agent.Worker/v4.1.2611.998', 'Gurux.DLMS.AMI.Agent.Worker.zip'),
            release('Gurux.DLMS.AMI.Agent/v4.1.2611.302', 'Gurux.DLMS.AMI.Agent-4.1.2611.302.zip', True)
        ]
        releases[0]['assets'].append({'name': 'Gurux.DLMS.AMI.Agent-4.1.2611.301.zip.sha256',
                                     'size': 64, 'browser_download_url': 'https://example.test/checksum'})
        catalog = build_catalog({'items': [source]}, lambda repo: releases,
                                fetch_commits=lambda *args: self.fail('Prefixed releases use their own changelog'))
        agent = catalog['items'][0]
        self.assertEqual(agent['type'], 'agent')
        self.assertEqual(agent['repository'], 'Gurux/Gurux.DLMS.AMI4')
        self.assertEqual([r['version'] for r in agent['releases']], ['4.1.2611.301'])
        self.assertEqual([a['name'] for a in agent['releases'][0]['assets']],
                         ['Gurux.DLMS.AMI.Agent-4.1.2611.301.zip'])
        self.assertEqual(agent['releases'][0]['assets'][0]['digest'], 'sha256:abc')


class AgentCompatibilityTests(unittest.TestCase):
    def fixture(self):
        product = 'Gurux.DLMS.AMI.Agent'
        version = '4.1.2611.601'
        tag = f'{product}/v{version}'
        name = f'{product}-{version}.compatibility.json'
        source = {'id': product, 'type': 'agent', 'description': 'Agent',
                  'repository': 'Gurux/Gurux.DLMS.AMI4', 'tagPrefix': f'{product}/v',
                  'assetPattern': f'{product}-*.zip', 'compatibilityMetadata': True}
        release = {'tag_name': tag, 'published_at': '2026-11-06T12:00:00Z', 'assets': [
            {'name': f'{product}-{version}.zip', 'state': 'uploaded', 'size': 10,
             'browser_download_url': 'https://example.org/agent.zip'},
            {'name': name, 'state': 'uploaded', 'size': 100,
             'browser_download_url': f'https://github.com/Gurux/Gurux.DLMS.AMI4/releases/download/{tag}/{name}'}]}
        metadata = {'schemaVersion': 1, 'id': product, 'version': version,
                    'targetFramework': 'net10.0',
                    'references': [{'name': 'Gurux.DLMS.AMI.Script.dll', 'version': '4.1.2611.301'}]}
        return source, release, metadata

    def build(self, source, release, metadata):
        return build_catalog({'items': [source]}, lambda repo: [release],
                             fetch_metadata=lambda url: metadata)['items'][0]['releases'][0]

    def test_script_dependency_is_copied_from_release_sidecar(self):
        source, release, metadata = self.fixture()
        self.assertEqual(self.build(source, release, metadata)['references'], metadata['references'])

    def test_old_agent_without_sidecar_keeps_download(self):
        source, release, metadata = self.fixture()
        release['assets'].pop()
        result = self.build(source, release, metadata)
        self.assertNotIn('references', result)
        self.assertEqual(result['assets'][0]['downloadUrl'], 'https://example.org/agent.zip')

    def test_invalid_agent_metadata_is_rejected(self):
        for mode in ('identity', 'other_dependency', 'duplicate', 'unpublished'):
            source, release, metadata = self.fixture()
            if mode == 'identity':
                metadata['version'] = '0.0.0.1'
            elif mode == 'other_dependency':
                metadata['references'] = [{'name': 'Other.dll', 'version': '1.0.0.0'}]
            elif mode == 'duplicate':
                release['assets'].append(dict(release['assets'][1]))
            else:
                release['assets'][1]['state'] = 'new'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.build(source, release, metadata)


if __name__ == '__main__':
    unittest.main()
