import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_catalog import build_catalog

class ComposeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.version = '4.1.2611.301'
        self.name = f'Gurux.DLMS.AMI-{self.version}.compose.json'
        self.source = {'id':'Gurux.DLMS.AMI','type':'application','description':'AMI','repository':'Gurux/Gurux.DLMS.AMI4','tagPrefix':'Gurux.DLMS.AMI/v','assetPattern':'Gurux.DLMS.AMI-*.zip','composeMetadata':True}
        self.release = {'tag_name':'Gurux.DLMS.AMI/v'+self.version,'published_at':'2026-11-03T12:00:00Z','assets':[{'name':self.name,'state':'uploaded','browser_download_url':f'https://github.com/Gurux/Gurux.DLMS.AMI4/releases/download/Gurux.DLMS.AMI/v{self.version}/{self.name}'}]}
        self.metadata = {'schemaVersion':1,'version':self.version,'compose':{'channel':'stable','platforms':['linux/amd64'],'images':{'ami':'guruxorg/dlms-ami@sha256:'+'a'*64},'containsWasm':True,'rollbackCompatibleFrom':None,'maintenanceRequired':True}}
    def build(self):
        return build_catalog({'items':[self.source]},lambda _: [self.release],fetch_commits=lambda *args: [],fetch_metadata=lambda _:self.metadata)['items'][0]['releases'][0]
    def test_published_digest_metadata_is_attached_to_existing_release(self):
        self.assertEqual(self.build()['compose'],self.metadata['compose'])
    def test_older_release_without_sidecar_remains_available(self):
        self.release['assets']=[]
        self.assertNotIn('compose',self.build())
    def test_unuploaded_sidecar_is_not_advertised(self):
        self.release['assets'][0]['state']='new'
        self.assertNotIn('compose',self.build())
    def test_rejects_invalid_identity_and_compose_contract(self):
        original=copy.deepcopy(self.metadata)
        cases=[('version','other'),('schemaVersion',True)]
        for key,value in cases:
            self.metadata=copy.deepcopy(original); self.metadata[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): self.build()
        for key,value in [('images',{'ami':'guruxorg/dlms-ami:latest'}),('images',{'../bad':'guruxorg/dlms-ami@sha256:'+'a'*64}),('platforms',[]),('channel','latest'),('containsWasm','true'),('maintenanceRequired',False),('rollbackCompatibleFrom','garbage')]:
            self.metadata=copy.deepcopy(original); self.metadata['compose'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.build()
    def test_audited_rollback_range_is_supported(self):
        self.metadata['compose'].update(rollbackCompatibleFrom='[4.1.2611.1,4.1.2611.301]',maintenanceRequired=False)
        self.assertFalse(self.build()['compose']['maintenanceRequired'])
    def test_sidecar_url_must_belong_to_exact_release(self):
        self.release['assets'][0]['browser_download_url']='https://example.test/'+self.name
        with self.assertRaises(ValueError):self.build()

    def test_split_roles_preserve_distinct_digests(self):
        self.metadata['compose']['images']={'api':'guruxorg/dlms-ami@sha256:'+'a'*64,'wasm':'guruxorg/ami-wasm@sha256:'+'b'*64}
        self.assertEqual(self.build()['compose']['images'],self.metadata['compose']['images'])
    def test_stable_channel_cannot_advertise_prerelease(self):
        self.release['prerelease']=True
        with self.assertRaises(ValueError):self.build()
    def test_malformed_platform_is_rejected(self):
        self.metadata['compose']['platforms']=[{}]
        with self.assertRaises(ValueError):self.build()
