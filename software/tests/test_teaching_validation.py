from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from dermademo.analysis_identity import frozen_pair, result_identity
from dermademo.formal_results import live_pair, replay_pair
from dermademo.resources import resource_path
from dermademo.roi import decode_source
from dermademo.roi_preprocessing import ConfirmedROIProcessor
from dermademo.validation import (binary_mask, bundled_reference, historical_p7_comparison,
                                  load_reference, mask_comparison, performance_snapshot)
from teaching_fixtures import prepared_case, offline_outcome


def test_exactness_is_separate_from_annotation_scores():
    a = np.zeros((256,256), np.uint8); b = a.copy()
    a[1,1] = b[1,1] = 255
    b[1,2] = 255
    comparison = mask_comparison(a, b, kind='int8')
    assert comparison['different_pixels'] == 1 and not comparison['byte_exact']
    assert 'dice' not in comparison and 'iou' not in comparison
    scored = mask_comparison(a, b, kind='annotation')
    assert scored['dice'] == pytest.approx(2/3) and scored['iou'] == .5
    assert scored['prediction_only'] == 0 and scored['annotation_only'] == 1
    assert scored['difference_bgr'][1,2,0] > scored['difference_bgr'][1,2,2]
    empty = np.zeros_like(a)
    assert mask_comparison(empty, empty, kind='annotation')['dice'] == 1
    assert mask_comparison(empty, a, kind='annotation')['iou'] == 0
    with pytest.raises(ValueError):
        binary_mask(bytes([128])*65536)


@pytest.mark.parametrize('index', [0,1,2])
def test_full_goldens_bind_source_model_preprocessing_and_rgb(index, tmp_path):
    prepared = prepared_case(index)
    pair = live_pair(offline_outcome(prepared, tmp_path))
    reference = bundled_reference(pair)
    assert reference and np.array_equal(reference['mask'], pair[1].mask)
    assert result_identity(pair)['source_kind'] == 'protocol_simulator'
    saved_version = pair[1].metadata['input_context']['preprocessing']['version']
    pair[1].metadata['input_context']['preprocessing']['version'] = 'another_preprocessing'
    assert bundled_reference(pair) is None
    pair[1].metadata['input_context']['preprocessing']['version'] = saved_version
    pair[1].metadata['model_version'] = 'another_model'
    assert bundled_reference(pair) is None


def test_roi_goldens_keep_scope_and_mapping_identity(tmp_path):
    root = resource_path('roi_root')
    row = json.loads((root/'samples.json').read_text('utf-8'))[1]
    prepared = ConfirmedROIProcessor().prepare(decode_source(root/row['directory']/row['input_file']), row['roi'])
    pair = live_pair(offline_outcome(prepared, tmp_path))
    reference = bundled_reference(pair)
    assert np.array_equal(reference['mask'], pair[1].mask)
    assert reference['identity']['roi'] == row['roi']
    pair[1].metadata['input_context'] = deepcopy(prepared.context)
    pair[1].metadata['input_context']['preprocessing']['version'] = 'different'
    assert bundled_reference(pair) is None


def reference_document(tmp_path, pair, *, kind='annotation'):
    raw = pair[1].mask.tobytes()
    (tmp_path/'mask.bin').write_bytes(raw)
    identity = result_identity(pair)
    doc = dict(schema='alienhome.reference.v1', kind=kind, identity=identity, mask_file='mask.bin',
               mask_sha256=sha256(raw).hexdigest(), source_note='测试标注声明，不是实板证据',
               mapping_rule=dict(confirmed_by='member2', version='fixture_grid_v1'))
    path = tmp_path/'reference.json'
    path.write_text(json.dumps(doc), 'utf-8')
    return path, doc


@pytest.mark.parametrize('failure', ['rgb','source','model','preprocess','roi','sha','mapping','escape','encoding'])
def test_imported_reference_refuses_wrong_binding_and_unconfirmed_mapping(failure, tmp_path):
    pair = replay_pair()
    path, data = reference_document(tmp_path, pair)
    assert load_reference(path, pair)['kind'] == 'annotation'
    keys = {'rgb':'input_rgb_sha256','source':'source_sha256','model':'model_version','preprocess':'preprocess_version','roi':'roi'}
    if failure in keys:
        data['identity'][keys[failure]] = 'wrong'
    elif failure == 'sha': data['mask_sha256'] = '0'*64
    elif failure == 'mapping': data['mapping_rule'] = {}
    elif failure == 'escape': data['mask_file'] = '../elsewhere.bin'
    else:
        raw = bytes([128])*65536
        (tmp_path/'mask.bin').write_bytes(raw)
        data['mask_sha256'] = sha256(raw).hexdigest()
    path.write_text(json.dumps(data), 'utf-8')
    with pytest.raises((ValueError, OSError)):
        load_reference(path, pair)


def test_summary_missing_statistics_are_unavailable_not_zero():
    pair = replay_pair()
    pair[1].metadata.update(full_stages_available=False, telemetry_mode='summary', stage_timings_ms={})
    pair[1].metadata.pop('user_visible_wait_ms', None)
    view = performance_snapshot(pair)
    assert view['stages'] == [] and view['values']['visible'] is None
    assert view['values'].get('local_save') is None
    assert view['origin'] == '历史实板记录回放'


def test_frozen_analysis_snapshot_isolated_from_later_frame_metadata():
    original = replay_pair()
    snapshot = frozen_pair(original)
    original[1].metadata['stage_timings_ms']['display_prepare'] = 9999
    assert snapshot[1].metadata['stage_timings_ms']['display_prepare'] != 9999
    assert snapshot[1].mask is not original[1].mask and not snapshot[1].mask.flags.writeable
    assert snapshot[0].config is original[0].config  # Config is already immutable, including mappingproxy.


def test_historical_p7_recomputes_same_rgb_full_five_frames_without_relabel():
    value = historical_p7_comparison()
    assert len(value['frames']) == 10
    assert len({r['input_sha256'] for r in value['frames']}) == 1
    assert value['values']['graph_ms']['before'] == pytest.approx(573.518646)
    assert value['values']['graph_ms']['after'] == pytest.approx(520.227412)
    assert value['values']['e2e_ms']['reduction_percent'] == pytest.approx(8.26758, abs=.00001)
    assert all(r['raw_hardware_declaration']['candidate']=='formal_perf_v6_r1_flash_spi1'
               for r in value['frames'] if r['group']=='P7')
    assert '未验收持续5FPS' in value['note']


def test_integer_reference_manifests_are_packaged():
    from dermademo.resources import resource_files, resource_path
    files = set(resource_files())
    for key in ('full_image_root', 'roi_root'):
        assert resource_path(key) / 'manifest.json' in files
