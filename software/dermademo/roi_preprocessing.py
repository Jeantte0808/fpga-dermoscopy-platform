"""Member2 ROI R1 adapter; qualification uses this PC's pinned codec build."""
from functools import lru_cache
from dataclasses import replace
from hashlib import sha256
import json
from time import perf_counter

import cv2
import numpy as np

from .formal_preprocessing import decoded_bgr_to_rgb888, VERSION as FULL_VERSION
from .resources import resource_path

VERSION = 'ROI_RGB888_V1_20261026'


def validate_model_rect(rect, width, height):
    from .roi import validate_rect
    rect = validate_rect(rect, width, height)
    if rect[2]-rect[0] < 16 or rect[3]-rect[1] < 16:
        raise ValueError('ROI宽和高各至少16个原图像素（软件操作限制，不代表医学可靠性）')
    return rect


class ConfirmedROIProcessor:
    def prepare(self, source, rect):
        clock = perf_counter()
        from .roi import crop_rgb, create_roi_input
        qualification = verify_roi_preprocessing()
        rect = validate_model_rect(rect, source.rgb.shape[1], source.rgb.shape[0])
        rgb, meta = decoded_bgr_to_rgb888(cv2.cvtColor(crop_rgb(source, rect), cv2.COLOR_RGB2BGR))
        prepared = create_roi_input(source, rect, rgb, parameters={
            **meta, 'base_full_image_version': FULL_VERSION, 'resize': 'INTER_AREA',
            'minimum_side_original_pixels': 16, 'jpeg_encode_count_after_crop': 1,
            'coordinate_space': 'EXIF-oriented original image', 'interval': 'integer half-open [x0,y0,x1,y1)'},
            confirmation={'status': 'confirmed', 'test_only': False, 'rule_id': VERSION,
                          'golden_manifest_sha256': qualification['manifest_sha256']})
        return replace(prepared, cpu_preprocessing_ms=(perf_counter()-clock)*1000)


@lru_cache(maxsize=1)
def verify_roi_preprocessing():
    """Fail closed against delivered bytes. No network or model inference."""
    from .roi import decode_source, crop_rgb, map_mask
    root = resource_path('roi_root')
    manifest_bytes = (root/'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    # Verify all bundled sample/spec bytes used for qualification.
    for item in manifest['files']:
        path = root/item['path']
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('ROI交付资源摘要不符：'+item['path'])
    cases = []
    for row in json.loads((root/'samples.json').read_text('utf-8')):
        folder = root/row['directory']
        source = decode_source(folder/row['input_file'])
        rect = validate_model_rect(row['roi'], source.rgb.shape[1], source.rgb.shape[0])
        rgb, meta = decoded_bgr_to_rgb888(cv2.cvtColor(crop_rgb(source, rect), cv2.COLOR_RGB2BGR))
        if rgb.tobytes() != (folder/'input.rgb888.bin').read_bytes():
            raise ValueError('ROI RGB金标准不一致：'+row['case'])
        expected_meta = json.loads((folder/'conversion.json').read_text('utf-8'))
        if meta['intermediate_jpeg_sha256'] != expected_meta['intermediate_jpeg_sha256']:
            raise ValueError('ROI中间JPEG金标准不一致')
        mask = np.frombuffer((folder/'mask.bin').read_bytes(),np.uint8).reshape(256,256)
        mapped = map_mask(source,rect,mask)
        x0,y0,x1,y1 = rect
        oracle = mask[(np.arange(y1-y0)*256//(y1-y0))[:,None],(np.arange(x1-x0)*256//(x1-x0))[None,:]]
        if not np.array_equal(mapped['mapped_mask.png'][y0:y1,x0:x1], oracle):
            raise ValueError('ROI nearest映射不一致')
        cases.append({'case':row['case'],'roi':list(rect),'different_bytes':0,'mapping_verified':True})
    if len(cases) != 3:
        raise ValueError('ROI金标准必须包含三组')
    return {'rule_id': VERSION, 'manifest_sha256':sha256(manifest_bytes).hexdigest(),
            'cases':cases, 'physical_FPGA_tested':False, 'model_inference_performed':False}
