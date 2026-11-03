"""PC reference for a proposed hardware statistic; never sends a new command."""
from hashlib import sha256
import numpy as np

VERSION = 'mask_statistics_pc_v1'


def measure(raw):
    if type(raw) is not bytes or len(raw) != 65536 or not set(raw) <= {0, 255}:
        raise ValueError('统计只接受原始256×256 uint8、0/255 Mask')
    mask = np.frombuffer(raw, np.uint8).reshape(256, 256)
    y, x = np.nonzero(mask)
    edges = [bool(mask[0].any()), bool(mask[:, -1].any()),
             bool(mask[-1].any()), bool(mask[:, 0].any())]
    return dict(version=VERSION, mask_sha256=sha256(raw).hexdigest(),
                foreground_count=int(x.size), bbox_valid=bool(x.size),
                bbox=[int(x.min()), int(y.min()), int(x.max())+1, int(y.max())+1] if x.size else None,
                edge_flags=sum(int(value) << i for i, value in enumerate(edges)),
                grid=[256, 256], units='model_grid_px_px2',
                hardware_status='unavailable_protocol_not_frozen')


def compare(raw, hardware, *, frame_id, generation):
    """Offline candidate validation only. A caller must provide actual identity."""
    expected = measure(raw)
    if hardware is None:
        return dict(status='unavailable', pc=expected, hardware=None, differences=None)
    if (type(hardware) is not dict or hardware.get('frame_id') != frame_id
            or hardware.get('generation') != generation
            or type(hardware.get('frame_id')) is not int or type(hardware.get('generation')) is not int
            or hardware.get('valid') is not True or hardware.get('mask_sha256') != expected['mask_sha256']):
        raise ValueError('板内统计帧/代际/Mask身份或有效状态不符')
    fields = ('foreground_count', 'bbox_valid', 'bbox', 'edge_flags')
    import json
    differences = [key for key in fields if json.dumps(hardware.get(key), sort_keys=True) != json.dumps(expected[key], sort_keys=True)]
    return dict(status='mismatch' if differences else 'exact', pc=expected,
                hardware=dict(hardware), differences=differences,
                note='离线候选字段核对；不是现有FCP1读取接口')
