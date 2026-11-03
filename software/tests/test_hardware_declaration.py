from dataclasses import replace
from copy import deepcopy
import json

import pytest

from dermademo.device.formal_session import FormalConfig, run_formal
from dermademo.device.formal_protocol import assets
from dermademo.device.formal_capture import load_capture
from dermademo.formal_results import replay_pair, live_pair
from dermademo.hardware import hardware_declaration, hardware_label, resource_summary
from dermademo.resources import resource_path
from dermademo.storage import save_result
from dermademo.records import load_record
from formal_mask_peer import FormalPeer, local_config


def test_supplied_p7_evidence_exact_goldens_and_comparison():
    from scripts.verify_p7_handoff import audit
    result=audit()
    assert len(result['frames'])==11 and result['raw_reports_modified'] is False
    assert result['comparison_5_fixed_frames']['graph_ms']['reduction_percent']==pytest.approx(9.291979)


def test_explicit_profiles_not_automatic_and_copies_do_not_leak():
    assert FormalConfig().hardware_profile=='unspecified'
    assert hardware_declaration() is None
    with pytest.raises(ValueError):FormalConfig(hardware_profile='auto')
    p7=hardware_declaration('p7')
    assert p7['resources']['lut']==79851 and p7['setup_ns']==.021
    assert p7['mcs_sha256'] is None and p7['P7_Flash_programmed'] is False
    assert not p7['hardware_identity_verified'] and '非自动识别' in hardware_label(p7)
    p7['resources']['lut']=1
    assert hardware_declaration('p7')['resources']['lut']==79851
    assert '78,427' in resource_summary(hardware_declaration('v6-r1-flash'))
    assert '未记录' in resource_summary()


@pytest.mark.parametrize('profile', ['unspecified','v6-r1-flash','p7','p7-flash'])
def test_live_declaration_frozen_to_frame_roundtrip_no_wire_change(tmp_path,profile):
    with FormalPeer(busy_once=False) as peer:
        config=replace(local_config(peer),telemetry_mode='summary',hardware_profile=profile)
        outcome=run_formal(assets()[2],config,tmp_path,state_dir=tmp_path/'state')
        assert outcome.report['complete'],outcome.report
        assert bytes(peer.rgb)==assets()[2]
        assert len([r for r in peer.requests if r[0]==8 and r[3]])==3
    value=hardware_declaration(profile)
    assert outcome.report['hardware_declaration']==value
    loaded=load_capture(outcome.directory,resource_path('formal_root')/'assets/manifest.json')
    assert loaded.report['hardware_declaration']==value
    request,result=live_pair(outcome)
    assert result.metadata['hardware_declaration']==value
    restored=load_record(save_result(tmp_path,request,result))
    assert restored.result.metadata['hardware_declaration']==value
    assert restored.result.metadata['hardware_identity_verified'] is False


def test_historical_report_static_label_preserved_without_upgrade(tmp_path):
    request,result=replay_pair()
    assert result.metadata['hardware_declaration'] is None
    from dermademo.formal_input import hardware_reference
    old=hardware_reference()['hardware']
    result.metadata['hardware_declaration']=deepcopy(old)
    before=deepcopy(result.metadata)
    restored=load_record(save_result(tmp_path,request,result))
    assert restored.result.metadata==before
    assert restored.result.metadata['hardware_declaration']==old
    assert '原记录静态声明' in hardware_label(old)
    assert '78,427' in resource_summary(old) and '79,851' not in resource_summary(old)


def test_large_diagnostic_record_reopens_and_oversized_export_stays_incomplete(tmp_path):
    from dermademo.storage import MAX_RECORD_JSON_BYTES, ExportError
    request,result=replay_pair()
    result.metadata['diagnostic_fixture']='x'*(1100*1024)
    saved=save_result(tmp_path,request,result)
    assert (saved/'record.json').stat().st_size>1048576
    assert load_record(saved).result.metadata['diagnostic_fixture']==result.metadata['diagnostic_fixture']
    result.metadata['diagnostic_fixture']='x'*MAX_RECORD_JSON_BYTES
    with pytest.raises(ExportError) as caught:save_result(tmp_path,request,result)
    assert (caught.value.path/'INCOMPLETE.txt').is_file()
    assert not (caught.value.path/'record.json').exists()
    (saved/'record.json').write_bytes(b' '*(MAX_RECORD_JSON_BYTES+1))
    with pytest.raises(ValueError,match='8 MiB'):load_record(saved)
