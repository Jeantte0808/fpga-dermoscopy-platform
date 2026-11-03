"""Read-only capture validation adapted from the reviewed teammate capture_api.

The immutable original is retained in integrations/formal_mask_20261025/original_handoff.zip.
No inference, sockets, reference-Mask substitution or device qualification occurs here.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import ipaddress
from math import isfinite
from pathlib import Path
import struct
import zlib

from .formal_protocol import COUNTERS, IMAGE_ID, make_profile, make_summary, need


@dataclass(frozen=True)
class CaptureResult:
    rgb: bytes
    mask: bytes
    report: dict
    profile: dict | None
    info: dict
    source_bytes: bytes | None = None


def validate_capture(rgb, mask, report, profile, manifest, *, require_profile=True, endpoints=None, source_bytes=None):
    need(report.get('complete') is True and report.get('stage') == 'complete'
         and report.get('error') is None, '记录未完成或失败，保留上一次成功结果')
    for key in ('hardware_finish_confirmed', 'local_artifacts_complete'):
        need(key not in report or report[key] is True, '记录硬件/本地完成状态矛盾')
    need(report.get('protocol') == 'FCP1/FCR1_candidate' and report.get('model_image_id') == IMAGE_ID,
         '记录协议/模型不匹配')
    need(len(rgb) == 196608 and sha256(rgb).hexdigest() == report.get('input_sha256'), '输入长度或 SHA 不符')
    need(len(mask) == 65536 and set(mask) <= {0, 255}
         and sha256(mask).hexdigest() == report.get('mask_sha256'), 'Mask 长度/编码/摘要不符')
    fixed = sha256(rgb).hexdigest() == manifest['sample_RGB_sha256']
    if fixed:
        need(report.get('official_fixed_sample_exact') is True
             and sha256(mask).hexdigest() == manifest['sample_Mask_sha256'], '正式固定样例 Mask 不符')
    else:
        need(report.get('official_fixed_sample_exact') is not True, '其他输入不能声称固定样例完全一致')
    frame, generation = report.get('frame_id'), report.get('generation')
    need(all(type(v) is int and 0 < v <= 0xffffffff for v in (frame, generation)), '帧/代号无效')
    reply = report.get('last_reply', {})
    need(reply.get('op') == 7 and reply.get('error') == reply.get('input_error') == 0
         and not reply.get('flags', 16) & 16 and reply.get('flags', 0) & 2, '缺少合法 FINISH/模型资格')
    need(reply.get('frame') == reply.get('result_frame') == frame
         and reply.get('generation') == reply.get('result_generation') == generation, 'FINISH 结果身份不符')
    need(reply.get('completed_stages') == 82 and reply.get('loaded_bytes') == 2770336
         and reply.get('next_fragment') == 2931 and reply.get('next_RGB_chunk') == 192, '帧/模型未完整完成')
    board, pc = endpoints or (['192.168.0.2', 5000], ['192.168.0.3', 6102])
    need(report.get('board') == list(board) and report.get('pc') == list(pc), '记录端点不匹配')
    e2e = report.get('image_E2E_ms')
    need(type(e2e) in (int, float) and isfinite(e2e) and e2e > 0, '图像端到端耗时无效')
    need(report.get('pure_FPGA_inference_ms') is None, '不能推导纯 FPGA 推理耗时')
    graph = None
    if profile is not None and profile.get('complete') is True:
        summary = profile.get('schema') == 'FCP1_PROFILE_SUMMARY_V1'
        need(profile.get('schema') in ('FCP1_PROFILE_V1', 'FCP1_PROFILE_SUMMARY_V1') and profile.get('error') is None
             and profile.get('frame_id') == frame and profile.get('generation') == generation
             and profile.get('ui_clock_hz') == 100000000, '统计身份/时钟不符')
        stages, total = profile.get('stages', []), profile.get('total', {})
        if summary:
            need(profile.get('telemetry_mode') == 'summary' and profile.get('full_stages_available') is False
                 and profile.get('stage_sum_verified') is False and stages == [], '摘要不能声明逐层统计/求和校验')
            checked = make_summary(total, frame, generation)
        else:
            need(len(stages) == 82 and [s.get('stage') for s in stages] == list(range(82))
                 and set(total) == set(COUNTERS)
                 and all(set(s) == set(COUNTERS) | {'stage'} for s in stages), '统计字段/阶段不符')
            checked = make_profile([total] + [{k: s[k] for k in COUNTERS} for s in stages], frame, generation)
        graph = checked['graph_elapsed_ms']
        need(profile.get('graph_elapsed_cycles') == checked['graph_elapsed_cycles']
             and type(profile.get('graph_elapsed_ms')) in (int, float)
             and isfinite(profile['graph_elapsed_ms']) and abs(profile['graph_elapsed_ms'] - graph) < 1e-8
             and profile.get('transport', {}).get('successful_operations') == (3 if summary else 249), '统计耗时/页数不符')
    else:
        need(not require_profile, '缺少完整统计；不得用 E2E 或 0 代替')
    info = dict(source_kind='saved_FPGA_capture', display_mode='offline_replay',
                frame_id=frame, generation=generation, model_image_id=IMAGE_ID,
                input_sha256=sha256(rgb).hexdigest(), mask_sha256=sha256(mask).hexdigest(),
                official_fixed_sample=fixed, graph_elapsed_ms=graph, image_E2E_ms=e2e,
                pure_FPGA_inference_ms=None, hardware_identity_verified=False, fps_5_accepted=False,
                preprocessing='exact_RGB888_no_resize_no_CLAHE_no_host_normalization',
                probability_map=None, confidence=None, Dice=None)
    info.update(telemetry_mode=profile.get('telemetry_mode', 'full') if profile and profile.get('complete') else 'unavailable',
                full_stages_available=bool(profile and profile.get('complete') and profile.get('schema') == 'FCP1_PROFILE_V1'),
                stage_data_status='逐层统计可用' if profile and profile.get('complete') and profile.get('schema') == 'FCP1_PROFILE_V1' else '逐层统计不可用')
    context = report.get('input_context')
    if context is not None:
        from ..formal_input import validate_source
        validate_source(context, rgb, source_bytes)
        info['input_context'] = context
        info['preprocessing'] = context['preprocessing']['version']
    return CaptureResult(rgb, mask, report, profile, info, source_bytes)


def load_capture(folder, manifest_path, require_profile=True):
    folder = Path(folder)
    need(not (folder / 'INCOMPLETE.txt').exists(), '记录仍标记为未完成')
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    need(manifest['image_id'] == IMAGE_ID, '不支持的模型 image_id')
    report = read('report.json')
    endpoints = None
    if report.get('origin') == 'protocol_simulator':
        endpoints = report.get('board'), report.get('pc')
        try:
            need(all(len(endpoint) == 2 and ipaddress.ip_address(endpoint[0]).is_loopback
                     and type(endpoint[1]) is int and 1 <= endpoint[1] <= 65535
                     for endpoint in endpoints), '模拟记录必须使用本机回环端点')
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError('模拟记录必须使用本机回环端点') from exc
    return validate_capture((folder / 'input.rgb').read_bytes(), (folder / 'mask.raw').read_bytes(),
                            report, read('profile.json') if (folder / 'profile.json').exists() else None,
                            manifest, require_profile=require_profile, endpoints=endpoints,
                            source_bytes=(folder / 'source_image.bin').read_bytes() if report.get('input_context') is not None else None)


def png_bytes(raw, channels):
    need(channels in (1, 3) and len(raw) == 256 * 256 * channels, 'PNG 尺寸无效')
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    scan = b''.join(b'\0' + raw[y*256*channels:(y+1)*256*channels] for y in range(256))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 256, 256, 8,
            0 if channels == 1 else 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(scan)) + chunk(b'IEND', b''))
