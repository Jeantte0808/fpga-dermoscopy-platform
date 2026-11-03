from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import struct
from threading import Event
import zipfile

import numpy as np
import pytest

from dermademo.device.formal_capture import load_capture
from dermademo.device.formal_protocol import FormalProtocolError, assets, decode, encode
from dermademo.device.formal_session import run_formal, transaction_state
from dermademo.formal_results import live_pair, replay_pair
from dermademo.records import load_record
from dermademo.resources import resource_path
from dermademo.storage import save_result
from formal_mask_peer import FormalPeer, local_config


def test_three_real_captures_replay_save_and_raw_reopen_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_k: pytest.fail('replay opened socket'))
    root = resource_path('formal_root')
    for folder in sorted((root/'captures').iterdir()):
        request, result = replay_pair(folder)
        assert result.metadata['display_mode'] == 'offline_replay'
        assert result.metadata['source_kind'] == 'saved_FPGA_capture'
        assert result.metadata['live_run_performed'] is False
        assert result.measurements.area_px2 == 31174
        assert sha256(result.mask.tobytes()).hexdigest() == 'c823416f7998dbc822e3127be433666dd8adb6f3c6fdb74f9da756ed38e9e509'
        assert result.timings_ms['fpga_graph'] == result.metadata['graph_elapsed_ms']
        assert not np.array_equal(request.image_bgr, result.enhanced_bgr)
        saved = save_result(tmp_path, request, result)
        restored = load_record(saved)
        assert restored.result.metadata['source_kind'] == 'saved_FPGA_capture'
        assert np.array_equal(restored.result.mask, result.mask)
        (saved/'mask.raw').write_bytes(bytes(65536))
        with pytest.raises(ValueError, match='原始字节'):
            load_record(saved)


def test_input_and_profile_identity_tampering_rejected(tmp_path):
    import shutil
    root = resource_path('formal_root')
    folder = sorted((root/'captures').iterdir())[0]
    copied = tmp_path/'capture'
    shutil.copytree(folder, copied)
    profile = json.loads((copied/'profile.json').read_bytes())
    profile['generation'] += 1
    (copied/'profile.json').write_text(json.dumps(profile))
    with pytest.raises(ValueError, match='统计身份'):
        load_capture(copied, root/'assets/manifest.json')
    (copied/'input.rgb').write_bytes(bytes(196608))
    with pytest.raises(ValueError, match='输入长度或 SHA'):
        load_capture(copied, root/'assets/manifest.json')


def test_encoder_matches_handoff_reconstructed_packet_and_crc():
    root = resource_path('formal_root')
    with zipfile.ZipFile(root/'original_handoff.zip') as archive:
        request = archive.read('pc_formal_mask_handoff_20261025/packet_examples/FINISH_request_reconstructed.bin')
        response = archive.read('pc_formal_mask_handoff_20261025/packet_examples/FINISH_reply_reconstructed.bin')
    txn, gen, frame, index = struct.unpack_from('!IIII', request, 8)
    assert encode(7, txn, gen, frame=frame, index=index) == request
    assert decode(response)[0]['op'] == 7
    corrupted = bytearray(response)
    corrupted[75] ^= 1
    with pytest.raises(ValueError, match='CRC'):
        decode(corrupted)


def test_transaction_highwater_merge_lock_persistence_and_no_wrap(tmp_path):
    source = tmp_path/'old.json'
    source.write_text(json.dumps({'reserved_through': 3000000000}))
    with transaction_state(tmp_path/'state', source) as next_id:
        first = next_id()
        assert first == 3000000001 and next_id() == first + 1
        with pytest.raises(ValueError, match='占用'):
            with transaction_state(tmp_path/'state'):
                pass
    with transaction_state(tmp_path/'state') as next_id:
        assert next_id() > first + 9999
    source.write_text(json.dumps({'reserved_through': 0xfffffffe}))
    with transaction_state(tmp_path/'state', source) as next_id:
        with pytest.raises(ValueError, match='耗尽'):
            next_id()


@pytest.mark.parametrize('reuse', [False, True])
def test_local_session_parameters_mask_finish_249_pages_and_exact_retry(tmp_path, reuse):
    _manifest, _blob, sample = assets()
    with FormalPeer(drop_once=True) as peer:
        outcome = run_formal(sample, local_config(peer, reuse=reuse), tmp_path, state_dir=tmp_path/'state')
        assert outcome.report['complete'], outcome.report
        assert outcome.report['origin'] == 'protocol_simulator'
        assert outcome.report['BUSY'] == 1 and outcome.report['retries'] >= 2
        assert outcome.profile['transport']['successful_operations'] == 249
        assert [r[0] for r in peer.requests].count(2) == (0 if reuse else 2931)
        assert [r[0] for r in peer.requests].count(7) == 1
        request, result = live_pair(outcome)
        assert result.metadata['source_kind'] == 'protocol_simulator' and request.is_synthetic
        assert result.metadata['pure_FPGA_inference_ms'] is None
        assert result.metadata['fps_5_accepted'] is False
        assert not (outcome.directory/'INCOMPLETE.txt').exists()


@pytest.mark.parametrize('failure', ['bad_generation', 'bad_mask'])
def test_invalid_mask_or_identity_never_finishes_or_makes_result(tmp_path, failure):
    with FormalPeer(**{failure: True}, busy_once=False) as peer:
        outcome = run_formal(assets()[2], local_config(peer), tmp_path, state_dir=tmp_path/'state')
        assert outcome.report['complete'] is False
        assert not any(r[0] == 7 for r in peer.requests)
        assert (outcome.directory/'INCOMPLETE.txt').exists()
        assert not (outcome.directory/'mask.raw').exists()
        with pytest.raises(ValueError):
            live_pair(outcome)


def test_profile_failure_keeps_actual_mask_without_fabricated_timing(tmp_path):
    with FormalPeer(bad_profile=True) as peer:
        outcome = run_formal(assets()[2], local_config(peer), tmp_path, state_dir=tmp_path/'state')
    assert outcome.report['complete'] is True and outcome.profile['complete'] is False
    _request, result = live_pair(outcome)
    assert 'fpga_graph' not in result.timings_ms and result.metadata['graph_elapsed_ms'] is None
    assert result.timings_ms['image_transport_e2e'] == outcome.report['image_E2E_ms']


def test_cancel_retains_diagnostics_without_automatic_finish_or_update(tmp_path):
    cancel = Event()
    def progress(stage, count):
        if stage == 'RGB 上传' and count == 16:
            cancel.set()
    with FormalPeer() as peer:
        outcome = run_formal(assets()[2], local_config(peer), tmp_path, state_dir=tmp_path/'state',
                             cancel=cancel, progress=progress)
        assert outcome.report['complete'] is False and outcome.report['cancelled'] is True
        assert not any(r[0] in (1, 7) for r in peer.requests)
    assert (outcome.directory/'report.json').is_file()
