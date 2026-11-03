from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

import pytest

ROOT=Path(__file__).resolve().parents[1]
PS=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'


def spec(data):return {'bytes':len(data),'sha256':sha256(data).hexdigest()}


def fixture(tmp_path):
    base=tmp_path/'accepted';package=tmp_path/'update';base.mkdir();package.mkdir()
    files={'a.exe':b'old','internal/keep.dll':b'keep'}
    after={**files,'a.exe':b'new','internal/new.txt':b'added'}
    for name,data in files.items():
        path=base/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(data)
    for name in ('a.exe','internal/new.txt'):
        path=package/'payload'/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(after[name])
    manifest={'before':{k:spec(v) for k,v in files.items()},'after':{k:spec(v) for k,v in after.items()},
              'changed':['a.exe','internal/new.txt'],'deleted':[]}
    (package/'UPDATE_MANIFEST.json').write_text(json.dumps(manifest),'utf-8')
    for name in ('Verify_Update.ps1','Apply_Update.ps1'):shutil.copyfile(ROOT/'packaging'/name,package/name)
    return base,package,after


def run(package,base,target):
    return subprocess.run([str(PS),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'Apply_Update.ps1'),
                           '-BaseDirectory',str(base),'-NewDirectory',str(target)],capture_output=True)


@pytest.mark.skipif(not PS.is_file(),reason='Windows PowerShell only')
def test_incremental_clones_baseline_and_all_target_bytes(tmp_path):
    base,package,after=fixture(tmp_path);target=tmp_path/'new app'
    result=run(package,base,target);assert result.returncode==0,(result.stdout,result.stderr)
    assert {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}==after
    assert (base/'a.exe').read_bytes()==b'old' and not (base/'internal/new.txt').exists()
    assert run(package,base,target).returncode!=0


@pytest.mark.parametrize('fault',['wrong_base','tampered_payload','nested_destination'])
def test_incremental_rejects_before_output_and_preserves_baseline(tmp_path,fault):
    base,package,after=fixture(tmp_path);target=tmp_path/'new'
    if fault=='wrong_base':(base/'a.exe').write_bytes(b'bad')
    elif fault=='tampered_payload':(package/'payload/a.exe').write_bytes(b'bad')
    else:target=base/'nested'
    before={p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*') if p.is_file()}
    result=run(package,base,target);assert result.returncode!=0
    assert not target.exists()
    assert {p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('mode',['true','false'])
def test_source_patch_updates_attributes_before_hash_protected_files(tmp_path,mode):
    from scripts.verify_update import apply_source_patch
    repo=tmp_path/'repo';repo.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
    git('init','--quiet');git('config','core.autocrlf','false')
    git('config','user.name','Local update regression');git('config','user.email','update-test@example.invalid')
    (repo/'.gitattributes').write_bytes(b'legacy/** -text -diff\n')
    (repo/'app.py').write_bytes(b'old\n')
    git('add','.');git('commit','--quiet','-m','base');before=git('rev-parse','HEAD').decode().strip()
    (repo/'.gitattributes').write_bytes(b'legacy/** -text -diff\nincoming/** -text -diff\n')
    (repo/'app.py').write_bytes(b'new\n');(repo/'incoming').mkdir()
    (repo/'incoming/model.py').write_bytes(b'identity = 1\n')
    git('add','.');git('commit','--quiet','-m','target');after=git('rev-parse','HEAD').decode().strip()
    patch=tmp_path/'source.patch';patch.write_bytes(git('diff','--binary',before,after))
    folders=[]
    for name,ref in [('base',before),('expected',after)]:
        folder=tmp_path/name;folder.mkdir();archive=tmp_path/(name+'.zip')
        git('-c','core.autocrlf='+mode,'archive','--format=zip','--output',str(archive),ref)
        with ZipFile(archive) as z:z.extractall(folder)
        folders.append(folder)
    apply_source_patch(folders[0],patch,autocrlf=mode)
    actual={p.relative_to(folders[0]).as_posix():p.read_bytes() for p in folders[0].rglob('*')
            if p.is_file() and '.git' not in p.relative_to(folders[0]).parts}
    expected={p.relative_to(folders[1]).as_posix():p.read_bytes() for p in folders[1].rglob('*') if p.is_file()}
    assert actual==expected
    assert (folders[0]/'incoming/model.py').read_bytes()==b'identity = 1\n'


@pytest.mark.parametrize('iteration',['performance','p7','teaching-queue','flash-pdf','ai','https-fix','algorithm-review','pdf-table'])
def test_performance_delta_contains_only_changed_runtime_and_correct_handoff(tmp_path,monkeypatch,iteration):
    from scripts import package_update as package
    base=tmp_path/'base';target=tmp_path/'target'
    for folder in (base,target):
        folder.mkdir();(folder/'same.dll').write_bytes(b'keep')
    (base/'app.exe').write_bytes(b'old');(target/'app.exe').write_bytes(b'new')
    (tmp_path/'dist').mkdir();(tmp_path/'docs').mkdir();(tmp_path/'packaging').mkdir()
    for name in ('CPU_ROI_QUICK_START.md','PC_PERFORMANCE_ROUND1.md','P7_CANDIDATE_20261029.md',
                 'R1_教学验证与正式队列_20261030.md','R1_队友同步准备与交接_20261030.md','REFERENCE_PACKAGE_V1.md','P7_FLASH_BATCH_PDF_20261030.md','AI_CREDENTIALS_20261030.md','AI_HTTPS_FIX_20261030.md','PC_ALGORITHM_MATERIALS_RECEIPT_20261030.md','AI_DRAFT_RECOVERY_R2_20261030.md','PDF_ILLUSTRATED_TABLE_REPORT_20261030.md','CLASSIFIER_V2_20261031.md','DOMESTIC_REPORT_STYLE_20261031.md','COMPACT_REPORT_20261031.md'):
        (tmp_path/'docs'/name).write_text('instructions','utf-8')
    for name in ('Apply_Update.ps1','Verify_Update.ps1'):
        shutil.copyfile(ROOT/'packaging'/name,tmp_path/'packaging'/name)
    (tmp_path/'requirements-cpu.lock.txt').write_text('locked','utf-8')
    if iteration=='ai':
        (tmp_path/'scripts').mkdir();(tmp_path/'scripts/prepare_reference_import.py').write_text('# fixture')
        (tmp_path/'integrations/queue_reference_r1').mkdir(parents=True)
        (tmp_path/'integrations/queue_reference_r1/REFERENCE_IMPORT.md').write_text('fixture')
    evidence=tmp_path/'REGRESSION.json';evidence.write_text('{"status":"PASS"}')
    monkeypatch.setattr(package,'ROOT',tmp_path)
    monkeypatch.setattr(package,'verify_inventory',lambda p:{'build':dict(dirty=False,app_version='0.9.2' if p==base else '0.9.3-rc.1',git_commit='old' if p==base else 'new')})
    def patch(command,**kwargs):
        assert '--no-textconv' in command and '--no-ext-diff' in command
        kwargs['stdout'].write(b'git binary patch fixture\n')
    monkeypatch.setattr(package.subprocess,'run',patch)
    package.main([str(base),str(target),'--evidence',str(evidence),'--iteration',iteration])
    delta=tmp_path/'dist/AlienHomeDerma-0.9.2-to-0.9.3-rc.1-update'
    spec=json.loads((delta/'UPDATE_MANIFEST.json').read_text('utf-8'))
    assert spec['changed']==['app.exe']
    assert not (delta/'payload/same.dll').exists()
    readme=(delta/'README_FIRST.md').read_text('utf-8')
    expected = {'performance': ('3页','249页','512项','相同RGB','SOURCE_CHANGES.patch','PC_PERFORMANCE_ROUND1.md'),
                'p7': ('非自动识别','未固化Flash','11帧','SOURCE_CHANGES.patch','P7_CANDIDATE_20261029.md'),
                'algorithm-review': ('source_kind/result_display_mode/analysis_context','数字误拦截R2','ai-recover-response','OpenCV版本差异','SOURCE_CHANGES.patch'),
                'pdf-table': ('黑白检查单','仅重新生成PDF','256模型网格','SOURCE_CHANGES.patch'),
                'https-fix': ('ai-transport-check','SSLKEYLOGFILE','不自动重试','SOURCE_CHANGES.patch'),
                'ai': ('Windows凭据管理器','计费确认','不会调用AI','SOURCE_CHANGES.patch'),
                'flash-pdf': ('P7 Flash部署配置','DDR权重','报告错误独立','未启用','SOURCE_CHANGES.patch'),
                'teaching-queue': ('正式多图FPGA队列','逐帧FINISH','整数逐字节一致性','尚需成员2','SOURCE_CHANGES.patch')}[iteration]
    for text in expected:
        assert text in readme
    with ZipFile(delta.with_name(delta.name+'.zip')) as archive:assert archive.testzip() is None
    if iteration == 'teaching-queue':
        assert (delta/'REFERENCE_PACKAGE_V1.md').is_file()
        assert delta.with_name(delta.name+'.zip.sha256').is_file()
