from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import pytest

from dermademo.device import formal_session as session
from dermademo.device.formal_protocol import assets, make_summary
from dermademo.device.formal_capture import load_capture, validate_capture
from dermademo.formal_results import live_pair
from dermademo.resources import resource_path
from dermademo.storage import save_result
from dermademo.records import load_record
from formal_mask_peer import FormalPeer, local_config


@pytest.mark.parametrize('mode,pages', [('summary',3),('full',249)])
def test_mode_wire_timing_no_packet_disk_write_and_roundtrip(tmp_path, monkeypatch, mode, pages):
    original = session.write_bytes
    with FormalPeer(drop_once=True) as peer:
        def write(path, value):
            # No partial/result writes inside the RGB/Mask request loop.
            if Path(path).name in ('partial_mask.raw','mask.raw','mask.png'):
                assert any(r[0] == 7 for r in peer.requests)
            return original(path,value)
        monkeypatch.setattr(session,'write_bytes',write)
        config=replace(local_config(peer),telemetry_mode=mode)
        outcome=session.run_formal(assets()[2],config,tmp_path,state_dir=tmp_path/'state')
        assert outcome.report['complete'], outcome.report
        queries=[r[3] for r in peer.requests if r[0]==8 and r[3]]
        assert len(queries)==pages
        assert queries[:3]==[0x50520000,0x50520001,0x50520002]
        assert bytes(peer.rgb)==outcome.rgb
    profile=outcome.profile
    assert profile['full_stages_available'] == (mode=='full')
    assert profile['stage_sum_verified'] == (mode=='full')
    assert len(profile['stages']) == (82 if mode=='full' else 0)
    report=outcome.report
    times=report['stage_timings_ms']
    assert all(v>=0 for k,v in times.items() if k!='display_prepare')
    assert times['display_prepare'] is None
    assert times['statistics']==report['telemetry_ms']
    assert report['image_E2E_ms']>=sum(times[k] for k in ('image_begin','rgb_upload','first_mask_wait','remaining_mask','finish'))
    calls=report['operation_trace']['calls']
    assert any(c['BUSY']==1 for c in calls)
    assert any(c['timeout_retries']==1 for c in calls)
    assert all(c['success'] and c['elapsed_ms']>=c['first_matching_reply_ms']>=0 for c in calls)
    assert all(c['tx_bytes']>=96*c['sends'] for c in calls)
    loaded=load_capture(outcome.directory,resource_path('formal_root')/'assets/manifest.json')
    assert loaded.info['full_stages_available']==(mode=='full')
    request,result=live_pair(outcome)
    assert result.metadata['stage_timings_ms']['display_prepare']>=0
    (tmp_path/'records').mkdir()
    record=load_record(save_result(tmp_path/'records',request,result))
    assert record.result.metadata['full_stages_available']==(mode=='full')


@pytest.mark.parametrize('field,value', [('frame_id',1),('generation',9),('full_stages_available',True),
    ('stage_sum_verified',True),('stages',[{}]),('graph_elapsed_cycles',1)])
def test_summary_reopen_rejects_misleading_or_wrong_identity(tmp_path,field,value):
    with FormalPeer(busy_once=False) as peer:
        outcome=session.run_formal(assets()[2],replace(local_config(peer),telemetry_mode='summary'),tmp_path,state_dir=tmp_path/'state')
    assert outcome.report['complete']
    profile=deepcopy(outcome.profile);profile[field]=value
    with pytest.raises(ValueError):
        validate_capture(outcome.rgb,outcome.mask,outcome.report,profile,assets()[0],endpoints=(outcome.report['board'],outcome.report['pc']))


def test_cancelled_operation_preserves_failure_trace_and_partial_mask(tmp_path):
    cancel=Event()
    def progress(stage,count):
        if stage=='Mask 读取' and count==8:cancel.set()
    with FormalPeer(busy_once=False) as peer:
        outcome=session.run_formal(assets()[2],replace(local_config(peer),telemetry_mode='summary'),tmp_path,state_dir=tmp_path/'state',cancel=cancel,progress=progress)
        assert not any(r[0]==7 for r in peer.requests)
    assert not outcome.report['complete'] and outcome.report['cancelled']
    assert outcome.report['operation_trace']['calls'][-1]['success'] is False
    assert outcome.report['stage_timings_ms']['finish'] is None
    assert (outcome.directory/'partial_mask.raw').read_bytes()==outcome.mask


def test_summary_rejects_saturation_and_disabled_trace_is_explicit(tmp_path):
    with FormalPeer(busy_once=False) as peer:
        outcome=session.run_formal(assets()[2],replace(local_config(peer),telemetry_mode='summary',trace_operations=False),tmp_path,state_dir=tmp_path/'state')
    assert outcome.report['complete']
    assert outcome.report['operation_trace']['enabled'] is False
    assert outcome.report['operation_trace']['calls']==[]
    total=dict(outcome.profile['total']);total['cycles']=0xffffffff
    with pytest.raises(ValueError):make_summary(total,1,1)
