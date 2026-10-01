"""Bounded public HTTP reader: validate DNS once, connect to that exact address."""
from datetime import datetime, timezone
import http.client
import ipaddress
import socket
import ssl
from threading import Event, Thread
from time import monotonic
from urllib.parse import urlsplit, urljoin, quote

class WebError(RuntimeError): pass

def public_target(url,cancel=None,timeout=10):
    p=urlsplit(url)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:
        raise WebError('Only public HTTP(S) URLs without credentials are allowed')
    if p.port not in (None,80,443):raise WebError('Only standard web ports are allowed')
    host=p.hostname.encode('idna').decode('ascii')
    if host.casefold().rstrip('.')=='localhost' or host.endswith(('.local','.internal')):
        raise WebError('Local destinations are forbidden')
    port=p.port or (443 if p.scheme=='https' else 80)
    complete=Event();result=[]
    def resolve():
        try:result.append(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
        except OSError as exc:result.append(exc)
        finally:complete.set()
    Thread(target=resolve,daemon=True).start()
    deadline=monotonic()+timeout
    while not complete.wait(.05):
        if cancel and cancel.is_set():raise WebError('Cancelled during DNS')
        if monotonic()>deadline:raise WebError('DNS timeout')
    if isinstance(result[0],Exception):raise WebError('DNS unavailable')
    records=result[0]
    if not records:raise WebError('DNS returned no address')
    for record in records:
        address=ipaddress.ip_address(record[4][0])
        if not address.is_global or address.is_multicast or getattr(address,'ipv4_mapped',None):
            raise WebError('Private or special destination is forbidden')
    return p,host,port,records[0]

class PageReader:
    def __init__(self, enabled=lambda:True, max_bytes=1_000_000, timeout=10):
        self.enabled=enabled;self.max_bytes=max_bytes;self.timeout=timeout

    def fetch(self,url,cancel=None):
        cancel=cancel or Event()
        for redirect in range(4):
            if not self.enabled():raise WebError('Internet is off')
            if cancel.is_set():raise WebError('Cancelled')
            p,host,port,record=public_target(url,cancel,self.timeout)
            done=Event();sock=None;connection=None
            deadline=monotonic()+self.timeout
            try:
                sock=socket.socket(record[0],record[1],record[2]);sock.settimeout(self.timeout)
                def interrupt():
                    while not done.wait(.05):
                        if cancel.is_set() or not self.enabled() or monotonic()>deadline:
                            try:sock.shutdown(socket.SHUT_RDWR)
                            except OSError:pass
                            sock.close();return
                watcher=Thread(target=interrupt,daemon=True);watcher.start()
                sock.connect(record[4])  # No second DNS lookup; rebinding cannot change the peer.
                if p.scheme=='https':sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
                connection=http.client.HTTPConnection(host,port,timeout=self.timeout)
                connection.sock=sock
                path=quote(p.path or '/',safe="/%:@!$&'()*+,;=-._~")
                if p.query:path+='?'+quote(p.query,safe="%=&/?:@!$'()*+,;+-._~")
                connection.request('GET',path,headers={'Host':host,'User-Agent':'Onyx/0.3 public-page-reader',
                    'Accept':'text/html,text/plain,application/xhtml+xml','Accept-Encoding':'identity','Connection':'close'})
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location:raise WebError('Redirect without destination')
                    url=urljoin(url,location)
                    continue  # Every redirect must pass the same DNS and address gate.
                if response.status!=200:raise WebError('HTTP '+str(response.status))
                kind=response.getheader('Content-Type','').split(';')[0].strip().lower()
                if kind not in ('text/html','text/plain','application/xhtml+xml'):raise WebError('Unsupported content type')
                if response.getheader('Content-Encoding','identity')!='identity':raise WebError('Encoded response refused')
                length=response.getheader('Content-Length')
                if length and int(length)>self.max_bytes:raise WebError('Page exceeds byte limit')
                chunks=[];total=0
                while True:
                    if cancel.is_set() or not self.enabled():raise WebError('Cancelled or Internet off')
                    if monotonic()>deadline:raise WebError('Page timeout')
                    chunk=response.read(min(16384,self.max_bytes-total+1))
                    if not chunk:break
                    total+=len(chunk)
                    if total>self.max_bytes:raise WebError('Page exceeds byte limit')
                    chunks.append(chunk)
                return dict(url=url,html=b''.join(chunks).decode('utf-8',errors='replace'),
                    retrieved_at=datetime.now(timezone.utc).isoformat(),content_type=kind)
            except (OSError,http.client.HTTPException,ValueError) as exc:
                if isinstance(exc,WebError):raise
                raise WebError('Cancelled' if cancel.is_set() else 'Network failure: '+type(exc).__name__) from None
            finally:
                done.set()
                if connection:connection.close()
                elif sock:sock.close()
                if 'watcher' in locals():watcher.join(.2)
        raise WebError('Too many redirects')
