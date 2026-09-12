import copy, tempfile, unittest
from pathlib import Path
import threadmap_server as s
from test_controller_acceptance import FakeRPC

class StateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=s.STATE; s.STATE=Path(self.tmp.name)/'s.json'
        self.store=s.Store(); self.rpc=FakeRPC(); self.app=s.App(self.store,lambda:self.rpc)
        self.n=self.store.node('plan'); self.n.update(locked=False,status='ready',threadId='',requested={'model':'gpt-5.6-luna','effort':'low'},attempts=[],artifacts=[])
    def tearDown(self): s.STATE=self.old; self.tmp.cleanup()
    def test_human_evidence_satisfies_dependency(self):
        decision=self.store.node('root'); decision.update(locked=False,status='ready')
        self.app.human_confirm({'nodeId':'root','evidence':'用户确认范围'})
        self.n['dependsOn']=['root']
        self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        self.assertEqual(self.n['status'],'running')
    def test_max_redo_is_enforced(self):
        self.store.data['limits']['maxRedo']=1
        self.n['attempts']=[{'kind':'execution'},{'kind':'execution'}]
        with self.assertRaisesRegex(ValueError,'返工'): self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
    def test_accept_is_bound_to_turn_and_revision(self):
        self.n.update(status='pending_acceptance',turnId='t1')
        with self.assertRaisesRegex(ValueError,'当前 turn'): self.app.accept({'nodeId':'plan','turnId':'other','revision':self.n['revision'],'evidence':'x'},True)
    def test_rework_edit_creates_rerunnable_revision(self):
        self.n.update(status='needs_rework',revision=2)
        self.app.edit_node({'nodeId':'plan','revision':2,'delivery':'修正后的交付'})
        self.assertEqual(self.n['status'],'ready'); self.assertEqual(self.n['revision'],3)
    def test_duplicate_temp_ids_and_cycle_are_atomic(self):
        p={'proposalId':'p','parentId':'plan','turnId':'tp','nodes':[{'tempId':'a','title':'A','kind':'idea','dependsOn':['b']},{'tempId':'b','title':'B','kind':'idea','dependsOn':['a']}]}
        self.store.data['pendingProposal']=p; before=copy.deepcopy(self.store.data)
        with self.assertRaisesRegex(ValueError,'循环'): self.app.apply_proposal({'proposalId':'p'})
        self.assertEqual(self.store.data,before)
if __name__=='__main__': unittest.main()
