"""Isolated optional search/extraction dependencies. JSON stdin/stdout only."""
import json
import sys

request=json.load(sys.stdin)
if request['operation']=='search':
    from ddgs import DDGS
    # Fixed search engine; no distributed/DHT or automatic engine fallback.
    rows=list(DDGS(timeout=7).text(request['query'],max_results=5,backend='duckduckgo'))
    result=[{k:str(row.get(k,''))[:2000] for k in ('title','href','body')} for row in rows[:5]]
elif request['operation']=='extract':
    import trafilatura
    text=trafilatura.extract(request['html'],include_comments=False,include_tables=False,
        favor_precision=True) or ''
    # Avoid inferring a publication date from copyright or historical body text.
    from html.parser import HTMLParser
    from datetime import datetime
    class Publication(HTMLParser):
        date=None
        def handle_starttag(self,tag,attrs):
            values=dict(attrs)
            if tag=='meta' and (values.get('property')=='article:published_time' or values.get('itemprop')=='datePublished'):
                try:
                    value=values.get('content','')
                    datetime.fromisoformat(value.replace('Z','+00:00'))
                    self.date=value
                except ValueError:pass
    publication=Publication();publication.feed(request['html'])
    result={'text':text[:14000], 'published_at':publication.date}
else:raise ValueError('Unknown operation')
json.dump(result,sys.stdout,ensure_ascii=False)
