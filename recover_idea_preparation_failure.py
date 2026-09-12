#!/usr/bin/env python3
"""One-time, evidence-gated repair for the 2026-09-12 empty-thread timeout."""
import argparse, hashlib, json, shutil, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent; STATE=ROOT/'threadmap_state.json'
DEFAULT_OPERATION='77809fb4-19ee-4658-848f-37e944c29bf5'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
  p=argparse.ArgumentParser();p.add_argument('--operation-id',default=DEFAULT_OPERATION);p.add_argument('--evidence',required=True);a=p.parse_args()
  if not a.evidence.strip():raise SystemExit('必须填写本次诊断证据')
  before=digest(STATE);data=json.loads(STATE.read_text());node=next((x for x in data['nodes'] if x['id']=='idea'),None)
  attempt=next((x for x in node.get('attempts',[]) if x.get('operationId')==a.operation_id),None) if node else None
  if not attempt or attempt.get('state')!='receipt_unknown' or node.get('planningStatus')!='receipt_unknown' or node.get('threadId') or node.get('turnId') or node.get('planningTurnId') or attempt.get('error')!='等待 Codex 回执超时；请求状态未知，请勿重发':raise SystemExit('状态不符合这一次空对话超时或已恢复；未改动任何文件')
  backup=STATE.with_name(f'threadmap_state.before-preparation-recovery-{int(time.time())}.json');shutil.copy2(STATE,backup)
  attempt.update(state='preparation_abandoned',phase='thread/start',modelTurnStarted=False,diagnosticEvidence=a.evidence.strip(),diagnosedAt=time.time(),modelRunReserved=False,recoveredRequestId=attempt.pop('rpcRequestId',None),recoveredRpcInstanceId=attempt.pop('rpcInstanceId',None))
  node['planningStatus']='preparation_abandoned';data['runsUsed']=max(0,data.get('runsUsed',0)-1)
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(STATE)
  print(json.dumps({'beforeSha256':before,'backup':str(backup),'afterSha256':digest(STATE),'runsUsed':data['runsUsed'],'operationId':a.operation_id},ensure_ascii=False))
if __name__=='__main__':main()
