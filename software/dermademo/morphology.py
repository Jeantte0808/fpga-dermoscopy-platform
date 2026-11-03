"""Adapted member2 R1 morphology/quality; actual model grid only, no transport.

The cross-environment mirror Dice is deliberately unavailable. Input bytes and
member2 definitions stay unchanged. Source: integrations/pc_algorithm_r1.
"""
import hashlib
import json
import math
import cv2
import numpy as np
from .imaging import max_feret_diameter
from .resources import resource_path
MORPH_VERSION='MODEL256_MORPH_V1_R1_NO_MIRROR'

def load_quality_config():
    return json.loads(resource_path('quality_rules').read_text('utf-8'))

def validate_rgb(rgb):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.shape != (256, 256, 3):
        raise ValueError('RGB must be uint8 HWC (256,256,3); no implicit conversion')

def validate_mask(mask):
    if not isinstance(mask, np.ndarray) or mask.dtype != np.uint8 or mask.shape != (256, 256):
        raise ValueError('Mask must be uint8 (256,256)')
    if not np.all((mask == 0) | (mask == 255)):
        raise ValueError('Mask must contain only 0 and 255; no implicit thresholding')

def unavailable(reason):
    return dict(valid=False, value=None, reason=reason)

def _axes(mask):
    y, x = np.nonzero(mask)
    if not len(x):
        return dict(valid=False, reason='empty_mask', center_xy=None, direction_reliable=False)
    xy = np.column_stack([x, y]).astype(np.float64)
    center = xy.mean(axis=0)
    centered = xy - center
    covariance = centered.T @ centered / len(xy)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    values = np.maximum(eigenvalues[::-1], 0.0)
    u = eigenvectors[:, -1].copy()
    if u[0] < 0 or (abs(u[0]) < 1e-12 and u[1] < 0):
        u = -u
    v = np.array([-u[1], u[0]])
    lengths = 4.0 * np.sqrt(values)
    ratio = float(lengths[0] / lengths[1]) if lengths[1] > 0 else None
    stable = ratio is not None and ratio >= 1.05
    valid = bool(values[1] > 1e-12)
    result = dict(valid=valid, reason=None if valid else 'degenerate_covariance',
                  definition='uniform foreground pixel-center population covariance; moment-equivalent ellipse axes = 4*sqrt(eigenvalue)',
                  center_xy=center.tolist(), major_length_px=float(lengths[0]), minor_length_px=float(lengths[1]),
                  major_direction_deg=float(math.degrees(math.atan2(u[1], u[0])) % 180),
                  major_unit_xy=u.tolist(), minor_unit_xy=v.tolist(),
                  major_endpoints_xy=[(center-u*lengths[0]/2).tolist(), (center+u*lengths[0]/2).tolist()],
                  minor_endpoints_xy=[(center-v*lengths[1]/2).tolist(), (center+v*lengths[1]/2).tolist()],
                  axis_ratio=ratio, direction_reliable=stable,
                  direction_note='Near circular axes (ratio<1.05), a line, or a single pixel have unstable/undefined orientation.' if not stable else None)
    return result

def _feret_endpoints(mask, value):
    if value is None:
        return None
    contours, _ = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(np.concatenate(contours)).reshape(-1, 2).astype(np.float64)
    squared = ((hull[:, None, :] - hull[None, :, :]) ** 2).sum(axis=2)
    a, b = np.unravel_index(int(np.argmax(squared)), squared.shape)
    if not math.isclose(float(np.sqrt(squared[a, b])), value, rel_tol=1e-10, abs_tol=1e-10):
        raise RuntimeError('Feret overlay endpoints disagree with unchanged reference value')
    return [hull[a].tolist(), hull[b].tolist()]

def measure(rgb, mask, *, mask_source):
    validate_rgb(rgb)
    validate_mask(mask)
    if not isinstance(mask_source, str) or not mask_source.strip():
        raise ValueError('Caller must specify mask_source (raw_fpga, int8_reference, manual_revision:id...)')
    area = int(np.count_nonzero(mask))
    contours, _ = cv2.findContours(mask.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = float(sum(cv2.arcLength(c, True) for c in contours))
    circularity = (dict(valid=True, value=4.0*math.pi*area/(perimeter*perimeter), reason=None)
                   if area and perimeter > 0 else unavailable('empty_mask' if not area else 'zero_perimeter'))
    axes = _axes(mask)
    if area:
        pixels = rgb[mask == 255].astype(np.float64)
        bins = list(range(0, 257, 16))
        colors = dict(valid=True, count=area, space='uint8 RGB, 0..255, actual model input; no enhancement', bins=bins,
                      channels={c: dict(histogram_counts=np.histogram(pixels[:, i], bins=bins)[0].tolist(),
                                      histogram_fraction=(np.histogram(pixels[:, i], bins=bins)[0]/area).tolist(),
                                      mean=float(pixels[:, i].mean()), std=float(pixels[:, i].std()),
                                      min=int(pixels[:, i].min()), max=int(pixels[:, i].max()))
                                for i, c in enumerate(('R', 'G', 'B'))})
    else:
        colors = dict(valid=False, reason='empty_mask', count=0, channels=None)
    feret = max_feret_diameter(mask)
    return dict(algorithm_version=MORPH_VERSION, grid=[256, 256], mask_source=mask_source,
                mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(),
                rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(), valid=area>0,
                reason=None if area else 'empty_mask',
                coordinates='pixel centers: (x,y), origin top-left (0,0), x right, y down; overlay endpoints may lie outside grid',
                units=dict(area='model256 px^2', length='model256 px', angles='degrees clockwise modulo 180'),
                area_px2=area, max_feret_px=feret, max_feret_endpoints_xy=_feret_endpoints(mask, feret),
                perimeter=dict(valid=area>0, value_px=perimeter if area else None,
                               reason=None if area else 'empty_mask',
                               definition='closed arcLength, RETR_LIST/CHAIN_APPROX_NONE; all outer and hole contours included'),
                circularity=circularity, axes=axes, symmetry=dict(valid=False, reason='cross_environment_golden_pending', major_axis_dice=None, minor_axis_dice=None), colors=colors,
                postprocessing='none; no cleanup, filling, largest-component selection or thresholding',
                limitations=['Model-grid morphology only; nonuniform whole-image/ROI stretching distorts shape.',
                             'Discrete perimeter and continuous circularity have raster error; circularity is not clipped to 1.',
                             'Moment axes are not maximum Feret; disconnected regions and holes participate in the same mask.',
                             'Shape, symmetry and color are engineering displays, not disease classification or risk.'])

def quality_scores(rgb, config=None):
    validate_rgb(rgb)
    config = load_quality_config() if config is None else config
    pixel_rules = config['pixel_definitions']
    if pixel_rules['dark']['op'] != 'lt' or pixel_rules['overexposed']['op'] != 'ge':
        raise ValueError('Unsupported pixel definition comparisons')
    gray = (0.299*rgb[:, :, 0].astype(np.float64) + 0.587*rgb[:, :, 1].astype(np.float64)
            + 0.114*rgb[:, :, 2].astype(np.float64))
    lap = cv2.Laplacian(gray, cv2.CV_64F, ksize=1, borderType=cv2.BORDER_REFLECT_101)
    return dict(laplacian_variance=float(lap.var()), mean_luma=float(gray.mean()),
                dark_fraction=float(np.mean(gray < pixel_rules['dark']['threshold'])),
                overexposed_fraction=float(np.mean(gray >= pixel_rules['overexposed']['threshold'])))

def evaluate_quality_scores(scores, config):
    """Separate rule evaluation permits exact comparison-boundary tests."""
    if config.get('version') != 'MODEL256_QUALITY_V1_R1':
        raise ValueError('Unsupported quality configuration version')
    if not all(k in scores and np.isfinite(scores[k]) for k in
               ('laplacian_variance', 'mean_luma', 'dark_fraction', 'overexposed_fraction')):
        raise ValueError('Quality scores missing or nonfinite')
    operations = {'lt': lambda x,y:x<y, 'le': lambda x,y:x<=y,
                  'gt': lambda x,y:x>y, 'ge': lambda x,y:x>=y}
    results = []
    for rule in config['rules']:
        conditions = [dict(c, actual=scores[c['metric']], matched=bool(operations[c['op']](scores[c['metric']], c['threshold'])))
                      for c in rule['conditions']]
        flags = [c['matched'] for c in conditions]
        if rule['combine'] not in ('all', 'any'):
            raise ValueError('Invalid rule combination')
        trigger = all(flags) if rule['combine'] == 'all' else any(flags)
        results.append(dict(id=rule['id'], triggered=trigger, message=rule['message'] if trigger else None,
                            conditions=conditions, combine=rule['combine']))
    return dict(version=config['version'], input='actual uint8 RGB888 256x256, before LUT/ImageNet normalization',
                scores=scores, reminders=results, triggered=[r['id'] for r in results if r['triggered']],
                action='inform only; do not modify input, block inference, change Mask or send hardware commands',
                medical_quality_assessment=False)

def quality(rgb, config=None):
    if config is None:
        config = load_quality_config()
    return evaluate_quality_scores(quality_scores(rgb, config), config)
