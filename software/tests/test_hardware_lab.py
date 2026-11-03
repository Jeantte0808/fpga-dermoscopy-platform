"""Offline and loopback regression; these tests are not hardware performance."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from dermademo.mask_statistics import measure,compare
from dermademo.roi_expansion import expand
from dermademo.hardware_lab import numbers,evidence,compare_evidence,reopen
from dermademo.formal_results import replay_pair


@pytest.mark.parametrize('position,flags,bbox',[(0,9,[0,0,1,1]),(255,3,[255,0,256,1]),
    (65280,12,[0,255,1,256]),(65535,6,[255,255,256,256]),(25700,0,[100,100,101,101])])
def test_mask_single_pixel(position,flags,bbox):
    raw=bytearray(65536);raw[position]=255;s=measure(bytes(raw))
    assert s['foreground_count']==1 and s['bbox']==bbox and s['edge_flags']==flags
    assert s['hardware_status']=='unavailable_protocol_not_frozen'


def test_empty_full_and_mismatching_statistics():
    assert measure(bytes(65536))['bbox'] is None
    raw=bytes([255])*65536;s=measure(raw)
    assert s['foreground_count']==65536 and s['bbox']==[0,0,256,256] and s['edge_flags']==15
    assert compare(raw,None,frame_id=1,generation=2)['status']=='unavailable'
    hw=dict(s,valid=True,frame_id=1,generation=2)
    assert compare(raw,hw,frame_id=1,generation=2)['status']=='exact'
    hw['foreground_count']=65535
    assert compare(raw,hw,frame_id=1,generation=2)['differences']==['foreground_count']
    with pytest.raises(ValueError):compare(raw,hw,frame_id=3,generation=2)
    with pytest.raises(ValueError):measure(bytes([128])*65536)


@pytest.mark.parametrize('rect,percent,expected',[
    ((100,80,200,180),25,(75,55,225,205)),((0,0,17,17),10,(0,0,19,19)),
    ((0,0,400,300),50,(0,0,400,300)),((390,280,400,300),25,(387,275,400,300)),
    ((100,100,117,119),25,(95,95,122,124))])
def test_expansion_integer_half_open(rect,percent,expected):
    assert expand(rect,400,300,percent)==expected
    with pytest.raises(ValueError):expand(rect,400,300,0)


def test_quantiles_ignore_missing_without_inventing_zeros():
    result=numbers([None,True,-1,float('nan'),float('inf'),*range(1,21)])
    assert result['n']==20 and result['p95_ms']==19 and result['median_ms']==10.5
    assert numbers([])['mean_ms'] is None


def test_evidence_summary_missing_conditions_never_becomes_speedup():
    snap=evidence(replay_pair())
    before=deepcopy(snap);after=deepcopy(snap)
    assert not compare_evidence(before,after)['eligible']
    for obj in (before,after):
        obj.update(client_version='test-client',telemetry_mode='full',fixed_threshold={'q':1},hardware_declaration={'declared':'test'})
        obj['identity']['source_kind']='saved_FPGA_capture'
        obj['identity']['source_sha256']='test_source_identity'
    before['values']['graph']=100;after['values']['graph']=80
    assert compare_evidence(before,after)['metrics']['graph']['reduction_percent']==20
    after['identity']['roi']=[0,0,256,256]
    assert compare_evidence(before,after)['metrics']['graph']['reduction_percent'] is None
    after=deepcopy(before);after['client_version']=None
    assert 'client_version' in compare_evidence(before,after)['reasons']


def test_experiment_persists_all_trials_and_reuses_model(tmp_path,monkeypatch):
    import dermademo.hardware_lab as lab
    from formal_mask_peer import FormalPeer,local_config
    from teaching_fixtures import prepared_case
    prepared=prepared_case()
    class CPU:
        loads=0
        def __init__(self,_):self.closed=False;CPU.loads+=1
        def run(self,rgb,**_):
            return bytes(65536),dict(host_request_ms=5.,report={'rgb_to_mask_ms':3.},runtime={'load_ms':7.,'environment':{'device':'cpu'}})
        def close(self):self.closed=True
    monkeypatch.setattr(lab,'CPUClient',CPU)
    with FormalPeer() as peer:
        folder,doc=lab.run([prepared],replace(local_config(peer),telemetry_mode='summary',reuse_model=False),tmp_path,
            python='fixture',warmup=1,repeats=2,state_dir=tmp_path/'state')
    assert doc['status']=='completed',doc.get('error',doc)
    assert len(doc['trials'])==6 and sum(r['warmup'] for r in doc['trials'])==2
    assert CPU.loads==1
    assert doc['summary']['fpga_int8']['success']==2
    assert doc['summary']['cpu_fp32']['common_rgb_to_mask']['mean_ms']==5
    assert doc['summary']['fpga_pipeline_frames_per_s'] is None
    fpga=[r for r in doc['trials'] if r['backend']=='fpga_int8']
    assert len({r['frame_id'] for r in fpga})==3
    assert all(r['common_rgb_to_mask_ms']==r['e2e_ms'] for r in fpga)
    assert reopen(folder)['status']=='completed'
    for row in fpga:
        report=json.loads((folder/row['capture']/'report.json').read_text('utf-8'))
        assert ('model_upload_ms' in report)==(row==fpga[0])
    (folder/'trials.csv').write_text('corrupt','utf-8')
    with pytest.raises(ValueError):reopen(folder)


def test_cancel_keeps_unexecuted_rows_and_no_board_claim(tmp_path):
    from dermademo.hardware_lab import run
    from dermademo.device.formal_session import FormalConfig
    from teaching_fixtures import prepared_case
    cancel=Event();cancel.set()
    folder,doc=run([prepared_case()],FormalConfig(),tmp_path,warmup=0,repeats=2,
                   mode='fpga_sequence',cancel=cancel,state_dir=tmp_path/'state')
    assert doc['status']=='cancelled' and all(r['status']=='not_run' for r in doc['trials'])
    assert doc['summary']['fpga_pipeline_frames_per_s'] is None
    assert reopen(folder)['status']=='cancelled'


def test_device_lock_blocks_another_formal_client_between_frames(tmp_path,monkeypatch):
    import dermademo.hardware_lab as lab
    from dermademo.device.formal_session import transaction_state,FormalConfig
    from teaching_fixtures import prepared_case
    state=tmp_path/'state'
    class CPU:
        def __init__(self,_):pass
        def run(self,*_,**__):
            with pytest.raises(Exception,match='占用'):
                with transaction_state(state):pass
            raise RuntimeError('stop after lock assertion')
        def close(self):pass
    monkeypatch.setattr(lab,'CPUClient',CPU)
    folder,doc=lab.run([prepared_case()],FormalConfig(),tmp_path,python='fixture',warmup=0,repeats=1,state_dir=state)
    assert doc['status']=='failed' and doc['trials'][0]['status']=='failed'
    assert doc['trials'][1]['status']=='not_run'
    assert not list(folder.rglob('mask.raw'))


def test_roi_chain_roundtrip_and_missing_ancestor(tmp_path):
    from dermademo.roi_chain import save,load
    from dermademo.analysis_identity import result_identity
    from dermademo.records import load_record
    from dermademo.analysis_identity import frozen_pair
    pair=replay_pair();parent=frozen_pair(pair)
    folder=save(tmp_path,pair,[parent]);rec=load_record(folder)
    restored=load(folder,(rec.request,rec.result))
    assert len(restored)==1 and result_identity(restored[0])==result_identity(parent)
    parent_path=next((folder/'roi_history').glob('*/record.json'));parent_path.unlink()
    with pytest.raises(ValueError):load(folder,(rec.request,rec.result))


def test_mask_statistics_agree_with_member2_all_border_samples():
    from dermademo.resources import resource_path
    from dermademo.roi_edge_contact import check_edge_contact
    root=resource_path('edge_root')
    for row in json.loads((root/'samples.json').read_text('utf-8')):
        raw=(root/row['raw_mask_path']).read_bytes();s=measure(raw)
        edge=check_edge_contact(np.frombuffer(raw,np.uint8).reshape(256,256))
        expected=sum(1<<i for i,name in enumerate(('top','right','bottom','left')) if name in edge['touched_sides'])
        assert s['edge_flags']==expected


@pytest.mark.skipif(not Path('.cache/cpu-venv/Scripts/python.exe').is_file(),reason='Explicit CPU runtime unavailable')
def test_real_cpu_worker_with_loopback_experiment(tmp_path):
    from dermademo.hardware_lab import run
    from formal_mask_peer import FormalPeer,local_config
    from teaching_fixtures import prepared_case
    with FormalPeer() as peer:
        folder,doc=run([prepared_case()],replace(local_config(peer),telemetry_mode='summary'),tmp_path,
            python=str(Path('.cache/cpu-venv/Scripts/python.exe').resolve()),warmup=0,repeats=1,state_dir=tmp_path/'state')
    assert doc['status']=='completed',doc.get('error')
    cpu=doc['trials'][0]
    assert cpu['cpu_rgb_to_mask_ms']>0 and cpu['model_load_ms']>0 and 'fpga_agreement' in cpu
    assert doc['cpu_environment']['environment']['device']=='cpu'
    assert reopen(folder)['summary']['cpu_fp32']['success']==1


def test_reopen_refuses_trial_path_escape_and_input_identity_change(tmp_path):
    from dermademo.hardware_lab import run
    from dermademo.device.formal_session import FormalConfig
    from teaching_fixtures import prepared_case
    cancel=Event();cancel.set()
    folder,doc=run([prepared_case()],FormalConfig(),tmp_path,warmup=0,repeats=1,
        mode='fpga_sequence',cancel=cancel,state_dir=tmp_path/'state')
    original=deepcopy(doc)
    doc['trials'][0]['mask_path']='../outside.raw'
    (folder/'experiment.json').write_text(json.dumps(doc),'utf-8')
    with pytest.raises(ValueError,match='越界'):reopen(folder)
    doc=original;doc['trials'][0]['input_rgb_sha256']='bad'
    (folder/'experiment.json').write_text(json.dumps(doc),'utf-8')
    with pytest.raises(ValueError,match='输入身份'):reopen(folder)


def test_cli_real_run_requires_explicit_confirmation(tmp_path,capsys):
    from dermademo.hardware_lab_cli import main
    assert main(['run','--image',str(tmp_path/'not-opened.png')])==1
    assert '--confirm-live' in capsys.readouterr().out


def test_exported_team_fixtures_have_exact_binary_and_expected_fields(tmp_path):
    from scripts.export_mask_statistics_reference import export
    folder=tmp_path/'samples';doc=export(folder)
    assert len(doc['cases'])==10
    for row in doc['cases']:
        raw=(folder/row['file']).read_bytes()
        assert sha256(raw).hexdigest()==row['sha256'] and measure(raw)==row['expected']
    assert next(r for r in doc['cases'] if r['name']=='full')['expected']['foreground_count']==65536
