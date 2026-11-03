"""Convert a validated actual capture to the existing four-view/save contracts."""

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from .contracts import BackendCapabilities, BackendConfig, Measurements, ProcessingRequest, ProcessingResult
from .imaging import make_overlay, max_feret_diameter
from .resources import resource_path
from .device.formal_capture import load_capture, validate_capture
from .device.formal_protocol import assets
from .formal_input import EDUCATION_NOTICE, MODEL_VERSION, FIXED_THRESHOLD

# Stable backend key keeps rc.1/rc.2 records compatible; hardware is a separate declaration.
CAPABILITIES = BackendCapabilities('fpga_formal_v5', 'FPGA · 正式 CNN Mask', 'CNN_HW_R1/FCP1-v1', False)


def processing_pair(capture, *, live=False, capture_directory=None, display_clip=2.0):
    started = perf_counter()
    if not .5 <= display_clip <= 8:
        raise ValueError('展示增强强度无效')
    original = cv2.cvtColor(np.frombuffer(capture.rgb, np.uint8).reshape(256, 256, 3), cv2.COLOR_RGB2BGR)
    original = np.ascontiguousarray(original)
    mask = np.frombuffer(capture.mask, np.uint8).reshape(256, 256).copy()
    lab = cv2.cvtColor(original, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=display_clip, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    source = 'protocol_simulator' if capture.report.get('origin') == 'protocol_simulator' else (
        'live_FPGA_session' if live else 'saved_FPGA_capture')
    context = capture.info.get('input_context')
    config = BackendConfig(CAPABILITIES.key, '1', {'display_clahe': display_clip,
                                                'input_policy': ('member2_ROI' if context and context.get('roi') is not None else
                                                                 'member2_full_image_v1' if context else 'exact_RGB888'),
                                                'source_kind': source})
    original.setflags(write=False)
    request = ProcessingRequest(original, Path(context['source_filename'] if context else 'formal_input.rgb'), config,
                                is_synthetic=source == 'protocol_simulator', source_bytes=capture.source_bytes)
    graph = capture.info['graph_elapsed_ms']
    timings = {'end_to_end': capture.report['image_E2E_ms'], 'image_transport_e2e': capture.report['image_E2E_ms']}
    if graph is not None:
        timings['fpga_graph'] = graph
    for key, value in (('model_upload', capture.report.get('model_upload_ms')),
                       ('model_verify', capture.report.get('model_verify_ms')),
                       ('rgb_upload', capture.report.get('RGB_upload_ms')),
                       ('result_wait', capture.report.get('host_observed_result_wait_ms')),
                       ('telemetry', capture.report.get('telemetry_ms'))):
        if value is not None:
            timings[key] = value
    overlay = make_overlay(original, mask)
    measured = Measurements(int(np.count_nonzero(mask)), max_feret_diameter(mask), 'PC / FPGA 原始 Mask · 256 网格')
    timings['pc_presentation'] = (perf_counter() - started) * 1000
    metadata = {**capture.info, 'source_kind': source, 'display_mode': 'live_result' if live else 'offline_replay',
                'live_run_performed': live, 'postprocessing': {'region_policy': 'none', 'fill_holes': False},
                'coordinate_mapping': {'type': 'identity', 'output_space': 'original_image', 'grid': [256, 256]},
                'display_enhancement_only': True, 'status': 'ok' if measured.area_px2 else 'no_foreground',
                'calibration': {'status': 'not_calibrated', 'area_mm2': None, 'diameter_mm': None},
                'timing_scope': 'image_begin_to_legal_FINISH_excluding_model_load_telemetry_and_PC_presentation',
                'graph_definition': '100MHz RGB LUT/CNN/DDR wait; not pure inference',
                'capture_directory': str(capture_directory) if capture_directory else None,
                'wire_report': capture.report, 'wire_profile': capture.profile}
    metadata.update(model_version=capture.report.get('model_version', MODEL_VERSION),
                    fixed_logits_threshold=dict(FIXED_THRESHOLD), education_notice=EDUCATION_NOTICE,
                    hardware_declaration=capture.report.get('hardware_declaration'),
                    hardware_identity_verified=False, measurement_space='model_input_256_grid',
                    source_attribution=(context['attribution'] if context else {
                        'kind': 'bundled_ISIC_sample' if capture.info['official_fixed_sample'] else 'unknown_RGB_source',
                        'note': '公开 ISIC 固定样例' if capture.info['official_fixed_sample'] else '原始 RGB 来源未记录'}))
    from .roi_edge import result_edge_report
    edge = result_edge_report(metadata,mask)
    if edge is not None:
        metadata['roi_edge_contact'] = edge
    timings['pc_presentation'] = (perf_counter() - started) * 1000
    result = ProcessingResult(request.task_id, enhanced, mask, overlay, measured, CAPABILITIES, timings, metadata)
    metadata['stage_timings_ms'] = dict(capture.report.get('stage_timings_ms', {}), display_prepare=timings['pc_presentation'])
    metadata['timing_unavailable_policy'] = 'missing or null means unavailable; never zero or previous-frame values'
    result.validate(request)
    for array in (mask, enhanced, overlay):
        array.setflags(write=False)
    from .reference_library import update
    update((request,result))
    return request, result


def replay_pair(folder=None, display_clip=2.0):
    root = resource_path('formal_root')
    folder = Path(folder) if folder else sorted((root / 'captures').iterdir())[0]
    assets()
    capture = load_capture(folder, root / 'assets/manifest.json')
    return processing_pair(capture, capture_directory=folder, display_clip=display_clip)


def live_pair(outcome, display_clip=2.0):
    manifest, _blob, _sample = assets()
    capture = validate_capture(outcome.rgb, outcome.mask, outcome.report, outcome.profile, manifest,
                               require_profile=False, endpoints=(outcome.report['board'], outcome.report['pc']),
                               source_bytes=outcome.source_bytes)
    return processing_pair(capture, live=True, capture_directory=outcome.directory, display_clip=display_clip)
