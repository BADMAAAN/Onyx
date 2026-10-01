"""Bounded GGUF metadata inspection; tensor data is never read or executed."""
from pathlib import Path
import struct


def inspect_gguf(path):
    source=Path(path).resolve(strict=True)
    if not source.is_file() or source.suffix.lower()!='.gguf': raise ValueError('Choose a single GGUF file')
    size=source.stat().st_size
    limit=min(size,64*1024**2)
    with source.open('rb') as f:
        def read(n):
            if n<0 or f.tell()+n>limit: raise ValueError('Invalid or excessive GGUF metadata')
            value=f.read(n)
            if len(value)!=n: raise ValueError('Truncated GGUF metadata')
            return value
        def number(fmt): return struct.unpack('<'+fmt,read(struct.calcsize('<'+fmt)))[0]
        def string(keep=True):
            length=number('Q')
            if length>limit-f.tell(): raise ValueError('Invalid GGUF string')
            if keep:
                if length>1024*1024: raise ValueError('Excessive GGUF string')
                return read(length).decode('utf-8',errors='strict')
            f.seek(length,1)
        formats={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
        def value(kind,keep=False,depth=0):
            if kind in formats: return number(formats[kind])
            if kind==8:return string(keep)
            if kind==9 and depth<2:
                subtype,count=number('I'),number('Q')
                if count>4_000_000: raise ValueError('Excessive GGUF array')
                if subtype in formats:
                    n=count*struct.calcsize('<'+formats[subtype])
                    if n>limit-f.tell():raise ValueError('Invalid GGUF array')
                    f.seek(n,1)
                else:
                    for _ in range(count):value(subtype,False,depth+1)
                return None
            raise ValueError('Unsupported GGUF metadata type')
        if read(4)!=b'GGUF':raise ValueError('Invalid GGUF header')
        version=number('I')
        if version not in (2,3):raise ValueError('Only GGUF v2/v3 is supported')
        tensors,entries=number('Q'),number('Q')
        if not tensors or entries>100_000:raise ValueError('Invalid GGUF tensor/metadata count')
        wanted={'general.architecture','general.name','general.type','general.file_type','split.count'}
        metadata={}
        for _ in range(entries):
            key=string();kind=number('I');result=value(kind,key in wanted)
            if key in wanted:metadata[key]=result
        architecture=metadata.get('general.architecture')
        if not isinstance(architecture,str) or not architecture:raise ValueError('GGUF architecture is missing')
        if metadata.get('general.type','model')!='model' or architecture=='clip':raise ValueError('Adapters/projectors need a verified base model and are not supported by this single-file importer')
        if metadata.get('split.count',1)!=1:raise ValueError('Split GGUF needs all shards; single-file import only')
    return dict(path=str(source),bytes=size,version=version,architecture=architecture,
                name=metadata.get('general.name',''),file_type=metadata.get('general.file_type'),
                compatibility='Final architecture compatibility is checked by the installed runtime before completion')
