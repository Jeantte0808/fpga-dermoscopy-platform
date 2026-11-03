"""Read-only teaching comparisons. References never become device results."""
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from statistics import mean
from zipfile import ZipFile

import cv2
import numpy as np

from .analysis_identity import result_identity
from .formal_input import MODEL_VERSION
from .resources import resource_path


def binary_mask(raw):
    if len(raw) != 65536 or not set(raw) <= {0, 255}:
        raise ValueError('对照Mask必须为256×256 uint8二值0/255，不能自动缩放或二值化')
    return np.frombuffer(raw, np.uint8).reshape(256, 256).copy()


def mask_comparison(actual, reference, *, kind):
    actual = binary_mask(actual.tobytes())
    reference = binary_mask(reference.tobytes())
    a, b = actual > 0, reference > 0
    different = a != b
    # BGR: white=intersection; red=prediction-only; blue=reference-only.
    overlay = np.zeros((256, 256, 3), np.uint8)
    overlay[a & b] = (230, 230, 230)
    overlay[a & ~b] = (50, 70, 230)
    overlay[~a & b] = (230, 140, 50)
    out = dict(kind=kind, different_pixels=int(different.sum()),
               byte_exact=bool(not different.any()), difference_bgr=overlay)
    if kind == 'annotation':
        intersection, union, total = int((a & b).sum()), int((a | b).sum()), int(a.sum() + b.sum())
        out.update(dice=2 * intersection / total if total else 1.0,
                   iou=intersection / union if union else 1.0,
                   formula_version='model256_binary_v1_both_empty_score_1',
                   prediction_only=int((a & ~b).sum()), annotation_only=int((~a & b).sum()))
    elif kind != 'int8':
        raise ValueError('不支持的对照类型')
    return out


def _legacy_bundled_reference(pair):
    """Match actual RGB AND source/ROI contract against member2's byte goldens."""
    identity = result_identity(pair)
    if identity['model_version'] != MODEL_VERSION:
        return None
    context = pair[1].metadata.get('input_context') or {}
    candidates = [('full_image_root', 'full'), ('roi_root', 'roi')]
    for key, kind in candidates:
        if (kind == 'roi') != (context.get('schema') == 'formal_roi_v1'):
            continue
        root = resource_path(key)
        for row in json.loads((root / 'samples.json').read_text('utf-8')):
            folder = root / row['directory']
            rgb = (folder / 'input.rgb888.bin').read_bytes()
            if sha256(rgb).hexdigest() != identity['input_rgb_sha256']:
                continue
            if context:
                expected_source = row['source_sha256'] if kind == 'roi' else row['conversion']['input_sha256']
                if expected_source != identity['source_sha256']:
                    continue
                if kind == 'roi':
                    if row['roi'] != identity['roi']:
                        continue
                    metadata = json.loads((folder / 'conversion.json').read_text('utf-8'))
                    version = metadata['version']
                else:
                    version = row['conversion']['version']
                if version != identity['preprocess_version']:
                    continue
            elif not pair[1].metadata.get('official_fixed_sample'):
                # Legacy raw captures lack preprocessing/ROI identity; do not guess.
                continue
            raw = (folder / 'mask.bin').read_bytes()
            files = json.loads((root / 'manifest.json').read_text('utf-8'))['files']
            relative = (folder / 'mask.bin').relative_to(root).as_posix()
            declared = next((entry['sha256'] for entry in files if entry['path'] == relative), None)
            if not declared or sha256(raw).hexdigest() != declared:
                raise ValueError('随包整数参考摘要损坏')
            return dict(mask=binary_mask(raw), kind='int8', identity=identity,
                        note=f'成员2既有整数参考 · {key}/{row["directory"]}；非新测试集成绩')
    # Fixed raw device sample has a model-manifest-certified expected digest.
    if not context and pair[1].metadata.get('official_fixed_sample'):
        from .device.formal_protocol import assets
        manifest = assets()[0]
        if identity['input_rgb_sha256'] == manifest['sample_RGB_sha256']:
            for folder in sorted((resource_path('formal_root') / 'captures').iterdir()):
                raw = (folder / 'mask.raw').read_bytes()
                if sha256(raw).hexdigest() == manifest['sample_Mask_sha256']:
                    return dict(mask=binary_mask(raw), kind='int8', identity=identity,
                                note='随包固定样例整数金标准；非本次推理输出')
    return None


def bundled_reference(pair):
    from .reference_library import references
    return references(pair).get('int8')


def load_reference(path, pair):
    """Explicit declared reference package, matching the current exact grid.

    This validates binding, not who authored the declaration. The UI says imported.
    """
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError('对照声明过大')
    data = json.loads(path.read_text('utf-8'))
    if data.get('schema') != 'alienhome.reference.v1' or data.get('kind') not in ('int8', 'annotation'):
        raise ValueError('不支持的对照声明')
    identity = result_identity(pair)
    for key in ('input_rgb_sha256', 'source_sha256', 'model_version', 'preprocess_version', 'roi'):
        if data.get('identity', {}).get(key) != identity[key]:
            raise ValueError('对照与当前输入身份不匹配：' + key)
    note = data.get('source_note')
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValueError('需要有效的参考来源说明')
    if data['kind'] == 'annotation':
        rule = data.get('mapping_rule')
        if not isinstance(rule, dict) or rule.get('confirmed_by') != 'member2' or not rule.get('version'):
            raise ValueError('人工标注需要成员2确认的方向/ROI/256网格映射声明')
    relative = data.get('mask_file')
    if not isinstance(relative, str) or not relative:
        raise ValueError('缺少Mask文件')
    mask_path = (path.parent / relative).resolve()
    if not mask_path.is_relative_to(path.parent.resolve()) or mask_path.stat().st_size > 262144:
        raise ValueError('对照Mask路径或大小无效')
    content = mask_path.read_bytes()
    if sha256(content).hexdigest() != data.get('mask_sha256'):
        raise ValueError('对照Mask文件摘要不符')
    if mask_path.suffix.lower() == '.png':
        mask = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
        if mask is None or mask.dtype != np.uint8 or mask.shape != (256, 256):
            raise ValueError('对照PNG必须为原始单通道256×256 uint8')
        raw = mask.tobytes()
    else:
        raw = content
    return dict(mask=binary_mask(raw), kind=data['kind'], identity=identity,
                note='人工导入声明（未自动认证作者） · ' + note)


TIMING_ROWS = [('板内图执行（LUT/CNN/DDR等待）', 'graph'), ('图像E2E（至合法FINISH）', 'e2e'),
               ('模型上传/加载', 'model_upload_ms'), ('RGB上传', 'rgb_upload'),
               ('首Mask等待', 'first_mask_wait'), ('其余Mask获取', 'remaining_mask'),
               ('FINISH', 'finish'), ('统计查询', 'statistics'), ('本地保存', 'local_save'),
               ('显示准备', 'display_prepare'), ('操作至界面提交（非屏幕绘制）', 'visible')]


def performance_snapshot(pair):
    identity = result_identity(pair)
    result = pair[1]
    meta = result.metadata
    report, profile = meta.get('wire_report') or {}, meta.get('wire_profile') or {}
    values = dict(meta.get('stage_timings_ms') or {})
    values.update(graph=result.timings_ms.get('fpga_graph'), e2e=result.timings_ms.get('image_transport_e2e'),
                  model_upload_ms=report.get('model_upload_ms'), visible=meta.get('user_visible_wait_ms'))
    values = {key: float(v) if type(v) in (int, float) and isfinite(v) and v >= 0 else None for key, v in values.items()}
    return dict(identity=identity, values=values, telemetry_mode=meta.get('telemetry_mode', 'unavailable'),
                stages=profile.get('stages', []) if meta.get('full_stages_available') else [],
                hardware_declaration=meta.get('hardware_declaration'),
                origin='本机协议模拟' if identity['source_kind'] == 'protocol_simulator' else
                       '历史实板记录回放' if identity['display_mode'] == 'offline_replay' else '本次实时结果',
                diagnostics={key: report.get(key) for key in ('BUSY', 'retries', 'invalid_replies', 'ignored_packets', 'error')})


def historical_p7_comparison():
    """Recompute the supplied five-vs-five evidence, never relabel raw reports."""
    archive = resource_path('p7_evidence')
    expected = '4ca71782fc123fb4d31c897d40c3e6992a4d06bdb423e1b6e6b6e87acc3c7fba'
    if sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('P7历史证据包摘要不符')
    groups = []
    from .device.formal_capture import validate_capture
    from .device.formal_protocol import assets
    with ZipFile(archive) as z:
        folders = [f'baseline/{i}' for i in range(1, 6)]
        folders += [name.rsplit('/', 1)[0] for name in sorted(z.namelist())
                    if name.startswith('hardware_20261029/reuse5/') and name.endswith('/report.json')]
        if len(folders) != 10:
            raise ValueError('历史对照必须为各5帧')
        rgb_hashes = set()
        for folder in folders:
            report_raw = z.read(folder + '/report.json')
            report = json.loads(report_raw)
            profile = json.loads(z.read(folder + '/profile.json'))
            source_path = folder + '/source_image.bin'
            capture = validate_capture(z.read(folder + '/input.rgb'), z.read(folder + '/mask.raw'), report, profile,
                                       assets()[0], source_bytes=z.read(source_path) if source_path in z.namelist() else None)
            rgb_hashes.add(capture.info['input_sha256'])
            groups.append(dict(group='V6' if folder.startswith('baseline/') else 'P7', path=folder,
                               frame_id=report['frame_id'], input_sha256=capture.info['input_sha256'],
                               report_sha256=sha256(report_raw).hexdigest(), graph_ms=capture.info['graph_elapsed_ms'],
                               e2e_ms=capture.info['image_E2E_ms'], raw_hardware_declaration=report.get('hardware_declaration')))
    if len(rgb_hashes) != 1:
        raise ValueError('历史性能对照RGB不一致')
    values = {}
    for key in ('graph_ms', 'e2e_ms'):
        before = mean(row[key] for row in groups if row['group'] == 'V6')
        after = mean(row[key] for row in groups if row['group'] == 'P7')
        values[key] = dict(before=before, after=after, reduction_percent=(1-after/before)*100)
    return dict(values=values, frames=groups, archive_sha256=expected,
                note='历史实板证据：同v0.9.3客户端、同RGB、完整249页，各5帧；非本次测试。'
                     'P7归属依据团队交接声明；原报告旧V6静态标签未改写，程序不能自动识别位流。'
                     '板内图含LUT/CNN/DDR等待；未验收持续5FPS。')
