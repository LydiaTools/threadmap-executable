import json, tempfile, unittest
from pathlib import Path
import threadmap_server as s

class FakeP:
    def poll(self): return None
class FakeRPC:
    def __init__(self): self.p=FakeP(); self.calls=0
    def models(self): return [{'id':'gpt-5.6-luna','supportedReasoningEfforts':['low']}]
    def start(self,n,prompt): self.calls+=1; return 'thr_test',{'id':'turn_test','model':'gpt-5.6-luna','effort':'low'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.old=s.STATE; self.tmp=Path(tempfile.mkdtemp())/'state.json'; s.STATE=self.tmp; self.store=s.Store(); self.fake=FakeRPC(); self.app=s.App(self.store,lambda:self.fake)
        n=self.store.node('plan'); n['requested']={'model':'gpt-5.6-luna','effort':'low'}
    def tearDown(self): s.STATE=self.old
    def test_execute_then_no_double_dispatch(self):
        n=self.store.node('plan'); result=self.app.execute({'nodeId':'plan','revision':1,'prompt':'只读：列出此目录的文件。'})
        self.assertEqual(result['node']['turnId'],'turn_test'); self.assertEqual(self.fake.calls,1)
        with self.assertRaisesRegex(ValueError,'就绪'): self.app.execute({'nodeId':'plan','revision':1,'prompt':'again'})
    def test_budget_and_conflict(self):
        self.store.data['limits']['maxRuns']=0
        with self.assertRaisesRegex(ValueError,'最大轮次'): self.app.execute({'nodeId':'plan','revision':1,'prompt':'x'})
        self.store.data['limits']['maxRuns']=8
        with self.assertRaisesRegex(ValueError,'版本变化'): self.app.execute({'nodeId':'plan','revision':8,'prompt':'x'})
    def test_reject_requires_evidence(self):
        with self.assertRaisesRegex(ValueError,'待验收'): self.app.accept({'nodeId':'plan','evidence':'x'},False)
        self.store.node('plan').update(status='pending_acceptance',turnId='turn-accept')
        with self.assertRaisesRegex(ValueError,'必须填写'): self.app.accept({'nodeId':'plan','turnId':'turn-accept','revision':1,'evidence':''},False)
        n=self.app.accept({'nodeId':'plan','turnId':'turn-accept','revision':1,'evidence':'人工检查：不通过，缺交付文件。'},False)
        self.assertEqual(n['status'],'needs_rework'); self.assertEqual(n['revision'],2)
    def test_apply_only_valid_real_proposal(self):
        self.store.data['pendingProposal']={'proposalId':'p1','turnId':'turn-plan','parentId':'plan','nodes':[{'tempId':'n1','title':'核对结果','kind':'evidence','delivery':'截图','acceptance':'人工看过','dependsOn':[],'suggested':{'model':'gpt-5.6-luna','effort':'low'}}]}
        out=self.app.apply_proposal({'proposalId':'p1'})
        self.assertEqual(out['applied'][0]['kind'],'evidence')
        self.assertIsNone(self.store.data.get('pendingProposal'))
if __name__=='__main__': unittest.main()
