"""Offline manifest-based evaluation. Never records or executes a transcript."""
import importlib.util
import json
import re
import statistics
from pathlib import Path
from time import perf_counter


CANDIDATES = {
    'whisper': {'model':'large-v3-turbo','dependency':'faster_whisper','source':'https://github.com/SYSTRAN/faster-whisper'},
    'gigaam': {'model':'v3_e2e_rnnt','dependency':'gigaam','source':'https://github.com/salute-developers/GigaAM'},
    'gigaam-multilingual': {'model':'multilingual_ctc','dependency':'gigaam','source':'https://github.com/salute-developers/GigaAM'},
    'parakeet': {'model':'nvidia/parakeet-tdt-0.6b-v3','revision':'541d1f99c6b0c3cd0b11a95167540bb8edefd82b','dependency':'transformers','source':'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3'},
}


def words(text):
    return re.findall(r'[\w]+', text.casefold().replace('ё','е'))


def errors(expected, actual):
    a,b=words(expected),words(actual)
    row=list(range(len(b)+1))
    for i,left in enumerate(a,1):
        nxt=[i]
        for j,right in enumerate(b,1):
            nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(left!=right)))
        row=nxt
    return row[-1],len(a)


def load_manifest(path):
    path=Path(path).resolve()
    rows=json.loads(path.read_text(encoding='utf-8'))
    for row in rows:
        if row.get('source') not in {'synthetic','test_corpus','consented_recording'}:
            raise ValueError('Every recording requires explicit corpus provenance')
        if row.get('group') not in {'ru','mixed','en','silence','music','system_audio','self_tts'}:
            raise ValueError('Missing language/negative group')
        audio=(path.parent/row['audio']).resolve()
        if not audio.is_file(): raise FileNotFoundError(audio)
        row['audio']=str(audio)
    return rows


def build_provider(name, root, device='cuda', onnx_dir=None):
    candidate=CANDIDATES[name]
    if name=='whisper':
        from .stt_faster_whisper import FasterWhisperSTT
        engine=FasterWhisperSTT('large-v3-turbo',device,'int8_float16' if device=='cuda' else 'int8','auto',download_root=Path(root)/'data/models/faster-whisper')
        engine.hotwords=['Оникс','Onyx','Firefox','Telegram','YouTube','VS Code','PowerShell']
        return lambda path: engine.transcribe_final(Path(path))
    if name.startswith('gigaam'):
        if onnx_dir:
            import onnxruntime as ort
            from gigaam.onnx_utils import load_onnx,infer_onnx
            if device=='cuda' and 'CUDAExecutionProvider' not in ort.get_available_providers():
                raise RuntimeError('CUDA ONNX provider unavailable; CPU fallback is disabled')
            sessions,cfg=load_onnx(str(onnx_dir),candidate['model'],provider='CUDAExecutionProvider' if device=='cuda' else 'CPUExecutionProvider')
            if device=='cuda' and any('CUDAExecutionProvider' not in s.get_providers() for s in sessions):
                raise RuntimeError('ONNX silently fell back to CPU')
            return lambda path: infer_onnx([path],cfg,sessions,batch_size=1,progress=False)[0]
        import torch
        if device=='cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA Torch unavailable; export/load GigaAM ONNX in isolated environment')
        import gigaam
        checkpoint=Path(root)/'data/models/gigaam'/f"{candidate['model']}.ckpt"
        expected=getattr(gigaam,'_MODEL_HASHES',{}).get(candidate['model'])
        if checkpoint.exists() and expected:
            import hashlib
            with checkpoint.open('rb') as stream: actual=hashlib.file_digest(stream,'md5').hexdigest()
            if actual!=expected:
                raise RuntimeError('Incomplete GigaAM checkpoint: checksum mismatch; download must be repaired before inference')
        engine=gigaam.load_model(candidate['model'],device=device,download_root=str(Path(root)/'data/models/gigaam'))
        return engine.transcribe
    import torch
    if device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA Torch unavailable; use isolated Parakeet environment')
    from transformers import pipeline
    local=Path(root)/'data/models/parakeet'
    model=str(local) if (local/'model.safetensors').is_file() else candidate['model']
    engine=pipeline('automatic-speech-recognition',model=model,device=0 if device=='cuda' else -1,
                    **({'revision':candidate['revision']} if model==candidate['model'] else {}))
    return lambda path: engine(path)['text']


def benchmark(provider, rows, repeats=2):
    import soundfile as sf
    results=[]
    for row in rows:
        durations=[]; hypotheses=[]; accepted=[]
        for _ in range(repeats):
            start=perf_counter(); result=provider(row['audio']); durations.append(perf_counter()-start)
            hypotheses.append(result.text if hasattr(result,'text') else str(result))
            accepted.append(getattr(result,'accepted',None))
        error,count=errors(row['expected'],hypotheses[-1])
        info=sf.info(row['audio'])
        results.append(dict(id=row['id'],group=row['group'],source=row['source'],expected=row['expected'],hypotheses=hypotheses,
            accepted=accepted,errors=error,words=count,wer=error/count if count else None,
            latency_seconds=durations,warm_seconds=durations[-1],rtf=durations[-1]/info.duration if info.duration else None,
            false_transcript=not row['expected'] and bool(hypotheses[-1]),
            command_gate='not evaluated; no tools are available to this benchmark'))
    summary={}
    for group in sorted({r['group'] for r in results}):
        subset=[r for r in results if r['group']==group]; count=sum(r['words'] for r in subset)
        summary[group]=dict(samples=len(subset),wer=sum(r['errors'] for r in subset)/count if count else None,
            median_warm_seconds=statistics.median(r['warm_seconds'] for r in subset),
            false_transcripts=sum(r['false_transcript'] for r in subset))
    return dict(results=results,summary=summary,selection='No automatic provider promotion; require representative RU, mixed, EN and negative audio')
