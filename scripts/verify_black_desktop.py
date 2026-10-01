"""Neutral production-path checks, using only fixture IDs recorded in the report."""
from pathlib import Path
import json
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from onyx_desktop.transport import LocalTransport

out=ROOT/'data/desktop_acceptance/black-live.json'
t=LocalTransport(ROOT);hello=t.connect()
report=dict(pid=hello['pid'],checks={},fixtures=[],turns=[],jobs=[])
def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def check(name,value):report['checks'][name]=value;save();print(name, value,flush=True)

cid='draft:'+uuid.uuid4().hex;report['fixtures'].append(cid);save()
original_network=hello.get('internet','off')
try:
    snapshot=t.call('models.list',conversation_id=cid)
    report['models_before']=snapshot['rows'];save()
    check('embedding_excluded',not next(r for r in snapshot['rows'] if r['id']=='qwen3-embedding:0.6b')['chat'])
    for index,model in enumerate(('qwen3:14b','qwen3-vl:8b')):
        t.call('models.select',conversation_id=cid,model=model)
        rid=uuid.uuid4().hex
        payload=dict(conversation_id=cid,client_request_id=rid,model_id=model,text='Answer briefly: what is two plus three?' if index==0 else 'What arithmetic result did you give in your previous reply? Answer briefly.',spoken=False)
        response=t.call('desktop.submit',**payload)
        check('dedup_'+str(index),t.call('desktop.submit',**payload)['task_id']==response['task_id'])
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            response=t.call('desktop.request',client_request_id=rid)
            if response['status'] not in ('queued','running','cancelling'):break
            time.sleep(.4)
        history=t.call('desktop.history',conversation_id=cid)
        report['turns'].append(dict(payload=payload,response=response,history=history));save()
        assistant=history['messages'][-1]
        check('model_'+str(index),dict(status=response['status'],requested=model,actual=assistant['model_identity'].get('actual_model'),answer=assistant['content']))
    check('history_preserved',len(t.call('desktop.history',conversation_id=cid)['messages'])==4)
    # Local-only API rejection is checked before any runtime download begins.
    t.call('settings.save',key='internet',value='off')
    try:t.call('models.install',kind='pull',model='smollm2:135m',limit=512*1024**2)
    except RuntimeError as exc:check('offline_pull_blocked','Only local mode' in str(exc))
    else:check('offline_pull_blocked',False)
    try:t.call('git.pushPreview',remote='origin')
    except RuntimeError as exc:check('offline_push_blocked','local mode' in str(exc))
    else:check('offline_push_blocked',False)
    # User explicitly authorized a small live download in the acceptance scope.
    t.call('settings.save',key='internet',value='explicit')
    job=t.call('models.install',kind='pull',model='smollm2:135m',limit=512*1024**2)
    report['jobs'].append(job);save()
    duplicate=t.call('models.install',kind='pull',model='smollm2:135m',limit=512*1024**2)
    check('pull_dedup',duplicate['id']==job['id'])
    deadline=time.monotonic()+180;last=''
    while time.monotonic()<deadline:
        state=t.call('models.list',conversation_id=cid)
        job=next(j for j in state['jobs'] if j['id']==job['id'])
        report['jobs'][-1]=job;save()
        progress=job['state']+' '+str(round(job['completed']/1024**2))+' MiB'
        if progress!=last:print(progress,flush=True);last=progress
        if job['state'] not in ('queued','running','cancelling'):break
        time.sleep(.6)
    check('download',dict(state=job['state'],total=job['total'],error=job['error']))
    # Capture ownership checks; no synthetic audio is represented as human speech.
    h=t.call('hello');report['voice']=h.get('readiness');save()
    if h['voice_state']=='listening':
        sid=uuid.uuid4().hex
        session=t.call('dictation.start',session_id=sid,purpose='dictation',conversation_id=cid,revision=0,position=0)
        stopped=t.call('dictation.cancel',session_id=sid)
        check('capture_cancel',session['state']=='recording' and stopped['state']=='cancelled')
        live=t.call('voice.live',conversation_id=cid)
        stopped=t.call('voice.stopLive')
        check('live_same_conversation',live.get('conversation_id')==cid and live.get('started'))
        check('live_stop_backend_alive',t.call('hello')['pid']==hello['pid'])
finally:
    t.call('settings.save',key='internet',value=original_network)
    report['network_restored']=original_network
    t.call('desktop.delete',conversation_id=cid)
    report['fixture_deleted']=True;save()
