"""Read-only geometric reminder for an actual successful 256x256 binary prediction.

Only NumPy is required. This does not assess quality or completeness, and never
changes the prediction, ROI, thresholds, or hardware protocol.
"""
import numpy as np

MESSAGE = '可能截断病灶，请扩大范围或返回整图'
SIDES = ('top', 'bottom', 'left', 'right')


def check_edge_contact(mask):
    """Return edge_contact, touched_sides, side_foreground_counts, message.

    Input: raw successful-frame NumPy uint8 (256,256), values ONLY 0/255.
    Corners count once for each adjacent side. The fixed message is always
    returned, but the GUI should display it only when edge_contact is True.
    """
    if not isinstance(mask, np.ndarray) or np.ma.isMaskedArray(mask):
        raise TypeError('Mask must be a plain NumPy array, not a list or masked array.')
    if mask.shape != (256, 256):
        raise ValueError(f'Mask must have shape (256,256), got {mask.shape}. Do not map/resize before checking.')
    if mask.dtype != np.uint8:
        raise TypeError(f'Mask dtype must be uint8, got {mask.dtype}; no implicit conversion.')
    if np.any((mask != 0) & (mask != 255)):
        raise ValueError('Mask must contain only 0 and 255; no silent thresholding.')
    borders = (mask[0, :], mask[255, :], mask[:, 0], mask[:, 255])
    counts = {side: int(np.count_nonzero(border == 255)) for side, border in zip(SIDES, borders)}
    touched = [side for side in SIDES if counts[side] > 0]
    return dict(edge_contact=bool(touched), touched_sides=touched,
                side_foreground_counts=counts, message=MESSAGE)
