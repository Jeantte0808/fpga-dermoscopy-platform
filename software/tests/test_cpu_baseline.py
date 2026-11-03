from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import threading

import numpy as np
import pytest

from dermademo.cpu_baseline import CPUClient,agreement,save_comparison,load_comparison
from dermademo.resources import resource_path

CPU_PYTHON=Path(__file__).resolve().parents[1]/'.cache/cpu-venv/Scripts/python.exe'


def test_comparison_requires_same_bytes_and_detects_corruption(tmp_path):
    rgb=bytes(196608);mask=bytes(65536)
    report={'device':'cpu','dtype':'float32','threshold':'sigmoid(logits)>0.5','rgb_sha256':sha256(rgb).hexdigest(),
            'runtime':{'load_ms':1.,'environment':{'device':'cpu'}},'records':[]}
    meta={'input_sha256':sha256(rgb).hexdigest(),'source_kind':'protocol_simulator'}
    saved=save_comparison(tmp_path,rgb,mask,report,fpga_mask=mask,fpga_metadata=meta)
    assert load_comparison(saved)==(rgb,mask,report,mask,meta)
    assert agreement(mask,mask)['dice_agreement']==1
    assert agreement(mask,bytes([255])*65536)['dice_agreement']==0
    with pytest.raises(ValueError,match='同一份'):
        save_comparison(tmp_path,rgb,mask,report,fpga_mask=mask,fpga_metadata={'input_sha256':'0'*64})
    (saved/'cpu_mask.raw').write_bytes(bytes([255])*65536)
    with pytest.raises(ValueError,match='校验'):load_comparison(saved)


def test_local_comparison_save_failure_never_complete(tmp_path,monkeypatch):
    import dermademo.storage as storage
    rgb=bytes(196608);mask=bytes(65536)
    report={'device':'cpu','dtype':'float32','threshold':'sigmoid(logits)>0.5','rgb_sha256':sha256(rgb).hexdigest(),
            'runtime':{'load_ms':1.,'environment':{'device':'cpu'}}}
    def fail(*_a):raise OSError('disk full')
    monkeypatch.setattr(storage,'write_png',fail)
    with pytest.raises(OSError):save_comparison(tmp_path,rgb,mask,report)
    saved=next(tmp_path.glob('cpu_comparison_*'))
    assert (saved/'INCOMPLETE.txt').exists() and not (saved/'comparison.json').exists()
    with pytest.raises(ValueError,match='未完成'):load_comparison(saved)


@pytest.mark.skipif(not CPU_PYTHON.is_file(),reason='explicit separate CPU environment not installed')
def test_real_cpu_three_samples_resident_benchmark_and_reopen(tmp_path,monkeypatch):
    import dermademo.cpu_baseline as baseline
    root=tmp_path/'read-only-distribution'
    shutil.copytree(resource_path('cpu_root'),root,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    before={p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(baseline,'resource_path',lambda key:root if key=='cpu_root' else resource_path(key))
    client=CPUClient(CPU_PYTHON);pids=[];rows=[]
    try:
        for sample in sorted((root/'samples').iterdir()):
            rgb=(sample/'input.rgb888.bin').read_bytes();mask,report=client.run(rgb)
            pids.append(client.process.pid)
            # Same pinned CPU build on this PC qualified separately; no cross-platform zero-LSB promise.
            expected=(sample/'cpu_mask.bin').read_bytes()
            assert mask==expected
            assert report['runtime']['environment']['pytorch'].endswith('+cpu')
            assert report['runtime']['environment']['intra_op_threads']==4
            assert report['runtime']['environment']['inter_op_threads']==1
            fpga=(sample/'formal_int8_mask.bin').read_bytes()
            meta={'input_sha256':sha256(rgb).hexdigest(),'source_kind':'member2_integer_software_reference'}
            saved=save_comparison(tmp_path,rgb,mask,report,fpga_mask=fpga,fpga_metadata=meta)
            assert load_comparison(saved)[1]==mask
            rows.append({'sample':sample.name,'different_cpu_golden_pixels':sum(a!=b for a,b in zip(mask,expected)),
                         'FP32_vs_INT8':agreement(mask,fpga)})
        assert len(set(pids))==1
        mask,report=client.run(rgb,benchmark=True)
        bench=report['report'];assert bench['warmups_per_scope']==5
        assert bench['measured_repeats_per_scope']==20 and len(report['records'])==40
        assert bench['forward']['count']==bench['rgb_to_mask']['count']==20
        saved=save_comparison(tmp_path,rgb,mask,report)
        assert len((saved/'timings.csv').read_text('utf-8-sig').splitlines())==42
        assert load_comparison(saved)[2]==report
        evidence={'samples':rows,'resident_process_reused':True,'benchmark':report,
                  'scope':'local_CPU_only_not_FPGA_or_180_image_accuracy','physical_FPGA_tested':False}
        target=Path('artifacts/cpu-roi-baseline');target.mkdir(parents=True,exist_ok=True)
        (target/'CPU_VALIDATION.json').write_text(json.dumps(evidence,indent=2),'utf-8')
        cancel=threading.Event();cancel.set()
        with pytest.raises(InterruptedError):client.run(rgb,cancel=cancel)
        assert client.process is None
        assert {p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}==before
    finally:client.close()


def test_cpu_missing_interpreter_clear_error(tmp_path):
    with pytest.raises(ValueError,match='Python'):CPUClient(tmp_path/'missing.exe')
