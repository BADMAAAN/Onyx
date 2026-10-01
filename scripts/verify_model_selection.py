"""Real hidden Qt Client -> local IPC -> production executor -> Ollama A/B check."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

from PySide6.QtGui import QGuiApplication
from onyx_desktop.acceptance_scope import validate_acceptance
from onyx_desktop.client import Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    prefs = validate_acceptance(SimpleNamespace(production_acceptance_dir=str(output.parent), backend_root=str(root)), root, Path(__file__).resolve().parents[1])
    app = QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
    client = Client(root, prefs)
    report = dict(root=str(root), checks={}, messages=[], audio_validation='Not attempted; no microphone or playback')

    def wait_for(predicate, label, timeout=180):
        deadline = monotonic()+timeout
        while monotonic()<deadline:
            app.processEvents()
            if predicate(): return
            sleep(.03)
        raise TimeoutError(label+'; '+client.error)

    def check(name, passed):
        report['checks'][name] = bool(passed)
        if not passed: raise AssertionError(name)

    try:
        wait_for(lambda: client.ready and client._historyLoaded and bool(client.models), 'Client initialization')
        client.newChat();cid=client.selected
        wait_for(lambda: client._historyLoaded, 'Blank draft')
        check('blank_draft_not_persisted', all(row['conversation_id'] != cid for row in client.worker.transport.call('desktop.chats')))
        report.update(conversation_id=cid, backend=client.worker.transport.metadata['build'])
        for index,(model,prompt) in enumerate((('qwen3:14b','Answer briefly: what is two plus three?'),('qwen3-vl:8b','What arithmetic result did you give in the previous reply? Answer briefly.')),1):
            client.selectModel(model)
            wait_for(lambda: client.currentModel == model and not client.modelChanging, 'Model acknowledgment')
            client.setDraft(prompt);client.send()
            wait_for(lambda: not client.busy and len(client.messages.rows)==index*2 and client.messages.rows[-1]['status']=='completed', 'Actual model response')
            identity = client.messages.rows[-1]['model_identity']
            check('actual_model_'+str(index), identity.get('actual_model')==model and identity.get('response_completed') is True)
        report['messages']=client.messages.rows
        check('different_runtime_ids_and_digests', report['messages'][1]['model_identity']['actual_model'] != report['messages'][3]['model_identity']['actual_model'] and report['messages'][1]['model_identity']['digest'] != report['messages'][3]['model_identity']['digest'])
        client.saveProject('Model switching evidence', '', '')
        wait_for(lambda: bool(client.projects), 'Project creation')
        pid = client.projects[0]['project_id'];report['project_id']=pid
        client.moveChat(cid, pid);client.pinChat(cid, True)
        wait_for(lambda: any(r['conversation_id']==cid and r.get('project_id')==pid and r['pinned'] for r in client.chats), 'Move/pin persistence')
        check('canonical_move_keeps_identity', len([r for r in client.chats if r['conversation_id']==cid])==1)
        client.preference('startup','last');client.setDraft('Новый черновик — Onyx / English 🪐')
        client.close();client=Client(root,prefs)
        wait_for(lambda: client.ready and client._historyLoaded and len(client.messages.rows)==4 and bool(client.chats), 'Client restart')
        check('restart_same_history', client.selected==cid and [r['message_id'] for r in client.messages.rows]==[r['message_id'] for r in report['messages']])
        check('restart_draft', client.draft=='Новый черновик — Onyx / English 🪐')
        check('restart_model', client.currentModel=='qwen3-vl:8b')
        check('restart_project_pin', any(r['conversation_id']==cid and r.get('project_id')==pid and r['pinned'] for r in client.chats))
        client.pinChat(cid,False);client.moveChat(cid,'')
        wait_for(lambda: any(r['conversation_id']==cid and not r.get('project_id') and not r['pinned'] for r in client.chats), 'Unpin/unmove')
        check('unpin_unmove_keeps_identity', len([r for r in client.chats if r['conversation_id']==cid])==1)
        client.newChat()
        check('new_chat_preserves_history', client.selected!=cid and len(client.worker.transport.call('desktop.history',conversation_id=cid)['messages'])==4)
        report['status']='passed'
    except Exception as exc:
        report['status']='failed';report['error']=repr(exc)
        raise
    finally:
        client.close();output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status=report.get('status'), checks=report['checks'], output=str(output)),indent=2))


if __name__ == '__main__': main()
