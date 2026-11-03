"""Full-image input parity, wire identity and lossless record round trips."""

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import socket
import sys
import zipfile

import cv2
import numpy as np
from PIL import Image
import pytest

from dermademo import formal_preprocessing as prep
from dermademo.formal_input import prepare_image, verify_preprocessing, validate_source, EDUCATION_NOTICE
from dermademo.device.formal_session import run_formal
from dermademo.device.formal_capture import load_capture
from dermademo.formal_results import live_pair, replay_pair
from dermademo.resources import resource_path
from dermademo.records import load_record
from dermademo.storage import save_result, ExportError
from formal_mask_peer import FormalPeer, local_config

ROOT = resource_path('full_image_root')
ROWS = json.loads((ROOT / 'samples.json').read_text('utf-8'))


def test_upstream_immutable_manifest_and_three_reference_encodings():
    with zipfile.ZipFile(ROOT / 'original_handoff.zip') as archive:
        prefix = 'FPGA_Skin_FullImage_RGB888_V1_03_samples_r1/'
        manifest = json.loads(archive.read(prefix + 'manifest.json'))
        assert len(manifest['files']) == 38
        for row in manifest['files']:
            actual = (ROOT / row['path']).read_bytes()
            assert actual == archive.read(prefix + row['path'])
            assert len(actual) == row['bytes'] and sha256(actual).hexdigest() == row['sha256']
    for row in ROWS:
        folder = ROOT / row['directory']
        with Image.open(folder / 'input_rgb_preview.png') as image:
            assert np.array(image).tobytes() == (folder / 'input.rgb888.bin').read_bytes()
        with Image.open(folder / 'mask.png') as image:
            mask = np.array(image)
            assert mask.shape == (256, 256) and set(np.unique(mask)) <= {0, 255}
            assert mask.tobytes() == (folder / 'mask.bin').read_bytes()


def test_current_environment_three_goldens_and_intermediate_jpeg_are_exact():
    checked = verify_preprocessing()
    assert len(checked['cases']) == 3
    assert all(r['different_bytes'] == 0 for r in checked['cases'])
    assert checked['physical_FPGA_tested'] is False


def test_upstream_sixteen_exif_cases_unsupported_formats_and_explicit_bypasses(tmp_path, monkeypatch):
    # Run the immutable upstream checks against the reviewed PC port, not a modified test.
    monkeypatch.setitem(sys.modules, 'image_to_rgb888', prep)
    spec = importlib.util.spec_from_file_location('upstream_preprocessing_checks', ROOT / 'software/check_image_to_rgb888.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_checks(tmp_path)
    assert len(report['orientation_checks']) == 16
    assert len(report['unsupported_inputs_rejected']) == 6
    assert report['processed_and_binary_bypass_verified']
    assert report['default_jpeg_quality_matches'] == [95]


def test_unknown_environment_rejected_not_silently_accepted(monkeypatch):
    actual = prep.environment()
    monkeypatch.setattr(prep, 'environment', lambda: dict(actual, opencv_build_sha256='0' * 64))
    with pytest.raises(RuntimeError, match='Unverified preprocessing dependency'):
        prepare_image(ROOT / ROWS[0]['directory'] / ROWS[0]['input_file'])


def test_unicode_path_and_source_snapshot_survive_original_change(tmp_path):
    row = ROWS[0]
    path = tmp_path / '中文 空格原图.jpg'
    source = (ROOT / row['directory'] / row['input_file']).read_bytes()
    path.write_bytes(source)
    prepared = prepare_image(path)
    path.write_bytes(b'changed after preview')
    assert prepared.source_bytes == source
    assert prepared.rgb == (ROOT / row['directory'] / 'input.rgb888.bin').read_bytes()
    assert prepared.context['attribution']['dataset'] is None
    validate_source(prepared.context, prepared.rgb, prepared.source_bytes)


@pytest.mark.parametrize('kind', ['icc', 'broken', 'orientation', 'oversize'])
def test_extra_unsupported_inputs_fail_explicitly(tmp_path, monkeypatch, kind):
    path = tmp_path / 'unsupported.png'
    if kind == 'broken':
        path.write_bytes(b'not an image')
    elif kind == 'icc':
        Image.new('RGB', (10, 8)).save(path, icc_profile=b'fake profile')
    elif kind == 'orientation':
        exif = Image.Exif(); exif[274] = 9
        Image.new('RGB', (10, 8)).save(path, exif=exif)
    else:
        # Header-only size probe; avoid allocating >24 MP in the test.
        class Large:
            width, height = 6000, 5000
            def __enter__(self): return self
            def __exit__(self, *_args): pass
        monkeypatch.setattr(prep.Image, 'open', lambda *_a: Large())
        path.write_bytes(b'header')
    with pytest.raises(ValueError):
        prepare_image(path)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_full_image_wire_capture_save_reopen_and_resave(index, tmp_path):
    row = ROWS[index]
    prepared = prepare_image(ROOT / row['directory'] / row['input_file'])
    state = tmp_path / 'latest-state'
    state.mkdir()
    (state / 'transaction_highwater.json').write_text(json.dumps({'reserved_through': 3000000000}))
    with FormalPeer() as peer:
        peer.mask = (ROOT / row['directory'] / 'mask.bin').read_bytes()
        outcome = run_formal(prepared.rgb, local_config(peer), tmp_path / 'captures', state_dir=state,
                             input_context=prepared.context, source_bytes=prepared.source_bytes)
        assert bytes(peer.rgb) == prepared.rgb
    assert outcome.report['complete'] and outcome.report['hardware_finish_confirmed']
    assert outcome.report['frame_id'] > 3000000000
    assert json.loads((state / 'transaction_highwater.json').read_text())['reserved_through'] >= outcome.report['frame_id']
    request, result = live_pair(outcome)
    assert result.metadata['source_kind'] == 'protocol_simulator'  # A cached Mask peer never runs a CNN.
    assert result.metadata['fixed_logits_threshold']['q'] == 1
    assert result.metadata['hardware_identity_verified'] is False
    assert result.metadata['measurement_space'] == 'model_input_256_grid'
    assert result.metadata['input_context'] == prepared.context
    assert result.metadata['preprocessing'] == prepared.context['preprocessing']['version']
    assert request.config.values['input_policy'] == 'member2_full_image_v1'
    assert request.source_bytes == prepared.source_bytes
    assert 'fpga_graph' in result.timings_ms and result.timings_ms['image_transport_e2e'] > 0
    replay_request, replay_result = replay_pair(outcome.directory)
    assert replay_request.source_bytes == prepared.source_bytes
    assert replay_result.metadata['display_mode'] == 'offline_replay'
    saved = save_result(tmp_path, request, result)
    restored = load_record(saved)
    assert restored.request.source_bytes == prepared.source_bytes
    assert restored.document['education_notice'] == EDUCATION_NOTICE
    assert restored.document['source']['representation'] == 'actual_model_input_256_grid'
    assert (saved / 'input.rgb').read_bytes() == prepared.rgb
    assert restored.result.metadata == result.metadata
    saved_again = save_result(tmp_path, restored.request, restored.result, origin_record=restored.document)
    assert load_record(saved_again).request.source_bytes == prepared.source_bytes
    assert (saved_again / 'source_image.bin').read_bytes() == prepared.source_bytes
    (saved / 'source_image.bin').write_bytes(b'damaged')
    with pytest.raises(ValueError, match='原始图片快照'):
        load_record(saved)
    (outcome.directory / 'source_image.bin').write_bytes(b'damaged')
    with pytest.raises(ValueError, match='原始图片'):
        load_capture(outcome.directory, resource_path('formal_root') / 'assets/manifest.json')


def test_original_snapshot_save_failure_is_local_before_any_network(tmp_path, monkeypatch):
    import dermademo.device.formal_session as session
    prepared = prepare_image(ROOT / ROWS[0]['directory'] / ROWS[0]['input_file'])
    real_write = session.write_bytes
    def fail(path, value):
        if Path(path).name == 'source_image.bin':
            raise session.LocalPersistenceError('disk full')
        real_write(path, value)
    monkeypatch.setattr(session, 'write_bytes', fail)
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_k: pytest.fail('network reached before source snapshot saved'))
    outcome = run_formal(prepared.rgb, session.FormalConfig(), tmp_path,
                         input_context=prepared.context, source_bytes=prepared.source_bytes)
    assert not outcome.report['complete'] and not outcome.report['hardware_finish_confirmed']
    assert outcome.report['failure_domain'] == 'local_storage'
    assert outcome.report['manual_recovery_required'] is False
    assert (outcome.directory / 'INCOMPLETE.txt').exists()


def test_corrupt_context_rejected_before_network_and_old_capture_not_upgraded(tmp_path, monkeypatch):
    prepared = prepare_image(ROOT / ROWS[0]['directory'] / ROWS[0]['input_file'])
    context = deepcopy(prepared.context)
    context['preprocessing']['rgb888_sha256'] = '0' * 64
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_k: pytest.fail('invalid input reached network'))
    from dermademo.device.formal_session import FormalConfig
    with pytest.raises(ValueError, match='实际 RGB'):
        run_formal(prepared.rgb, FormalConfig(), tmp_path, input_context=context, source_bytes=prepared.source_bytes)
    old_request, old_result = replay_pair()
    assert old_request.source_bytes is None and old_result.metadata['hardware_declaration'] is None
    assert load_record(save_result(tmp_path, old_request, old_result)).request.source_bytes is None


def test_simulator_capture_cannot_claim_external_board(tmp_path):
    import shutil
    folder = tmp_path / 'capture'
    original = sorted((resource_path('formal_root') / 'captures').iterdir())[0]
    shutil.copytree(original, folder)
    report = json.loads((folder / 'report.json').read_text('utf-8'))
    report['origin'] = 'protocol_simulator'
    (folder / 'report.json').write_text(json.dumps(report), 'utf-8')
    with pytest.raises(ValueError, match='回环端点'):
        load_capture(folder, resource_path('formal_root') / 'assets/manifest.json')
