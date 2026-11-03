from hashlib import sha256
import json
from pathlib import Path
import pytest
from scripts.package_submission import create_submission
from scripts.verify_release import verify_inventory


def fixture(tmp_path,*,branded=False):
    folder=tmp_path/'accepted';folder.mkdir()
    values={'AlienHomeDerma.exe':b'gui','AlienHomeTools.exe':b'tools','_internal/runtime.dll':b'unchanged',
            'docs/QUICK_START.md':b'Run AlienHomeDerma.exe','docs/older.md':b'old document'}
    if branded:values['docs/QUICK_START.md']=''.join(map(chr,[22806,26143,20154,20043,23478])).encode('utf-8')
    for name,raw in values.items():
        p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    specs={name:dict(sha256=sha256(raw).hexdigest(),bytes=len(raw)) for name,raw in values.items()}
    manifest=dict(build=dict(app_version='fixture'),files=specs)
    (folder/'release-manifest.json').write_text(json.dumps(manifest),'utf-8')
    (folder/'SHA256SUMS.txt').write_text(''.join(s['sha256']+'  '+n+'\n' for n,s in specs.items()),'utf-8')
    return folder


def test_neutral_submission_renames_only_public_entries_and_preserves_runtime(tmp_path):
    base=fixture(tmp_path);before={p:p.read_bytes() for p in base.rglob('*') if p.is_file()}
    output=create_submission(base,tmp_path/'submission');manifest=verify_inventory(output)
    assert manifest['entrypoints']==dict(gui='DermaWorkbench.exe',tools='DermaTools.exe')
    assert (output/'DermaWorkbench.exe').read_bytes()==b'gui'
    assert not (output/'AlienHomeDerma.exe').exists()
    assert (output/'_internal/runtime.dll').read_bytes()==b'unchanged'
    assert 'DermaWorkbench.exe' in (output/'docs/QUICK_START.md').read_text('utf-8')
    assert not (output/'docs/older.md').exists() and not (output/'SUBMISSION_INCOMPLETE.txt').exists()
    assert all(p.read_bytes()==raw for p,raw in before.items())
    with pytest.raises(FileExistsError):create_submission(base,output)


def test_submission_rejects_nested_output_and_remaining_team_name(tmp_path):
    base=fixture(tmp_path,branded=True)
    with pytest.raises(ValueError):create_submission(base,base/'nested')
    output=tmp_path/'submission'
    with pytest.raises(ValueError):create_submission(base,output)
    assert (output/'SUBMISSION_INCOMPLETE.txt').exists()
