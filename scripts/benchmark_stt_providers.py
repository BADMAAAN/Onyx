"""One provider per process: optional environments cannot disturb the voice runtime."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import socket
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kk.voice.provider_benchmark import CANDIDATES,load_manifest,build_provider,benchmark


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--provider',choices=CANDIDATES,default='whisper')
    parser.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    parser.add_argument('--onnx-dir')
    parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    socket.setdefaulttimeout(45)  # model downloads must fail instead of hanging forever
    report=dict(provider=args.provider,device=args.device,candidate=CANDIDATES[args.provider])
    report['environment']={'python':sys.version.split()[0]}
    for package in ('torch','torchaudio','gigaam','transformers','faster-whisper','onnxruntime'):
        try:report['environment'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:pass
    try:
        rows=load_manifest(args.manifest)
        report['corpus']={'count':len(rows),'sources':sorted({r['source'] for r in rows})}
        started=perf_counter()
        engine=build_provider(args.provider,ROOT,args.device,args.onnx_dir)
        report['initialization_seconds']=perf_counter()-started
        report.update(benchmark(engine,rows,args.repeats)); report['status']='measured'
    except Exception as exc:
        report.update(status='blocked',error=f'{type(exc).__name__}: {exc}')
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    return 0 if report['status']=='measured' else 2

if __name__=='__main__': raise SystemExit(main())
