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
    def test_approved_idea_satisfies_planning_dependency(self):
        self.store.node('idea')['planningStatus']='applied'
        self.n['dependsOn']=['idea']
        self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        self.assertEqual(self.n['status'],'running')
    def test_planning_does_not_complete_execution_dependency(self):
        dependency=self.store.node('idea');dependency.update(kind='execution',planningStatus='applied',status='ready')
        self.n['dependsOn']=['idea']
        with self.assertRaisesRegex(ValueError,'依赖节点'):
            self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
    def test_execution_gets_only_project_write_scope(self):
        rpc=object.__new__(s.CodexRPC);calls=[]
        def call(method,params,*args):
            calls.append((method,params));return {'thread':{'id':'t'},'turn':{'id':'v'}}
        rpc.call=call
        n=copy.deepcopy(self.n);n['attempts']=[{'kind':'execution'}]
        n['clientUserMessageId']='check';rpc.start(n,'write file')
        params=calls[-1][1]
        self.assertEqual(params['sandboxPolicy'],{'type':'workspaceWrite','writableRoots':[n['cwd']],'networkAccess':False})
        self.assertEqual(params['approvalPolicy'],'on-request')
    def test_planning_stays_read_only(self):
        rpc=object.__new__(s.CodexRPC);calls=[]
        rpc.call=lambda method,params,*args: (calls.append((method,params)) or {'thread':{'id':'t'},'turn':{'id':'v'}})
        n=copy.deepcopy(self.n);n['attempts']=[{'kind':'planning'}];n['clientUserMessageId']='check'
        rpc.start(n,'plan');self.assertEqual(calls[-1][1]['sandboxPolicy'],{'type':'readOnly'})
    def test_unapproved_working_directory_cannot_start(self):
        rpc=object.__new__(s.CodexRPC);n=copy.deepcopy(self.n);n['cwd']='/tmp'
        with self.assertRaisesRegex(ValueError,'项目目录'):rpc.start(n,'test')
    def test_rejected_requests_do_not_consume_redo_allowance(self):
        self.store.data['limits']['maxRedo']=1
        self.n['attempts']=[{'kind':'execution','modelRunReserved':True},{'kind':'execution','modelRunReserved':False},{'kind':'execution','modelRunReserved':False}]
        self.app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'retry'})
        self.assertEqual(self.n['status'],'running')
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
    def test_model_listing_does_not_reuse_execution_rpc(self):
        made=[]
        def factory():
            rpc=FakeRPC();made.append(rpc);return rpc
        app=s.App(self.store,factory)
        app.models()
        app.execute({'nodeId':'plan','revision':self.n['revision'],'prompt':'test'})
        self.assertEqual(len(made),2)
        self.assertIs(app.model_rpc,made[0])
        self.assertIs(app.rpc,made[1])
        self.assertEqual(made[0].calls,[])
        self.assertEqual(len(made[1].calls),1)
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
    def test_manual_added_step_is_runnable_execution_node(self):
        child=self.app.add_child({'parentId':'idea','title':'手动做这一步'})
        self.assertEqual(child['kind'],'execution')
        self.app.execute({'nodeId':child['id'],'revision':child['revision'],'prompt':'test'})
        self.assertEqual(child['status'],'running')
    def test_pending_proposal_blocks_second_plan_until_discarded(self):
        self.store.data['pendingProposal']={'proposalId':'one','turnId':'turn-one','parentId':'idea','nodes':[{'tempId':'a','title':'A','kind':'execution'}]}
        with self.assertRaisesRegex(ValueError,'上一份拆分结果'):
            self.app.plan({'parentId':'plan','idea':'B'})
        self.assertEqual(self.store.data['pendingProposal']['proposalId'],'one')
        self.assertEqual(self.rpc.calls,[])
        self.app.discard_proposal({'proposalId':'one'})
        self.assertEqual(self.store.node('idea')['planningStatus'],'discarded')
        self.app.plan({'parentId':'plan','idea':'B'})
        self.assertEqual(self.store.node('plan')['planningStatus'],'running')
    def test_pending_proposal_survives_reopen(self):
        self.store.data['pendingProposal']={'proposalId':'one','turnId':'turn-one','parentId':'idea','nodes':[{'tempId':'a','title':'A','kind':'execution'}]}
        self.store.write()
        reopened=s.Store()
        self.assertEqual(reopened.data['pendingProposal']['proposalId'],'one')
    def test_thread_start_timeout_is_preparation_failure_without_model_budget(self):
        def timeout_start(n,prompt):
            e=s.RPCReceiptTimeout('thread/start',77,'fake-rpc');e.phase='thread/start';raise e
        self.rpc.start=timeout_start
        with self.assertRaisesRegex(RuntimeError,'回执超时'): self.app.plan({'parentId':'plan','idea':'test'})
        self.assertEqual(self.n['planningStatus'],'preparation_failed')
        self.assertEqual(self.store.data['runsUsed'],0)
        self.assertEqual(self.n['attempts'][-1]['phase'],'thread/start')
        self.assertFalse(self.n['attempts'][-1]['modelTurnStarted'])
    def test_late_reply_requires_the_same_rpc_instance(self):
        self.rpc.instance_id='new-rpc'
        self.n['attempts']=[{'rpcRequestId':7,'rpcInstanceId':'old-rpc','phase':'thread/start','state':'preparation_failed'}]
        out=self.app.recover_late_reply({'nodeId':'plan'})
        self.assertEqual(out['status'],'unavailable')
        self.assertEqual(self.n['attempts'][0]['rpcRequestId'],7)
    def test_corrupt_state_is_preserved_without_resetting_budget(self):
        s.STATE.write_text('{invalid-json')
        with self.assertRaises(RuntimeError): s.Store()
        self.assertEqual(s.STATE.read_text(),'{invalid-json')

if __name__=='__main__': unittest.main()
