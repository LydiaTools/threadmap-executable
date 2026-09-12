#!/usr/bin/env python3
"""Loopback-only, allow-listed Codex task-map executor."""
import argparse, collections, copy, json, os, secrets, subprocess, threading, time, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT=Path(__file__).resolve().parent; STATE=ROOT/'threadmap_state.json'
CODEX='/Applications/ChatGPT.app/Contents/Resources/codex'
ALLOWED_CWDS={'/Users/macmini/Developer/threadmap-executable-design','/Users/macmini/Documents/ChatGPT/我的口播视频'}
FINAL={'passed','needs_rework','interrupted','failed'}; BUSY={'sent','running','receipt_unknown'}

def initial_state():
  common={'locked':False,'status':'ready','revision':1,'project':'任务脉络本地执行器','cwd':str(ROOT),'suggested':{'model':'gpt-5.6-luna','effort':'low'},'requested':{},'actual':{},'children':[],'dependsOn':[],'attempts':[],'artifacts':[]}
  def node(i,k,t,**kw): return dict(copy.deepcopy(common),id=i,kind=k,title=t,**kw)
  return {'version':2,'limits':{'maxRuns':8,'maxRedo':2,'maxConcurrent':1},'runsUsed':0,'appliedProposalTurns':[], 'nodes':[
    node('root','decision','我想做的事',locked=True,children=['idea','business','tests']),
    node('idea','idea','新想法',delivery='把想法拆成几步，方便逐步完成',acceptance='每一步都知道要做什么、做好是什么样'),
    node('business','decision','拼豆这期视频',locked=True,project='我的口播视频',children=['tool','photo','video']),
    node('tool','execution','做照片转拼豆图纸工具',locked=True,project='我的口播视频',threadId='01a094be-a3b4-7841-8114-8f0a43cd9074',delivery='可上传、生成、导出',acceptance='真实照片操作通过'),
    node('photo','evidence','用宠物照片试效果',locked=True,status='waiting_input',project='我的口播视频',delivery='真实证据',acceptance='图纸、豆数与效果一致'),
    node('video','execution','制作口播视频',locked=True,project='我的口播视频',threadId='01a094be-efe9-7571-bdf0-aea8dc19a997',delivery='真实演示成片',acceptance='展示真实工具与结果'),
    node('tests','decision','已做过的测试',locked=True,collapsed=True,children=['selftest','fixture']),
    node('plan','execution','测试兼容节点',project='任务脉络本地执行器',delivery='测试',acceptance='测试'),
    node('selftest','execution','已通过｜本工具自管 LOCAL-OK',locked=True,status='passed',threadId='01a0950c-f9aa-7252-8a7e-e4c7af240865',turnId='01a0950c-fbf0-7b00-acb0-5070ff79ddb6',result='LOCAL-OK',delivery='独立短回答',acceptance='人工确认 LOCAL-OK'),
    node('fixture','execution','受阻｜桌面任务被占用，未派发',locked=True,status='blocked_not_sent',threadId='01a09505-33b5-74e3-b314-d842f6631ac5',delivery='续跑桌面验收任务',acceptance='必须有真实 turnId') ]}

class Store:
  def __init__(self): self.lock=threading.RLock(); self.data=self.load()
  def load(self):
    if STATE.exists():
      try:return json.loads(STATE.read_text())
      except (json.JSONDecodeError,OSError) as e:
        raise RuntimeError('本地状态无法读取，已停止启动并保留原文件；请从总入口恢复，不能重置执行记录') from e
    d=initial_state(); self.write(d); return d
  def write(self,d=None):
    p=STATE.with_suffix('.tmp'); p.write_text(json.dumps(d or self.data,ensure_ascii=False,indent=2)); p.replace(STATE)
  def node(self,i):
    for n in self.data['nodes']:
      if n['id']==i:return n
    raise ValueError('找不到节点')

class CodexRPC:
  def __init__(self,binary=CODEX):
    if not os.path.isfile(binary) or not os.access(binary,os.X_OK):raise RuntimeError('未找到桌面版 Codex binary')
    self.p=subprocess.Popen([binary,'app-server','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
    self.lock=threading.RLock(); self.pending={}; self.events=collections.deque(maxlen=200); self.seq=0
    threading.Thread(target=self.reader,daemon=True).start()
    self.call('initialize',{'clientInfo':{'name':'threadmap_local_executor','title':'任务脉络本地执行器','version':'0.2.0'},'capabilities':{'experimentalApi':True}}); self.send({'method':'initialized','params':{}})
  def send(self,m):self.p.stdin.write(json.dumps(m,ensure_ascii=False)+'\n');self.p.stdin.flush()
  def reader(self):
    for line in self.p.stdout:
      try:m=json.loads(line)
      except json.JSONDecodeError:continue
      with self.lock:
        if 'id' in m:self.pending[str(m['id'])]=m
        else:self.events.append(m)
  def call(self,method,params,timeout=25):
    with self.lock:self.seq+=1;rid=self.seq;self.send({'id':rid,'method':method,'params':params})
    end=time.time()+timeout
    while time.time()<end:
      with self.lock:r=self.pending.pop(str(rid),None)
      if r:
        if 'error'in r:raise RuntimeError(r['error'].get('message','Codex 返回错误'))
        return r.get('result',{})
      if self.p.poll()is not None:raise RuntimeError('Codex App Server 已退出')
      time.sleep(.05)
    raise RuntimeError('等待 Codex 回执超时；请求状态未知，请勿重发')
  def models(self):
    r=self.call('model/list',{},15);return r.get('data',r.get('models',[]))
  def start(self,n,prompt):
    req=n['requested'] or n['suggested']; new=not n.get('threadId')
    if new:
      r=self.call('thread/start',{'model':req['model'],'cwd':n['cwd']});tid=r.get('thread',{}).get('id')
      if not tid:raise RuntimeError('创建任务回执不完整；请求状态未知，请勿重发')
      n['threadId']=tid
    else:
      self.call('thread/resume',{'threadId':n['threadId'],'excludeTurns':True});tid=n['threadId']
    p={'threadId':tid,'input':[{'type':'text','text':prompt,'text_elements':[]}],'model':req['model'],'effort':req['effort'],'clientUserMessageId':n['clientUserMessageId']}
    if new:p['cwd']=n['cwd']
    r=self.call('turn/start',p,30);turn=r.get('turn',{})
    if not turn.get('id'):raise RuntimeError('派发回执不完整；请求状态未知，请勿重发')
    return tid,turn
  def matching_metadata(self,thread_id,turn_id):
    actual={'model':'unknown','effort':'unknown'};usage='unknown'
    def walk(x):
      if isinstance(x,dict):
        if x.get('threadId')==thread_id and x.get('turnId')==turn_id:
          if x.get('model'):actual['model']=x['model']
          if x.get('effort'):actual['effort']=x['effort']
          if x.get('usage')is not None:return x['usage']
        for v in x.values():
          u=walk(v)
          if u is not None:return u
      elif isinstance(x,list):
        for v in x:
          u=walk(v)
          if u is not None:return u
    with self.lock:
      for e in list(self.events):
        u=walk(e)
        if u is not None:usage=u
    return actual,usage

class App:
  def __init__(self,store,rpc_factory=CodexRPC):self.store=store;self.rpc_factory=rpc_factory;self.rpc=None;self.rpc_lock=threading.Lock();self.undo=[];self.model_caps=None
  def rpc_client(self):
    with self.rpc_lock:
      if not self.rpc or self.rpc.p.poll()is not None:self.rpc=self.rpc_factory()
      return self.rpc
  def clear_undo_boundary(self):self.undo.clear()
  def state(self):return self.store.data
  def models(self):
    try:
      ms=self.rpc_client().models();self.model_caps={m['id']:{e['reasoningEffort'] if isinstance(e,dict) else e for e in m.get('supportedReasoningEfforts',[])} for m in ms};return {'models':ms,'connected':True}
    except Exception as e:return {'models':[],'connected':False,'error':str(e)}
  def valid_model(self,req):
    if not self.model_caps:self.models()
    return bool(self.model_caps and req.get('model')in self.model_caps and req.get('effort')in self.model_caps[req['model']])
  def reserve(self,n,kind,prompt):
    lim=self.store.data['limits'];active=sum(x.get('status')in BUSY or x.get('planningStatus')in('sent','running','receipt_unknown') for x in self.store.data['nodes'])
    if self.store.data['runsUsed']>=lim['maxRuns']:raise ValueError('已到服务器最大轮次，未派发')
    if active>=lim['maxConcurrent']:raise ValueError('已到服务器并发上限，未派发')
    req=n.get('requested')or n.get('suggested')or{}
    if not self.valid_model(req):raise ValueError('模型与 effort 组合当前不可用，未派发')
    oid=str(uuid.uuid4());cid=str(uuid.uuid4());n['operationId']=oid;n['clientUserMessageId']=cid;self.store.data['runsUsed']+=1
    a={'operationId':oid,'clientUserMessageId':cid,'kind':kind,'revision':n['revision'],'requested':copy.deepcopy(req),'inputSnapshot':prompt,'at':time.time(),'state':'reserved'};n.setdefault('attempts',[]).append(a);self.clear_undo_boundary();return a
  def execute(self,b):
    with self.store.lock:
      n=self.store.node(b.get('nodeId'));prompt=str(b.get('prompt','')).strip()
      if n.get('kind')!='execution' or n.get('status')!='ready':raise ValueError('只有就绪的执行节点可以派发')
      if b.get('revision')!=n['revision'] or not prompt:raise ValueError('节点版本变化或缺少执行要求')
      for d in n.get('dependsOn',[]):
        if self.store.node(d).get('status')!='passed':raise ValueError('依赖节点尚未通过，未派发')
      redo=sum(a.get('kind')=='execution' for a in n.get('attempts',[]))-1
      if redo>=self.store.data['limits']['maxRedo'] and n.get('attempts'):raise ValueError('已到最大返工次数，未派发')
      a=self.reserve(n,'execution',prompt);n['status']='sent';self.store.write()
    try:tid,turn=self.rpc_client().start(n,prompt)
    except Exception as e:
      with self.store.lock:
        msg=str(e);blocked='already has an active writer'in msg
        n['status']='blocked_not_sent' if blocked else 'receipt_unknown';a['state']=n['status'];a['error']=('桌面任务正在占用，未派发' if blocked else msg)
        if blocked:self.store.data['runsUsed']-=1
        self.store.write()
      raise RuntimeError(a['error'])
    with self.store.lock:n['status']='running';n['turnId']=turn['id'];a.update(state='running',threadId=tid,turnId=turn['id']);self.store.write()
    return {'node':n,'message':'已收到真实 turnId；完成后仍需人工验收'}
  def plan(self,b):
    idea=str(b.get('idea','')).strip()
    with self.store.lock:
      n=self.store.node(b.get('parentId'))
      if not idea or n.get('locked'):raise ValueError('选择可编辑节点并输入想法')
      if n.get('planningStatus')in BUSY:raise ValueError('该节点已有规划运行或回执未知')
      locked=[{'id':x['id'],'title':x['title']} for x in self.store.data['nodes'] if x.get('locked')][:20]
      near=[{'id':x['id'],'title':x['title'],'status':x.get('status')} for x in self.store.data['nodes'] if x['id']in n.get('children',[])+n.get('dependsOn',[])][:20]
      ctx={'nodeId':n['id'],'title':n['title'],'delivery':n.get('delivery',''),'acceptance':n.get('acceptance',''),'lockedDecisions':locked,'adjacent':near}
      prompt='仅规划，不执行不改文件。上下文：'+json.dumps(ctx,ensure_ascii=False)+'。用户想法：'+idea+'。只输出JSON：{"nodes":[{"tempId":"n1","title":"","kind":"execution|evidence|idea|decision","delivery":"","acceptance":"","dependsOn":["n1或现有nodeId"],"suggested":{"model":"gpt-5.6-luna","effort":"low"}}],"assumptions":[],"reason":""}。tempId必须唯一；不得修改锁定项。'
      prompt+='。最低成本分工：固定转换与统计优先脚本；有材料的提取、文案、整理推荐Luna low/medium；小工具代码和真实媒体操作推荐Terra medium；只有商业取舍、关键矛盾或已有失败证据才推荐Sol/Astra。每个执行节点说明交付和验收，不保证未经实测的降档零损失。'
      a=self.reserve(n,'planning',prompt);n['planningStatus']='sent';n['planningTurnId']=None;self.store.write()
    try:tid,turn=self.rpc_client().start(n,prompt)
    except Exception as e:
      with self.store.lock:
        blocked='already has an active writer'in str(e);n['planningStatus']='blocked_not_sent' if blocked else 'receipt_unknown';a['state']=n['planningStatus'];a['error']=('桌面任务正在占用，未派发' if blocked else str(e));self.store.data['runsUsed']-=1 if blocked else 0;self.store.write()
      raise RuntimeError(a['error'])
    with self.store.lock:n['planningStatus']='running';n['planningTurnId']=turn['id'];a.update(state='running',threadId=tid,turnId=turn['id']);self.store.write()
    return {'turnId':turn['id'],'status':'running'}
  def refresh(self,b):
    with self.store.lock:
      n=self.store.node(b.get('nodeId'));planning=bool(n.get('planningTurnId'));turn_id=n.get('planningTurnId')or n.get('turnId')
    if not n.get('threadId')or not turn_id:raise ValueError('没有可刷新的真实回合')
    r=self.rpc_client().call('thread/turns/list',{'threadId':n['threadId'],'limit':8,'itemsView':'full','sortDirection':'desc'},25);t=next((x for x in r.get('data',[])if x.get('id')==turn_id),None)
    if not t:return {'status':'unknown','message':'未读到该 turn，不会自动重发'}
    text=''.join(i.get('text','')for i in t.get('items',[])if i.get('type')=='agentMessage'and i.get('phase')!='commentary');status=t.get('status','unknown')
    rpc=self.rpc_client(); actual,usage=rpc.matching_metadata(n['threadId'],turn_id) if hasattr(rpc,'matching_metadata') else ({'model':'unknown','effort':'unknown'},'unknown');actual={'model':t.get('model',actual['model']),'effort':t.get('effort',actual['effort'])};usage=t.get('usage',usage)
    out={'status':status,'result':text or None,'usage':usage}
    with self.store.lock:
      if status=='completed':
        if planning:
          n['planningStatus']='completed';n['planningResult']=text
          existing=self.store.data.get('pendingProposal')
          if existing and existing.get('turnId')==turn_id:out['proposal']=existing
          elif turn_id not in self.store.data.get('appliedProposalTurns',[]):
            try:p=json.loads(text.strip());self.validate_proposal(p);p.update(proposalId=str(uuid.uuid4()),parentId=n['id'],turnId=turn_id);self.store.data['pendingProposal']=p;out['proposal']=p
            except Exception:out['proposalError']='回复不是有效任务图 JSON，保留原文且未改图'
        else:
          if n.get('status')not in FINAL:n['status']='pending_acceptance';n['result']=text;n['actual']=actual;n['usage']=usage
      self.store.write()
    return out
  def validate_proposal(self,p):
    xs=p.get('nodes')if isinstance(p,dict)else None
    if not isinstance(xs,list)or not xs:raise ValueError('提案没有节点')
    ids=[x.get('tempId')for x in xs]
    if any(not isinstance(i,str)or not i for i in ids)or len(ids)!=len(set(ids)):raise ValueError('提案临时ID无效或重复')
    for x in xs:
      if x.get('kind')not in {'execution','evidence','idea','decision'}or not isinstance(x.get('title'),str):raise ValueError('提案节点无效')
  def apply_proposal(self,b):
    with self.store.lock:
      p=self.store.data.get('pendingProposal')
      if not p or p.get('proposalId')!=b.get('proposalId'):raise ValueError('待应用提案不存在或已应用')
      parent=self.store.node(p['parentId']);self.validate_proposal(p);existing={x['id'] for x in self.store.data['nodes']};mapping={x['tempId']:'node_'+uuid.uuid4().hex[:10]for x in p['nodes']};prepared=[]
      graph={}
      for x in p['nodes']:
        deps=[mapping.get(d,d)for d in x.get('dependsOn',[])]
        if any(d not in existing|set(mapping.values())or d==mapping[x['tempId']]for d in deps):raise ValueError('提案含未知或自引用依赖')
        graph[mapping[x['tempId']]]=[d for d in deps if d in mapping.values()]
        prepared.append(dict(id=mapping[x['tempId']],kind=x['kind'],title=x['title'],locked=False,status='ready',revision=1,project=parent.get('project',''),threadId='',cwd=parent.get('cwd',str(ROOT)),suggested=x.get('suggested',{'model':'gpt-5.6-luna','effort':'low'}),requested={},actual={},delivery=x.get('delivery',''),acceptance=x.get('acceptance',''),dependsOn=deps,children=[],attempts=[],artifacts=[]))
      def cycle(v,path):return v in path or any(cycle(q,path|{v})for q in graph.get(v,[]))
      if any(cycle(v,set())for v in graph):raise ValueError('提案依赖形成循环')
      self.clear_undo_boundary();self.store.data['nodes'].extend(prepared);parent.setdefault('children',[]).extend(x['id']for x in prepared);parent['revision']+=1;self.store.data.setdefault('appliedProposalTurns',[]).append(p['turnId']);del self.store.data['pendingProposal'];self.store.write();return {'applied':prepared}
  def edit_node(self,b):
    with self.store.lock:
      n=self.store.node(b.get('nodeId'))
      if n.get('status')in BUSY|{'pending_acceptance','passed'}or n.get('planningStatus')in BUSY:raise ValueError('当前状态不能修改执行语义')
      if b.get('revision')!=n['revision']:raise ValueError('节点版本已变化')
      if n.get('locked')and b.get('locked')is not False and set(b)-{'nodeId','revision','collapsed'}:raise ValueError('锁定节点只能折叠或先解锁')
      before=copy.deepcopy(n)
      for k in ('title','delivery','acceptance','locked','collapsed','requested'):
        if k in b:n[k]=b[k]
      if n.get('requested')and not self.valid_model(n['requested']):n.clear();n.update(before);raise ValueError('模型与 effort 组合当前不可用')
      if before.get('status')=='needs_rework' and any(k in b for k in ('title','delivery','acceptance','requested')):n['status']='ready'
      n['revision']+=1;self.undo.append({'type':'edit','id':n['id'],'before':before,'afterRevision':n['revision']});self.undo=self.undo[-10:];self.store.write();return n
  def add_child(self,b):
    with self.store.lock:
      p=self.store.node(b.get('parentId'))
      if p.get('locked')or p.get('planningStatus')in BUSY:raise ValueError('父节点当前不能添加')
      i='node_'+uuid.uuid4().hex[:10];n=copy.deepcopy(initial_state()['nodes'][1]);n.update(id=i,title=str(b.get('title')or'新的待评审节点')[:120],kind=b.get('kind','idea'),children=[]);self.store.data['nodes'].append(n);p.setdefault('children',[]).append(i);p['revision']+=1;self.undo.append({'type':'add','id':i,'parentId':p['id'],'parentRevision':p['revision']});self.store.write();return n
  def undo_edit(self):
    with self.store.lock:
      if not self.undo:raise ValueError('没有可撤销的纯编辑')
      u=self.undo.pop()
      if u['type']=='edit':
        n=self.store.node(u['id'])
        if n['revision']!=u['afterRevision']or n.get('attempts'):raise ValueError('已有执行记录，不能撤销这次编辑')
        n.clear();n.update(u['before'])
      else:
        n=self.store.node(u['id']);p=self.store.node(u['parentId'])
        if n.get('attempts')or n.get('artifacts')or n.get('children'):raise ValueError('子节点已有记录，不能撤销')
        self.store.data['nodes'].remove(n);p['children'].remove(n['id']);p['revision']+=1
      self.store.write();return self.store.data
  def accept(self,b,ok):
    with self.store.lock:
      n=self.store.node(b.get('nodeId'));ev=str(b.get('evidence','')).strip()
      if n.get('kind')!='execution'or n.get('status')!='pending_acceptance':raise ValueError('只有待验收执行节点可通过或退回')
      if b.get('turnId')!=n.get('turnId')or b.get('revision')!=n['revision']:raise ValueError('验收不属于当前 turn 或节点版本')
      if not ev:raise ValueError('必须填写证据')
      self.clear_undo_boundary();n.setdefault('artifacts',[]).append({'evidence':ev,'turnId':n['turnId'],'revision':n['revision'],'at':time.time()});n['status']='passed'if ok else'needs_rework';n['revision']+=0 if ok else 1;self.store.write();return n
  def human_confirm(self,b):
    with self.store.lock:
      n=self.store.node(b.get('nodeId'));ev=str(b.get('evidence','')).strip()
      if n.get('kind')not in {'decision','evidence'}or n.get('status')in BUSY:raise ValueError('仅决定或证据节点可人工确认')
      if not ev:raise ValueError('必须填写证据')
      self.clear_undo_boundary();n.setdefault('artifacts',[]).append({'evidence':ev,'revision':n['revision'],'at':time.time()});n['status']='passed';self.store.write();return n

def make_handler(app,token):
 class H(SimpleHTTPRequestHandler):
  def log_message(self,*a):pass
  def safe(self):
   h=self.headers.get('Host','');o=self.headers.get('Origin','');base=f'127.0.0.1:{self.server.server_port}';return h in(base,f'localhost:{self.server.server_port}')and(not o or o in(f'http://{base}',f'http://localhost:{self.server.server_port}'))
  def sendj(self,c,x):
   b=json.dumps(x,ensure_ascii=False).encode();self.send_response(c);self.send_header('Content-Type','application/json;charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
  def do_GET(self):
   if not self.safe():return self.sendj(403,{'error':'仅允许本机页面'})
   if self.path=='/api/state':return self.sendj(200,app.state())
   if self.path=='/api/models':return self.sendj(200,app.models())
   if self.path=='/api/csrf':return self.sendj(200,{'token':token})
   if self.path=='/':self.path='/index.html'
   return super().do_GET()
  def do_POST(self):
   if not self.safe()or self.headers.get('X-CSRF-Token')!=token:return self.sendj(403,{'error':'来源或CSRF校验失败'})
   try:b=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
   except Exception:return self.sendj(400,{'error':'请求不是JSON'})
   routes={'/api/execute':app.execute,'/api/plan':app.plan,'/api/refresh':app.refresh,'/api/apply-proposal':app.apply_proposal,'/api/node/edit':app.edit_node,'/api/node/add':app.add_child,'/api/undo':lambda _:app.undo_edit(),'/api/accept':lambda x:app.accept(x,True),'/api/return':lambda x:app.accept(x,False),'/api/human-confirm':app.human_confirm}
   try:
    if self.path not in routes:return self.sendj(404,{'error':'不允许的操作'})
    self.sendj(200,routes[self.path](b))
   except(ValueError,RuntimeError)as e:self.sendj(409,{'error':str(e)})
 return H

def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8771);a=p.parse_args();s=ThreadingHTTPServer(('127.0.0.1',a.port),make_handler(App(Store()),secrets.token_urlsafe(32)));print(f'任务脉络仅本机运行：http://127.0.0.1:{a.port}/',flush=True);s.serve_forever()
if __name__=='__main__':main()
