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


if __name__ == '__main__':
    unittest.main()
