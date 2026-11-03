"""Explicit verified TLS context and transport probes; never auto retry."""
from contextvars import ContextVar
import os,ssl,sys,urllib.request,json,errno
from pathlib import Path
from urllib.parse import urlsplit
from .ai_protocol import NoRedirect
from .ai_diagnostics import exception_diagnostic

stage_callback=ContextVar('AI_transport_stage',default=None)
def stage(name,**details):
    cb=stage_callback.get()
    if cb:cb(name,details)

def security_asset_status():
    constants=getattr(getattr(ssl.SSLContext.__new__,'__code__',None),'co_consts',())
    required='codex-rejected-cas.pem' in constants
    path=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    return required,path

def tls_context():
    stage('tls.runtime_asset_check')
    required,path=security_asset_status()
    if required and not path.is_file():raise FileNotFoundError(errno.ENOENT,'TLS runtime security asset missing',str(path))
    stage('tls.context_create')
    # Same client verification configuration as CPython3.12 create_default_context,
    # deliberately excluding its SSLKEYLOGFILE side effect. No global env mutation.
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode=ssl.CERT_REQUIRED;context.check_hostname=True
    stage('tls.load_default_certs')
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    context.set_alpn_protocols(['http/1.1'])
    return context

def opener(context=None,*,local_probe=False):
    context=context or tls_context()
    stage('https.proxy_prepare')
    proxy=urllib.request.ProxyHandler({}) if local_probe else urllib.request.ProxyHandler()
    stage('https.opener_create')
    return urllib.request.build_opener(proxy,NoRedirect(),urllib.request.HTTPSHandler(context=context,debuglevel=0))

def runtime_info():
    paths=ssl.get_default_verify_paths()
    return dict(frozen=bool(getattr(sys,'frozen',False)),python=sys.version.split()[0],openssl=ssl.OPENSSL_VERSION,
                ssl_extension_present=True,python_ignore_environment=bool(sys.flags.ignore_environment),
                runtime_security_asset_required=security_asset_status()[0],runtime_security_asset_present=security_asset_status()[1].is_file(),certificate_verification='CERT_REQUIRED + hostname',TLS_key_logging=False,
                ssl_env_present={k:bool(os.environ.get(k)) for k in ('SSL_CERT_FILE','SSL_CERT_DIR','SSLKEYLOGFILE')},
                default_CA_file_exists=bool(paths.cafile),default_CA_directory_exists=bool(paths.capath))

def probe(*,legacy=False,url=None,cafile=None):
    current=['probe.start'];token=stage_callback.set(lambda value,details:current.__setitem__(0,value))
    try:
        if legacy and url:raise ValueError('Legacy probe is initialization only')
        if legacy:
            stage('legacy.default_opener_create');client=urllib.request.build_opener(NoRedirect())
        else:
            context=tls_context()
            if cafile:
                stage('tls.load_probe_CA');context.load_verify_locations(cafile=cafile)
            client=opener(context,local_probe=bool(url))
        result=dict(status='PASS',runtime=runtime_info(),network_used=False)
        if legacy:result['runtime']['TLS_key_logging']='legacy environment behavior; initialization only'
        if url:
            parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password:raise ValueError('Probe endpoint must be local HTTPS')
            stage('probe.https_open')
            req=urllib.request.Request(url,b'{"probe":true}',{'Content-Type':'application/json'},method='POST')
            with client.open(req,timeout=5) as response:
                stage('probe.response_read');raw=response.read(4097)
                if len(raw)>4096:raise ValueError('Probe response too large')
            result.update(network_used=True,response=json.loads(raw))
        return result
    except Exception as exc:
        return dict(status='FAIL',diagnostic=exception_diagnostic(exc,current[0]),runtime=runtime_info())
    finally:stage_callback.reset(token)

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='TLS init by default, optional localhost-only POST; no API key')
    p.add_argument('--legacy',action='store_true');p.add_argument('--url');p.add_argument('--cafile');p.add_argument('--output')
    a=p.parse_args(argv);result=probe(legacy=a.legacy,url=a.url,cafile=a.cafile)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output:
        from pathlib import Path
        Path(a.output).write_text(text+'\n',encoding='utf-8')
    print(text);return 0 if result['status']=='PASS' else 1
