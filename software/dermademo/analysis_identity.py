"""Identity of an immutable raw result, shared by derived PC analyses."""
from hashlib import sha256
from copy import deepcopy
from dataclasses import replace

import numpy as np


def frozen_pair(pair):
    """Copy mutable pixels/metadata, reuse frozen Qt-independent config contracts."""
    request, result = pair
    result.validate(request)
    def pixels(array):
        owned = array.copy()
        owned.setflags(write=False)
        return owned
    return (replace(request, image_bgr=pixels(request.image_bgr)),
            replace(result, enhanced_bgr=pixels(result.enhanced_bgr), mask=pixels(result.mask),
                    overlay_bgr=pixels(result.overlay_bgr), timings_ms=dict(result.timings_ms),
                    metadata=deepcopy(result.metadata)))


def result_identity(pair):
    request, result = pair
    result.validate(request)
    meta = result.metadata
    if result.capabilities.key != 'fpga_formal_v5':
        raise ValueError('请选择正式FPGA结果或对应历史记录')
    rgb = np.ascontiguousarray(request.image_bgr[:, :, ::-1]).tobytes()
    mask_hash = sha256(result.mask.tobytes()).hexdigest()
    if sha256(rgb).hexdigest() != meta['input_sha256'] or mask_hash != meta['mask_sha256']:
        raise ValueError('当前图像或Mask与原始结果摘要不一致')
    context = meta.get('input_context') or {}
    roi = context.get('roi')
    return dict(analysis_id=meta.get('analysis_id', request.task_id),
                frame_id=meta['frame_id'], generation=meta['generation'],
                source_sha256=context.get('source_sha256'), input_rgb_sha256=meta['input_sha256'],
                preprocess_version=context.get('preprocessing', {}).get('version', meta.get('preprocessing')),
                model_version=meta['model_version'], roi=roi.get('bounds') if roi else None,
                raw_mask_sha256=mask_hash, mask_variant='raw_fpga', revision_id=None,
                source_kind=meta['source_kind'], display_mode=meta['display_mode'])
