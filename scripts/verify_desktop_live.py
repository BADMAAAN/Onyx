"""Neutral local protocol acceptance; no external desktop actions."""
from pathlib import Path
import json
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from onyx_desktop.transport import LocalTransport


def main():
    t=LocalTransport(ROOT);hello=t.connect();checks={'pid':hello['pid']}
    cid=t.call('desktop.create')['conversation_id'];rid='acceptance-'+uuid.uuid4().hex
    payload=dict(conversation_id=cid,client_request_id=rid,text='Explain the lifecycle of stars in twenty detailed paragraphs, from a nebula to its possible final remnants.',spoken=False)
    first=t.call('desktop.submit',**payload)
    duplicate=t.call('desktop.submit',**payload)
    checks['dedup_same_task']=first['task_id']==duplicate['task_id']
    versions=[];deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        r=t.call('desktop.request',client_request_id=rid);versions.append(r['revision'])
        if r['messages'][1]['content'] and r['status']=='running':break
        if r['status'] not in ('queued','running'):break
        time.sleep(.15)
    checks['live_partial_before_cancel']=bool(r['messages'][1]['content']) and r['status']=='running'
    checks['versions_monotonic']=versions==sorted(versions)
    t.call('desktop.cancel',client_request_id=rid)
    while time.monotonic()<deadline:
        r=t.call('desktop.request',client_request_id=rid)
        if r['status'] not in ('queued','running','cancelling'):break
        time.sleep(.15)
    checks['cancel_status']=r['status'];checks['partial_retained']=bool(r['messages'][1]['content'])
    checks['exactly_two_messages']=len(t.call('desktop.history',conversation_id=cid)['messages'])==2
    # A fresh connection must discover the same task; never submit it again.
    reconnect=LocalTransport(ROOT);reconnect.connect()
    checks['reconnect_same_task']=reconnect.call('desktop.request',client_request_id=rid)['task_id']==first['task_id']
    follow='acceptance-'+uuid.uuid4().hex
    t.call('desktop.submit',conversation_id=cid,client_request_id=follow,text='Explain what a Python list is, with one short fenced Python example.',spoken=False)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        r=t.call('desktop.request',client_request_id=follow)
        if r['status'] not in ('queued','running','cancelling'):break
        time.sleep(.2)
    checks['after_stop_status']=r['status'];checks['real_code_block']='```' in r['messages'][-1]['content']
    hello=t.call('hello')
    if hello['voice_ready'] and hello['voice_state']=='listening':
        start=t.call('voice.start',conversation_id=cid)
        checks['voice_start_bound']=start['recording'] and start['conversation_id']==cid
        cancel=t.call('voice.cancel',conversation_id=cid)
        checks['voice_cancel']=not cancel['recording'] and not cancel['pending']
    else:checks['voice_control']='unavailable: '+hello['voice_state']
    checks['same_backend_pid']=t.call('hello')['pid']==checks['pid']
    checks['conversation_id']=cid
    destination=ROOT/'data/desktop_acceptance/live-protocol.json'
    destination.write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print(json.dumps(checks,indent=2))


if __name__=='__main__':main()
