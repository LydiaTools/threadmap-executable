"""Controller regression checks: no real Codex calls, temporary state only."""
import copy
import tempfile
import unittest
from pathlib import Path
import threadmap_server as s

class FakeRPC:
    def __init__(self):
        self.p=type('Process',(),{'poll':lambda self:None})()
        self.calls=[]
    def models(self):
        return [{'id':m,'model':m,'supportedReasoningEfforts':[{'reasoningEffort':e} for e in ['low','medium']]} for m in ['gpt-5.6-luna','gpt-5.6-terra']]
    def start(self,n,prompt):
        self.calls.append(copy.deepcopy(n))
        return 'local-test',{'id':'turn-test','status':'inProgress'}
    def call(self,method,params,*args,**kwargs):
        if method=='thread/turns/list':
            return {'data':[{'id':'turn-test','status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'OK'}]}]}
        return {}

class ControllerAcceptance(unittest.TestCase):
    def test_initial_nodes_have_independent_mutable_collections(self):
        data=s.initial_state(); nodes=data['nodes']
        for field in ('attempts','artifacts','children','dependsOn'):
            containers=[n[field] for n in nodes if field in n]
            self.assertEqual(len(containers),len({id(value) for value in containers}),field)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=s.STATE; s.STATE=Path(self.tmp.name)/'state.json'
        self.store=s.Store(); self.rpc=FakeRPC(); self.app=s.App(self.store,lambda:self.rpc)
        self.n=self.store.node('plan'); self.n.update(locked=False,status='ready',threadId='',requested={'model':'gpt-5.6-luna','effort':'low'},attempts=[],artifacts=[])
    def tearDown(self):
        s.STATE=self.old; self.tmp.cleanup()
    def test_model_edit_reaches_dispatch(self):
        self.app.edit_node({'nodeId':'plan','revision':self.n['revision'],'requested':{'model':'gpt-5.6-terra','effort':'medium'}})
        self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        self.assertEqual(self.rpc.calls[-1]['requested'],{'model':'gpt-5.6-terra','effort':'medium'})
    def test_refresh_keeps_accepted_result(self):
        self.n.update(status='passed',threadId='local-test',turnId='turn-test',result='OK')
        self.n['attempts']=[{'state':'completed','revision':self.n['revision'],'turnId':'turn-test'}]
        self.app.refresh({'nodeId':'plan'})
        self.assertEqual(self.n['status'],'passed')
    def test_edit_running_delivery_cannot_be_accepted_as_new_version(self):
        self.n.update(status='running',threadId='local-test',turnId='turn-test')
        with self.assertRaises(ValueError):
            self.app.edit_node({'nodeId':'plan','revision':self.n['revision'],'delivery':'changed'})
    def test_planning_obeys_global_budget(self):
        self.store.data['limits']['maxRuns']=0
        with self.assertRaises(ValueError): self.app.plan({'parentId':'plan','idea':'test'})
        self.assertEqual(self.rpc.calls,[])
    def test_unknown_receipt_cannot_redispatch(self):
        self.n['status']='receipt_unknown'
        with self.assertRaises(ValueError): self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        self.assertEqual(self.rpc.calls,[])
    def test_undo_never_erases_started_run_or_budget(self):
        self.app.edit_node({'nodeId':'plan','revision':self.n['revision'],'title':'edited'})
        self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        used=self.store.data['runsUsed']; attempts=copy.deepcopy(self.n['attempts'])
        try: self.app.undo_edit()
        except ValueError: pass
        self.assertEqual(self.store.data['runsUsed'],used)
        self.assertEqual(self.store.node('plan')['attempts'],attempts)
        self.assertEqual(self.store.node('plan')['status'],'running')
    def test_invalid_proposal_is_atomic(self):
        self.store.data['pendingProposal']={'proposalId':'bad','parentId':'plan','nodes':[
            {'id':'a','title':'first','kind':'idea','dependsOn':[]},
            {'id':'b','title':'second','kind':'execution','dependsOn':['missing']} ]}
        before=copy.deepcopy(self.store.data)
        with self.assertRaises(ValueError): self.app.apply_proposal({'proposalId':'bad'})
        self.assertEqual(self.store.data,before)
    def test_corrupt_state_is_preserved_without_resetting_budget(self):
        s.STATE.write_text('{invalid-json')
        with self.assertRaises(RuntimeError): s.Store()
        self.assertEqual(s.STATE.read_text(),'{invalid-json')

if __name__=='__main__': unittest.main()
