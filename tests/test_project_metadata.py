import unittest
import test_module_releases as fixtures


class ProjectMetadataTests(unittest.TestCase):
    source = fixtures.ModuleReleaseTests.source
    release = fixtures.ModuleReleaseTests.release
    fixture = fixtures.CompatibilityMetadataTests.fixture
    build = fixtures.CompatibilityMetadataTests.build

    def test_nuget_ranges_include_single_components_and_build_metadata(self):
        for value in ('[1.0.0+build.1,2.0.0)', '[1,2)', '*'):
            source, release, metadata = self.fixture()
            metadata['amiVersionRange'] = value
            with self.subTest(value=value):
                self.assertEqual(self.build(source, release, metadata)['amiVersionRange'], value)
    def test_project_fields_and_release_range_survive_generation(self):
        from build_catalog import build_catalog
        source, release, metadata = self.fixture()
        source.update(maintenanceStatus='active', category='communication', audience='operator')
        metadata['amiVersionRange'] = '[4.1.2610.2701,5.0)'
        item = build_catalog({'items': [source]}, lambda _: [release],
                             fetch_metadata=lambda _: metadata)['items'][0]
        self.assertEqual(item['maintenanceStatus'], 'active')
        self.assertEqual(item['category'], 'communication')
        self.assertEqual(item['audience'], 'operator')
        self.assertEqual(item['releases'][0]['amiVersionRange'], '[4.1.2610.2701,5.0)')

    def test_invalid_project_metadata_is_rejected(self):
        from build_catalog import build_catalog
        for field, value in [('maintenanceStatus', 'typo'), ('category', ''),
                             ('audience', ['developer']), ('category', 'bad slug')]:
            source, release, metadata = self.fixture()
            source[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_catalog({'items': [source]}, lambda _: [release], fetch_metadata=lambda _: metadata)

    def test_invalid_or_empty_release_range_is_rejected(self):
        for value in ('', 'whatever', '[5.0,4.0)', '[4.0,4.0)', '(,)', None):
            source, release, metadata = self.fixture()
            metadata['amiVersionRange'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build(source, release, metadata)

    def test_configuration_metadata_does_not_require_a_release(self):
        from build_catalog import build_catalog
        source = {'id': 'config', 'type': 'configuration', 'repository': 'Gurux/Config',
                  'description': 'Config', 'indexUrl': 'https://example.com/index.json',
                  'maintenanceStatus': 'minimal', 'category': 'configuration', 'audience': 'developer'}
        item = build_catalog({'items': [source]}, lambda _: [])['items'][0]
        self.assertEqual(item['audience'], 'developer')
