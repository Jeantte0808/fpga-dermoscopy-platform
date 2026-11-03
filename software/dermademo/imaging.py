from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_PIXELS = 24_000_000


@dataclass(frozen=True)
class LoadedImage:
    source_path: Path
    image_bgr: np.ndarray
    is_synthetic: bool

    def validate(self) -> None:
        if not isinstance(self.source_path, Path) or type(self.is_synthetic) is not bool:
            raise ValueError("导入路径或样例标识无效")
        image = self.image_bgr
        if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
                or image.ndim != 3 or image.shape[2] != 3
                or not image.size or image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS
                or not image.flags.c_contiguous or image.flags.writeable):
            raise ValueError("导入图片尺寸、编码或只读状态无效")


def load_candidate(path: Path, is_synthetic: bool = False) -> LoadedImage:
    image = load_image(path)
    image.setflags(write=False)
    candidate = LoadedImage(Path(path), image, is_synthetic)
    candidate.validate()
    return candidate


def load_image(path: Path) -> np.ndarray:
    """Read Unicode paths, normalize EXIF orientation and produce uint8 BGR."""
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("目前支持 PNG 和 JPEG 图片")
    try:
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("文件实际格式不是 PNG 或 JPEG")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("支持不超过 2400 万像素的图片，请先缩小图片")
            image = ImageOps.exif_transpose(image)
            # Composite transparent PNGs on white instead of exposing hidden RGB.
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                image = Image.alpha_composite(background, rgba)
            rgb = np.asarray(image.convert("RGB"))
            return np.ascontiguousarray(rgb[:, :, ::-1])
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError(f"图片读取失败：{exc}") from exc


def largest_region(mask: np.ndarray, min_pixels: int = 1) -> np.ndarray:
    """8-connectivity; retain largest component; do not fill holes."""
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return np.zeros_like(binary)
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[label, cv2.CC_STAT_AREA] < min_pixels:
        return np.zeros_like(binary)
    return np.where(labels == label, 255, 0).astype(np.uint8)


def max_feret_diameter(mask: np.ndarray) -> float | None:
    """Exact largest distance on the convex hull of boundary pixel centers.

    Rotating calipers avoids an O(n²) pairwise-distance matrix. A single pixel
    has diameter 0 under this convention; the empty region has no diameter.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    points = np.concatenate(contours)
    hull = cv2.convexHull(points).reshape(-1, 2).astype(np.float64)
    n = len(hull)
    if n == 1:
        return 0.0
    if n == 2:
        return float(np.linalg.norm(hull[1] - hull[0]))

    def cross_area(i: int, j: int) -> float:
        edge = hull[(i + 1) % n] - hull[i]
        offset = hull[j] - hull[i]
        return abs(float(edge[0] * offset[1] - edge[1] * offset[0]))

    best_sq = 0.0
    j = 1
    for i in range(n):
        advances = 0
        while cross_area(i, (j + 1) % n) > cross_area(i, j) + 1e-9:
            j = (j + 1) % n
            advances += 1
            if advances >= n:
                break
        # Include tied antipodal points on parallel hull edges.
        for a, b in ((i, j), ((i + 1) % n, j), (i, (j + 1) % n)):
            delta = hull[a] - hull[b]
            best_sq = max(best_sq, float(delta @ delta))
    return best_sq ** 0.5


def make_overlay(original_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    overlay = original_bgr.copy()
    foreground = mask > 0
    color = np.array([154, 186, 24], dtype=np.float32)
    overlay[foreground] = (
        original_bgr[foreground].astype(np.float32) * 0.83 + color * 0.17
    ).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    width = max(1, round(min(mask.shape) / 240))
    cv2.drawContours(overlay, contours, -1, (150, 238, 40), width, cv2.LINE_AA)
    return overlay
