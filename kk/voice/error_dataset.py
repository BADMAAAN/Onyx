"""Explicitly saved user errors only; no automatic recording or weight training."""
from pathlib import Path
import json
import re


class VoiceErrorDataset:
    def __init__(self, folder): self.folder=Path(folder)

    def rows(self):
        if not self.folder.exists(): return []
        result=[]
        for path in sorted(self.folder.glob('*.json')):
            try: row=json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError): continue
            if not path.with_suffix('.wav').is_file(): continue
            result.append(dict(sample_id=path.stem,audio=path.with_suffix('.wav').name,
                heard=row.get('raw_transcript',''), final=row.get('final_transcript',''),
                corrected=row.get('user_corrected_text'),category=row.get('category',row.get('intent','unknown')),
                language=row.get('language','unknown'),noise=row.get('noise_conditions','unknown')))
        return result

    def correct(self, sample_id, text, category='user_correction', language='ru', noise='unknown'):
        if Path(sample_id).name != sample_id or not re.fullmatch(r'[\w-]+',sample_id):
            raise ValueError('Invalid sample id')
        path=self.folder/(sample_id+'.json')
        if not path.is_file() or not path.with_suffix('.wav').is_file(): raise FileNotFoundError(sample_id)
        if not text.strip(): raise ValueError('A user correction must be nonempty')
        row=json.loads(path.read_text(encoding='utf-8'))
        row.update(user_corrected_text=text,category=category,language=language,noise_conditions=noise)
        path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        return dict(verified=True,sample_id=sample_id)

    def hotwords(self):
        words=[]
        for row in self.rows():
            if row['corrected']:
                words.extend(re.findall(r'\b[A-Za-z][\w.+-]{1,35}\b',row['corrected']))
        return list(dict.fromkeys(words))[-100:]

    def export_manifest(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        path=self.folder/'dataset.jsonl'
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in self.rows()),encoding='utf-8')
        return path
