"""Resume official GigaAM weights; verify checkpoints before GPU benchmarks."""
import argparse
import hashlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def fetch(url, path, checksum=None, algorithm='md5'):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    def valid():
        if not path.exists() or not checksum:return False
        with path.open('rb') as stream:return hashlib.file_digest(stream,algorithm).hexdigest()==checksum
    if valid():return
    last_error=None
    for attempt in range(3):
        offset=path.stat().st_size if path.exists() else 0
        try:
            request=Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
            with urlopen(request,timeout=45) as response:
                if offset and response.status!=206:
                    raise RuntimeError('Server ignored resume range; partial file preserved')
                if response.status==206 and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                    raise RuntimeError('Wrong download range')
                remaining=int(response.headers['Content-Length'])
                with path.open('ab' if offset else 'wb') as output:
                    # Stop at the declared length: a broken final connection
                    # must not make us wait for an extra EOF read indefinitely.
                    while remaining:
                        chunk=response.read(min(1024*1024,remaining))
                        if not chunk:raise IOError('Truncated model download')
                        output.write(chunk);remaining-=len(chunk)
            if checksum and not valid():raise RuntimeError('Checkpoint checksum mismatch')
            return
        except HTTPError as exc:
            if exc.code==416 and (valid() or (checksum is None and offset>0)):return
            last_error=exc
        except (OSError,TimeoutError) as exc:last_error=exc
        print(f'Retry {attempt+1}: {type(last_error).__name__}; partial file retained',flush=True)
    raise RuntimeError('Official weight download incomplete; rerun to resume') from last_error


def main():
    import gigaam
    parser=argparse.ArgumentParser()
    parser.add_argument('model',choices=['v3_e2e_rnnt','multilingual_ctc'])
    parser.add_argument('--directory',default='data/models/gigaam')
    args=parser.parse_args();root=Path(args.directory)
    fetch(f'{gigaam._URL_DIR}/{args.model}.ckpt',root/f'{args.model}.ckpt',gigaam._MODEL_HASHES[args.model])
    if 'e2e' in args.model:
        fetch(f'{gigaam._URL_DIR}/{args.model}_tokenizer.model',root/f'{args.model}_tokenizer.model')
    print('Official checkpoint checksum verified',flush=True)


if __name__=='__main__':main()
