"""ROI geometry, source snapshots and presentation; no guessed model preprocessing."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import cv2
import numpy as np

from .formal_preprocessing import check_environment, inspect_image, environment
from .formal_input import PreparedImage
from .imaging import make_overlay


@dataclass(frozen=True)
class ROISource:
    path: Path
    data: bytes
    rgb: np.ndarray
    info: dict


def decode_source(path, data=None):
    check_environment()
    path = Path(path)
    data = path.read_bytes() if data is None else data
    info = inspect_image(data)
    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError('原图解码失败')
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    rgb.setflags(write=False)
    info.update(oriented_width=rgb.shape[1], oriented_height=rgb.shape[0])
    return ROISource(path, data, rgb, info)


def validate_rect(rect, width, height):
    if (not isinstance(rect, (tuple, list)) or len(rect) != 4 or any(type(v) is not int for v in rect)):
        raise ValueError('ROI必须是四个整数坐标')
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError('ROI必须位于方向校正后的原图内，且宽高大于0')
    return tuple(rect)


def crop_rgb(source, rect):
    x0, y0, x1, y1 = validate_rect(rect, source.rgb.shape[1], source.rgb.shape[0])
    return np.ascontiguousarray(source.rgb[y0:y1, x0:x1])


class ROIProcessor(Protocol):
    """Member2-confirmed implementation plugs in here after its goldens pass."""
    def prepare(self, source: ROISource, rect: tuple[int, int, int, int]) -> PreparedImage: ...


def get_roi_processor():
    from .roi_preprocessing import ConfirmedROIProcessor
    return ConfirmedROIProcessor()


def create_roi_input(source, rect, rgb, *, parameters, confirmation):
    """Bind actual adapter output to its source/crop. Does NOT perform conversion."""
    rect = validate_rect(rect, source.rgb.shape[1], source.rgb.shape[0])
    raw = rgb.tobytes(order='C') if isinstance(rgb, np.ndarray) else rgb
    if isinstance(rgb, np.ndarray) and (rgb.dtype != np.uint8 or rgb.shape != (256,256,3)):
        raise ValueError('ROI适配器必须返回uint8的256×256×3 RGB')
    if type(raw) is not bytes or len(raw) != 196608:
        raise ValueError('ROI适配器必须返回196608字节RGB888')
    cropped = crop_rgb(source, rect)
    prep = {**source.info, 'environment': environment(), 'version': confirmation.get('rule_id'),
            'source_filename': source.path.name, 'input_sha256': sha256(source.data).hexdigest(),
            'input_kind': 'roi', 'crop_rgb_sha256': sha256(cropped.tobytes()).hexdigest(),
            'crop_shape': list(cropped.shape), 'parameters': parameters,
            'output_shape': [256, 256, 3], 'output_bytes': len(raw), 'rgb888_sha256': sha256(raw).hexdigest(),
            'host_normalization': False, 'augmentation': False}
    context = {'schema': 'formal_roi_v1', 'source_filename': source.path.name,
               'source_sha256': sha256(source.data).hexdigest(), 'source_bytes': len(source.data),
               'preprocessing': prep, 'measurement_space': 'model_input_256_grid',
               'attribution': {'kind': 'user_selected', 'dataset': None, 'note': '用户自选图片，数据来源未核实'},
               'roi': {'bounds': list(rect), 'source_size': [source.rgb.shape[1], source.rgb.shape[0]],
                       'coordinate_space': 'EXIF_normalized_original', 'bounds_convention': 'half_open'},
               'rule_confirmation': confirmation}
    validate_roi_context(context, raw, source.data)
    return PreparedImage(source.path, source.data, raw, context)


def validate_roi_context(context, rgb, data):
    try:
        if (context['schema'] != 'formal_roi_v1' or type(data) is not bytes
                or context['source_bytes'] != len(data) or context['source_sha256'] != sha256(data).hexdigest()
                or type(rgb) is not bytes or len(rgb) != 196608
                or context['measurement_space'] != 'model_input_256_grid'):
            raise ValueError('ROI原图/输入摘要不一致')
        name = context['source_filename']
        if not isinstance(name, str) or not name or name in ('.', '..') or any(c in name for c in '/\\\x00\r\n'):
            raise ValueError('ROI原文件名无效')
        source = decode_source(name, data)
        roi, prep, confirm = context['roi'], context['preprocessing'], context['rule_confirmation']
        if not isinstance(prep.get('parameters'), dict) or len(json.dumps(context, allow_nan=False).encode('utf-8')) > 65536:
            raise ValueError('ROI预处理参数必须是有限、可保存的JSON对象')
        rect = validate_rect(roi['bounds'], source.rgb.shape[1], source.rgb.shape[0])
        if (roi['source_size'] != [source.rgb.shape[1], source.rgb.shape[0]]
                or roi['coordinate_space'] != 'EXIF_normalized_original' or roi['bounds_convention'] != 'half_open'
                or any(prep[k] != source.info[k] for k in ('format', 'stored_width', 'stored_height',
                       'oriented_width', 'oriented_height', 'exif_orientation'))
                or prep['source_filename'] != name or prep['input_sha256'] != context['source_sha256']
                or prep['input_kind'] != 'roi' or prep['output_shape'] != [256, 256, 3]
                or prep['output_bytes'] != 196608 or prep['rgb888_sha256'] != sha256(rgb).hexdigest()
                or prep['host_normalization'] is not False or prep['augmentation'] is not False):
            raise ValueError('ROI坐标/预处理元数据不一致')
        cropped = crop_rgb(source, rect)
        if prep['crop_shape'] != list(cropped.shape) or prep['crop_rgb_sha256'] != sha256(cropped.tobytes()).hexdigest():
            raise ValueError('ROI裁剪区域摘要不一致')
        if (confirm['status'] not in ('confirmed', 'test_fixture') or type(confirm['test_only']) is not bool
                or (confirm['status'] == 'test_fixture') != confirm['test_only']
                or not isinstance(confirm['rule_id'], str) or not confirm['rule_id']
                or prep['version'] != confirm['rule_id']
                or len(confirm['golden_manifest_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in confirm['golden_manifest_sha256'])):
            raise ValueError('ROI规则缺少确认/金标准身份')
        return source, rect
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('ROI输入元数据不完整') from exc


def map_mask(source, rect, model_mask):
    """Nearest-neighbour presentation only. Outside coverage=0 means UNPROCESSED."""
    rect = validate_rect(rect, source.rgb.shape[1], source.rgb.shape[0])
    if model_mask.shape != (256, 256) or model_mask.dtype != np.uint8 or not np.isin(model_mask, (0, 255)).all():
        raise ValueError('ROI模型Mask必须是256×256的0/255结果')
    x0, y0, x1, y1 = rect
    mapped = np.zeros(source.rgb.shape[:2], np.uint8)
    coverage = np.zeros_like(mapped)
    mapped[y0:y1, x0:x1] = cv2.resize(model_mask, (x1-x0, y1-y0), interpolation=cv2.INTER_NEAREST)
    coverage[y0:y1, x0:x1] = 255
    bgr = cv2.cvtColor(source.rgb, cv2.COLOR_RGB2BGR)
    overlay = make_overlay(bgr, mapped)
    overlay[coverage == 0] = (bgr[coverage == 0].astype(np.float32) * .45).astype(np.uint8)
    return {'source_oriented.png': bgr, 'roi_crop.png': cv2.cvtColor(crop_rgb(source, rect), cv2.COLOR_RGB2BGR),
            'mapped_mask.png': mapped, 'processed_region.png': coverage, 'mapped_overlay.png': overlay}


def roi_artifacts(request, result):
    context = result.metadata.get('input_context', {})
    raw = np.ascontiguousarray(request.image_bgr[:, :, ::-1]).tobytes()
    source, rect = validate_roi_context(context, raw, request.source_bytes)
    return map_mask(source, rect, result.mask)


def save_draft(parent, source, rect):
    """A selection draft is never a completed inference record."""
    if rect is not None:
        rect = validate_rect(rect, source.rgb.shape[1], source.rgb.shape[0])
    target = Path(parent) / ('roi_draft_' + uuid4().hex[:12])
    target.mkdir(exist_ok=False)
    from .device.formal_session import write_bytes, write_json
    from .storage import write_png, file_sha256
    write_bytes(target / 'INCOMPLETE.txt', b'ROI draft save in progress\n')
    write_bytes(target / 'source_image.bin', source.data)
    write_png(target / 'source_oriented.png', cv2.cvtColor(source.rgb, cv2.COLOR_RGB2BGR))
    if rect is not None:
        write_png(target / 'roi_crop.png', cv2.cvtColor(crop_rgb(source, rect), cv2.COLOR_RGB2BGR))
    files = {p.name: file_sha256(p) for p in target.iterdir() if p.name != 'INCOMPLETE.txt'}
    write_json(target / 'roi_draft.json', {'schema': 'roi_selection_draft_v1', 'source_filename': source.path.name,
               'roi': list(rect) if rect else None, 'coordinate_space': 'EXIF_normalized_original',
               'bounds_convention': 'half_open', 'rule_status': 'selection_only_not_prepared',
               'no_inference_performed': True, 'files': files})
    (target / 'INCOMPLETE.txt').unlink()
    return target


def load_draft(folder):
    folder = Path(folder)
    if (folder / 'INCOMPLETE.txt').exists():
        raise ValueError('ROI草稿保存未完成')
    doc = json.loads((folder / 'roi_draft.json').read_text('utf-8'))
    names = {'source_image.bin', 'source_oriented.png'} | ({'roi_crop.png'} if doc['roi'] is not None else set())
    if (doc['schema'] != 'roi_selection_draft_v1' or doc['no_inference_performed'] is not True
            or doc['coordinate_space'] != 'EXIF_normalized_original' or doc['bounds_convention'] != 'half_open'
            or set(doc['files']) != names):
        raise ValueError('ROI草稿结构无效')
    for name in names:
        if sha256((folder / name).read_bytes()).hexdigest() != doc['files'][name]:
            raise ValueError('ROI草稿文件校验不符')
    source = decode_source(doc['source_filename'], (folder / 'source_image.bin').read_bytes())
    rect = validate_rect(doc['roi'], source.rgb.shape[1], source.rgb.shape[0]) if doc['roi'] is not None else None
    from PIL import Image
    with Image.open(folder / 'source_oriented.png') as image:
        if not np.array_equal(np.array(image), source.rgb):
            raise ValueError('ROI草稿原图不一致')
    if rect is not None:
        with Image.open(folder / 'roi_crop.png') as image:
            if not np.array_equal(np.array(image), crop_rgb(source, rect)):
                raise ValueError('ROI草稿裁剪不一致')
    return source, rect
