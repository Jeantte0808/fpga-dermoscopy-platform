import errno,json,ssl,urllib.error
from pathlib import Path
import pytest
from dermademo.ai_transport import tls_context,probe
from dermademo.ai_diagnostics import exception_diagnostic
from dermademo.ai_service import run_analysis
from test_ai import app,record,KEY
from tls_peer import tls_peer,CERT


def test_verified_TLS_roundtrip_and_no_keylog(tmp_path,monkeypatch):
    path=tmp_path/'missing'/'tls-secrets.log';monkeypatch.setenv('SSLKEYLOGFILE',str(path))
    legacy=probe(legacy=True)
    assert legacy['status']=='FAIL' and legacy['diagnostic']['missing_files']==['tls-secrets.log']
    ctx=tls_context();assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED and ctx.keylog_filename is None
    monkeypatch.setenv('https_proxy','http://127.0.0.1:9')
    monkeypatch.setenv('no_proxy','')
    with tls_peer() as (url,requests):
        ok=probe(url=url,cafile=str(CERT));assert ok['status']=='PASS' and ok['response']=={'ok':True}
        assert requests==[dict(body={'probe':True},authorization_present=False)]
        failed=probe(url=url)
        assert failed['status']=='FAIL' and len(requests)==1  # untrusted cert rejected before HTTP
    assert not path.exists()


def test_diagnostic_redacts_secret_headers_URL_and_keeps_missing_basename():
    inner=FileNotFoundError(errno.ENOENT,'Authorization: Bearer '+KEY,'C:/private/'+KEY+'/missing-ca.pem')
    outer=urllib.error.URLError(inner)
    diag=exception_diagnostic(outer,'tls.load_default_certs',KEY)
    serialized=json.dumps(diag)
    assert KEY not in serialized and 'C:/private' not in serialized
    assert diag['missing_files']==['missing-ca.pem'] and len(diag['exception_chain'])==2
    assert diag['failure_stage']=='tls.load_default_certs'


def test_real_client_setup_failure_report_no_retry(app,tmp_path,monkeypatch):
    import dermademo.ai_transport as transport
    calls=[]
    def failed():calls.append(1);raise FileNotFoundError(errno.ENOENT,'missing',str(tmp_path/'ca-missing.pem'))
    monkeypatch.setattr(transport,'tls_context',failed)
    # ai_service.opener resolves its TLS helper at invocation, before any socket.
    result=run_analysis(record(tmp_path),'https://dashscope.aliyuncs.com',KEY)
    meta=result['meta'];assert calls==[1] and not meta['cloud_complete']
    assert meta['diagnostic']['missing_files']==['ca-missing.pem']
    assert meta['diagnostic']['failure_stage']=='https.request_build'
    assert not meta['request_started_local'] and not meta['http_response_received']
    assert KEY not in json.dumps(meta)


def test_missing_runtime_security_asset_reports_filename_before_context(monkeypatch,tmp_path):
    import dermademo.ai_transport as transport
    missing=tmp_path/'codex-rejected-cas.pem'
    monkeypatch.setattr(transport,'security_asset_status',lambda:(True,missing))
    result=transport.probe()
    assert result['status']=='FAIL' and result['diagnostic']['failure_stage']=='tls.runtime_asset_check'
    assert result['diagnostic']['missing_files']==['codex-rejected-cas.pem']


def test_build_collects_exact_required_runtime_security_asset():
    from scripts.runtime_tls_assets import inventory,runtime_tls_assets
    if runtime_tls_assets():
        assert 'codex-rejected-cas.pem' in inventory()
        assert inventory()['codex-rejected-cas.pem']['bytes']>100
