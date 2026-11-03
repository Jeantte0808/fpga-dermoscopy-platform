"""Explicit laboratory transforms, never labelled as the teammate's model input."""

from dataclasses import dataclass
from hashlib import sha256

import cv2
import numpy as np

from ..imaging import MAX_IMAGE_PIXELS


@dataclass(frozen=True)
class PreparedInput:
    rgb: bytes
    original_width: int
    original_height: int
    resized_width: int
    resized_height: int
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    geometry: str = "stretch"
    preprocessing_profile: str = "lab-linear-256-v1-not-team-reference"

    def __post_init__(self):
        dimensions = (self.original_width, self.original_height, self.resized_width, self.resized_height)
        pads = (self.left, self.top, self.right, self.bottom)
        if (type(self.rgb) is not bytes or len(self.rgb) != 196608
                or any(type(value) is not int or value <= 0 for value in dimensions)
                or self.original_width * self.original_height > MAX_IMAGE_PIXELS
                or any(type(value) is not int or value < 0 for value in pads)
                or self.left + self.resized_width + self.right != 256
                or self.top + self.resized_height + self.bottom != 256
                or self.geometry not in {"stretch", "letterbox"}
                or self.geometry == "stretch" and any(pads)
                or self.preprocessing_profile != "lab-linear-256-v1-not-team-reference"):
            raise ValueError("Invalid laboratory transform snapshot")

    @property
    def sha256(self):
        return sha256(self.rgb).hexdigest()


def prepare_lab_input(image_bgr: np.ndarray, geometry="stretch") -> PreparedInput:
    if (not isinstance(image_bgr, np.ndarray) or image_bgr.dtype != np.uint8 or image_bgr.ndim != 3
            or image_bgr.shape[2] != 3 or not 0 < image_bgr.shape[0] * image_bgr.shape[1] <= MAX_IMAGE_PIXELS):
        raise ValueError("Invalid original uint8 BGR image")
    height, width = image_bgr.shape[:2]
    if geometry == "stretch":
        rw, rh, left, top = 256, 256, 0, 0
    elif geometry == "letterbox":
        factor = min(256 / width, 256 / height)
        rw, rh = max(1, round(width * factor)), max(1, round(height * factor))
        left, top = (256 - rw) // 2, (256 - rh) // 2
    else:
        raise ValueError("Only explicit laboratory stretch/letterbox transforms are supported")
    resized = cv2.resize(image_bgr, (rw, rh), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((256, 256, 3), np.uint8)
    canvas[top:top + rh, left:left + rw] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).tobytes()
    return PreparedInput(rgb, width, height, rw, rh, left, top, 256-rw-left, 256-rh-top, geometry)


def restore_binary_mask(data: bytes, prepared: PreparedInput) -> np.ndarray:
    if type(data) is not bytes or len(data) != 65536:
        raise ValueError("Mask must contain exactly 65536 bytes")
    mask = np.frombuffer(data, dtype=np.uint8).reshape(256, 256)
    if not np.isin(mask, (0, 255)).all():
        raise ValueError("Mask must contain only 0 and 255")
    if not isinstance(prepared, PreparedInput):
        raise ValueError("Invalid transform snapshot")
    cropped = mask[prepared.top:prepared.top+prepared.resized_height, prepared.left:prepared.left+prepared.resized_width]
    return cv2.resize(cropped, (prepared.original_width, prepared.original_height), interpolation=cv2.INTER_NEAREST)


def diagnostic_pattern() -> bytes:
    """Asymmetric RGB row/column encoding catches channel, flip and stride errors."""
    y, x = np.indices((256, 256), dtype=np.uint16)
    return np.stack((x, y, (x * 3 + y * 5) % 256), axis=2).astype(np.uint8).tobytes()
