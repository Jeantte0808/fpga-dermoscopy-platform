"""Bounded, credential-redacted exception diagnostics, never request/response bodies."""
import base64,json,os,re,traceback
from pathlib import Path
from urllib.parse import quote,quote_plus


def redact(value,key):
    text=str(value)
    if key:
        variants={key,quote(key,safe=''),quote_plus(key),json.dumps(key)[1:-1],base64.b64encode(key.encode()).decode()}
        for token in sorted(variants,key=len,reverse=True):text=text.replace(token,'[REDACTED]')
    text=re.sub(r'(?im)(authorization|proxy-authorization|api[-_ ]?key)\s*[:=][^\r\n]*',r'\1: [REDACTED]',text)
    text=re.sub(r'(?i)bearer\s+[^\s,;\"\']+','Bearer [REDACTED]',text)
    text=re.sub(r'https?://[^\s\]\)\"\']+','[URL REDACTED]',text)
    return text[:1200]


def exception_diagnostic(exc,stage,key='',*,include_message=True):
    chain=[];seen=set();current=exc
    while isinstance(current,BaseException) and id(current) not in seen and len(chain)<5:
        seen.add(id(current));filename=getattr(current,'filename',None)
        nested=getattr(current,'reason',None)
        summary=(type(nested).__name__ if isinstance(nested,BaseException) else os.strerror(current.errno) if isinstance(current,OSError) and current.errno else str(current) if include_message else 'exception message omitted')
        chain.append(dict(type=type(current).__name__,summary=redact(summary,key),
                          errno=getattr(current,'errno',None),winerror=getattr(current,'winerror',None),http_status=getattr(current,'code',None),
                          missing_file=redact(Path(filename).name,key) if filename else None,
                          stack=[dict(file=Path(f.filename).name,function=f.name,line=f.lineno) for f in traceback.extract_tb(current.__traceback__)[-10:]]))
        reason=getattr(current,'reason',None)
        current=current.__cause__ or (reason if isinstance(reason,BaseException) else None) or current.__context__
    return dict(failure_stage=stage,exception_chain=chain,automatic_retry=False,
                missing_files=[c['missing_file'] for c in chain if c['missing_file']],
                note='No headers, payload, response body, locals, or full filesystem paths collected')
