from dataclasses import replace
import json
from threading import Event

import pytest
import numpy as np

from dermademo.device.formal_session import FormalConfig
from dermademo.formal_queue import FormalQueue, freeze_input, read_input
from dermademo.records import load_record
from teaching_fixtures import prepared_case, offline_outcome


def inputs(tmp_path, count=2):
    return [freeze_input(p.path, tmp_path/'snapshots', prepared=p) for p in (prepared_case(i) for i in range(count))]


def install_offline_wire(monkeypatch, tmp_path):
    calls = []
    def run(rgb, config, output, **kwargs):
        from dermademo.formal_input import PreparedImage
        from pathlib import Path
        prepared = PreparedImage(Path(kwargs['input_context']['source_filename']), kwargs['source_bytes'], rgb, kwargs['input_context'])
        calls.append(config)
        return offline_outcome(prepared, tmp_path)
    monkeypatch.setattr('dermademo.formal_queue.run_formal', run)
    return calls


def test_freeze_preserves_original_bytes_and_detects_snapshot_tamper(tmp_path):
    p = prepared_case()
    path = tmp_path/'任意文件名.jpg'
    path.write_bytes(p.source_bytes)
    item = freeze_input(path, tmp_path/'snapshots')
    path.write_bytes(b'original changed')
    assert read_input(item).source_bytes == p.source_bytes
    assert read_input(item).rgb == p.rgb
    (item.folder/'input.rgb').write_bytes(bytes(196608))
    with pytest.raises(ValueError): read_input(item)


def test_serial_queue_reuses_only_after_success_saves_reopens_and_new_frames(tmp_path, monkeypatch):
    calls = install_offline_wire(monkeypatch, tmp_path)
    queue = FormalQueue(inputs(tmp_path), FormalConfig(reuse_model=True), tmp_path/'run')
    first = queue.step()
    assert first.success and not calls[0].reuse_model
    assert load_record(first.row['record_directory']).result.mask.tobytes() == first.pair[1].mask.tobytes()
    queue.pause()
    with pytest.raises(ValueError): queue.step()
    queue.resume()
    second = queue.step()
    assert second.success and calls[1].reuse_model
    assert first.row['frame_id'] != second.row['frame_id']
    assert queue.status == 'completed'
    saved = json.loads((queue.directory/'queue.json').read_text('utf-8'))
    assert saved['success_count'] == 2 and saved['failure_count'] == 0
    assert saved['throughput_FPS'] is None and saved['continuous_5FPS_accepted'] is False
    assert queue.summary()['wall_ms'] == queue.summary()['wall_ms']


def test_loop_is_real_new_frame_not_replay_and_stop_is_durable(tmp_path, monkeypatch):
    calls = install_offline_wire(monkeypatch, tmp_path)
    queue = FormalQueue(inputs(tmp_path,1), FormalConfig(), tmp_path/'run', loop=True)
    a,b = queue.step(),queue.step()
    assert a.success and b.success and len(calls)==2 and a.row['frame_id'] != b.row['frame_id']
    assert a.row['cycle']==1 and b.row['cycle']==2 and a.row['record_directory'] != b.row['record_directory']
    queue.stop()
    assert queue.status=='stopped'
    with pytest.raises(ValueError):queue.step()
    assert json.loads((queue.directory/'queue.json').read_text('utf-8'))['status']=='stopped'


@pytest.mark.parametrize('finish', [False, True])
def test_wire_failure_pauses_and_requires_explicit_recovery_then_reload(finish, tmp_path, monkeypatch):
    original_calls = install_offline_wire(monkeypatch, tmp_path)
    queue = FormalQueue(inputs(tmp_path), FormalConfig(), tmp_path/'run')
    assert queue.step().success
    def fail(rgb, config, output, **kwargs):
        p = read_input(queue.items[queue.index])
        o = offline_outcome(p, tmp_path)
        o.report.update(complete=False, error='test failure', hardware_finish_confirmed=finish,
                        manual_recovery_required=not finish, failure_domain='local_storage' if finish else 'session')
        return o
    monkeypatch.setattr('dermademo.formal_queue.run_formal', fail)
    step = queue.step()
    assert not step.success and queue.status=='failed' and queue.index==1
    assert step.row['hardware_finish_confirmed'] is finish
    with pytest.raises(ValueError):queue.resume()
    calls = install_offline_wire(monkeypatch, tmp_path)
    queue.resume(recovery_confirmed=True)
    assert queue.step().success and not calls[0].reuse_model


def test_finish_success_but_four_view_save_failure_never_marks_item_success(tmp_path, monkeypatch):
    install_offline_wire(monkeypatch, tmp_path)
    monkeypatch.setattr('dermademo.formal_queue.save_result', lambda *_a,**_k: (_ for _ in ()).throw(OSError('disk full')))
    queue = FormalQueue(inputs(tmp_path,1), FormalConfig(), tmp_path/'run')
    step = queue.step()
    assert not step.success and step.pair is None and queue.status=='failed'
    assert step.row['hardware_finish_confirmed'] and not step.row['local_record_complete']
    assert queue.summary()['success_count']==0 and step.row['failure_domain']=='local_record_or_presentation'


def test_input_corruption_and_pre_cancel_never_touch_network(tmp_path, monkeypatch):
    monkeypatch.setattr('dermademo.formal_queue.run_formal',lambda *_a,**_k:pytest.fail('invalid input reached wire'))
    items=inputs(tmp_path,1)
    queue=FormalQueue(items,FormalConfig(),tmp_path/'cancel')
    cancel=Event();cancel.set()
    step=queue.step(cancel=cancel)
    assert not step.success and step.row['status']=='cancelled' and not step.row['manual_recovery_required']
    assert queue.summary()['cancelled_count']==1
    (items[0].folder/'input.json').write_text('{}','utf-8')
    other=FormalQueue(items,FormalConfig(),tmp_path/'corrupt')
    assert not other.step().success and other.rows[0]['failure_domain']=='local_input'


def test_queue_journal_failure_never_claims_success_or_contacts_board(tmp_path,monkeypatch):
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'run')
    monkeypatch.setattr('dermademo.formal_queue.run_formal',lambda *_a,**_k:pytest.fail('uncommitted journal reached wire'))
    monkeypatch.setattr(queue,'_persist',lambda:(_ for _ in ()).throw(OSError('journal unwritable')))
    step=queue.step()
    assert not step.success and step.row['failure_domain']=='queue_index_storage' and queue.index==0


def test_no_reentrant_submission_and_attempt_limit(tmp_path,monkeypatch):
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'run',loop=True)
    queue._lock.acquire()
    try:
        with pytest.raises(RuntimeError):queue.step()
    finally:queue._lock.release()
    monkeypatch.setattr('dermademo.formal_queue.MAX_ATTEMPTS',0)
    with pytest.raises(ValueError):queue.step()
    assert queue.status=='limit_reached'


def test_journal_failure_after_record_commit_keeps_evidence_and_resets_position(tmp_path,monkeypatch):
    install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'run',loop=True)
    original=queue._persist
    def fail_only_at_final_commit():
        if queue.rows:raise OSError('final index failure')
        original()
    monkeypatch.setattr(queue,'_persist',fail_only_at_final_commit)
    step=queue.step()
    assert not step.success and queue.index==0 and queue.cycle==1
    assert step.row['local_record_complete'] and step.row['hardware_finish_confirmed']
    assert load_record(step.row['record_directory']).result is not None
    assert queue.summary()['success_count']==0


def test_low_disk_space_and_corrupt_preview_stop_before_sending(tmp_path,monkeypatch):
    from types import SimpleNamespace
    items=inputs(tmp_path,1)
    monkeypatch.setattr('dermademo.formal_queue.run_formal',lambda *_a,**_k:pytest.fail('preflight failed but sent'))
    monkeypatch.setattr('dermademo.formal_queue.shutil.disk_usage',lambda *_:SimpleNamespace(free=0))
    queue=FormalQueue(items,FormalConfig(),tmp_path/'space')
    assert not queue.step().success and not queue.rows[0]['manual_recovery_required']
    monkeypatch.setattr('dermademo.formal_queue.shutil.disk_usage',lambda *_:SimpleNamespace(free=1024**3))
    (items[0].folder/'preview.png').write_bytes(b'wrong preview')
    queue=FormalQueue(items,FormalConfig(),tmp_path/'preview')
    assert not queue.step().success and queue.rows[0]['failure_domain']=='local_input'


def test_last_frame_presentation_failure_can_be_resolved_without_resending(tmp_path,monkeypatch):
    calls=install_offline_wire(monkeypatch,tmp_path)
    queue=FormalQueue(inputs(tmp_path,1),FormalConfig(),tmp_path/'run')
    step=queue.step()
    assert step.success and queue.status=='completed'
    queue.note_presentation_failure('display unavailable')
    assert json.loads((queue.directory/'queue.json').read_text('utf-8'))['status']=='failed'
    queue.resume(recovery_confirmed=True)
    assert queue.status=='completed' and len(calls)==1
    assert queue.rows[0]['presentation_error']=='display unavailable'


def test_real_localhost_queue_keeps_wire_bytes_new_ids_reuse_and_summary(tmp_path):
    from pathlib import Path
    from formal_mask_peer import FormalPeer, local_config
    from dermademo.resources import resource_path
    from dermademo.validation import bundled_reference
    items=inputs(tmp_path)
    state=tmp_path/'state';state.mkdir()
    (state/'transaction_highwater.json').write_text(json.dumps({'reserved_through':3000000000}),'utf-8')
    with FormalPeer(drop_once=True,busy_once=True) as peer:
        config=replace(local_config(peer,reuse=False),telemetry_mode='summary')
        queue=FormalQueue(items,config,tmp_path/'wire',state_dir=state)
        frames=[]
        for item in items:
            p=read_input(item)
            for path in (resource_path('full_image_root')/'samples').glob('*/input.rgb888.bin'):
                if path.read_bytes()==p.rgb:peer.mask=(path.parent/'mask.bin').read_bytes()
            step=queue.step()
            assert step.success,step.row
            assert bytes(peer.rgb)==p.rgb
            reopened=load_record(step.row['record_directory'])
            assert reopened.result.metadata['source_kind']=='protocol_simulator'
            assert not reopened.result.metadata['full_stages_available']
            assert np.array_equal(bundled_reference((reopened.request,reopened.result))['mask'],reopened.result.mask)
            assert step.row['transport']['BUSY'] and step.row['transport']['retries']
            frames.append(step.row['frame_id'])
        assert frames[1]>frames[0]>3000000000
        assert [row['model_reuse'] for row in queue.rows]==[False,True]
        assert queue.summary()['success_count']==2
