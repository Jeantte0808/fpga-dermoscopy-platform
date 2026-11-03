"""Immutable full-image input snapshots. No sockets, model loading or state writes."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from .formal_preprocessing import prepare_input
from .resources import resource_path
from .hardware import resource_summary

EDUCATION_NOTICE = '仅用于图像分析与算法研究，不作为临床诊断依据。'
MODEL_VERSION = 'FPGA_Skin_MobileNetV2_INT8_MASK_V1_03_HW_R1'
FIXED_THRESHOLD = {'domain': 'final_integer_logits', 'operator': '>=', 'q': 1,
                   'adjustable': False, 'probability_threshold': False}


def hardware_reference():
    """Read-only team declaration; never an authenticated running-bit identity."""
    return json.loads((resource_path('full_image_root') / 'hardware_display_reference.json').read_text('utf-8'))


@dataclass(frozen=True)
class PreparedImage:
    path: Path
    source_bytes: bytes
    rgb: bytes
    context: dict
    cpu_preprocessing_ms: float | None = None


def prepare_image(path, *, source_data=None):
    clock = perf_counter()
    path = Path(path)
    original = path.read_bytes() if source_data is None else source_data
    rgb, metadata = prepare_input(path, input_kind='raw', source_data=original)
    context = {'schema': 'formal_full_image_v1', 'source_filename': path.name,
               'source_sha256': sha256(original).hexdigest(), 'source_bytes': len(original),
               'preprocessing': metadata,
               'attribution': {'kind': 'user_selected', 'dataset': None,
                               'note': '用户自选图片，数据来源未核实；不自动标记为 ISIC'},
               'measurement_space': 'model_input_256_grid', 'roi': None}
    return PreparedImage(path, original, rgb.tobytes(order='C'), context, (perf_counter()-clock)*1000)


def validate_source(context, rgb, source):
    """Bind persisted source, preprocessing declaration and actual transmitted bytes."""
    if isinstance(context, dict) and context.get('schema') == 'formal_roi_v1':
        from .roi import validate_roi_context
        validate_roi_context(context, rgb, source)
        return
    try:
        name = context['source_filename']
        prep = context['preprocessing']
        if (context['schema'] != 'formal_full_image_v1' or not isinstance(name, str)
                or not name or name in ('.', '..') or any(c in name for c in '/\\\x00\r\n')
                or type(source) is not bytes or len(source) != context['source_bytes']
                or sha256(source).hexdigest() != context['source_sha256']
                or prep['input_kind'] != 'raw' or prep['input_sha256'] != context['source_sha256']
                or prep['source_filename'] != name or prep['output_bytes'] != 196608
                or prep['rgb888_sha256'] != sha256(rgb).hexdigest()
                or prep['output_shape'] != [256, 256, 3]
                or prep['host_normalization'] is not False or prep['augmentation'] is not False
                or prep['format'] not in ('JPEG', 'PNG')
                or any(type(prep[k]) is not int or prep[k] < 1 for k in
                       ('stored_width', 'stored_height', 'oriented_width', 'oriented_height'))
                or prep['oriented_width'] * prep['oriented_height'] > 24_000_000
                or prep['exif_orientation'] not in range(1, 9)
                or context['measurement_space'] != 'model_input_256_grid' or context['roi'] is not None):
            raise ValueError('原始图片、预处理元数据或实际 RGB 摘要不一致')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('整图输入元数据不完整') from exc


def verify_preprocessing():
    """Byte goldens, not model inference or physical-board acceptance."""
    root = resource_path('full_image_root')
    cases = []
    for row in json.loads((root / 'samples.json').read_text('utf-8')):
        folder = root / row['directory']
        prepared = prepare_image(folder / row['input_file'])
        if prepared.rgb != (folder / 'input.rgb888.bin').read_bytes():
            raise ValueError('预处理金标准字节不一致：' + row['input_file'])
        if prepared.context['preprocessing']['intermediate_jpeg_sha256'] != row['conversion']['intermediate_jpeg_sha256']:
            raise ValueError('中间 JPEG 金标准不一致：' + row['input_file'])
        cases.append({'file': row['input_file'], 'rgb_sha256': sha256(prepared.rgb).hexdigest(), 'different_bytes': 0})
    return {'schema': 'formal_preprocessing_checks_v1', 'cases': cases,
            'model_inference_performed': False, 'physical_FPGA_tested': False}
