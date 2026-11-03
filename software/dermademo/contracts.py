"""GUI-independent contracts shared by future CPU/model/FPGA backends."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from math import isfinite
from types import MappingProxyType
from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

import numpy as np


@dataclass(frozen=True)
class ProcessingConfig:
    clahe_clip: float = 2.0
    threshold: int | None = None  # None: automatic Otsu, otherwise dark foreground
    min_region_pixels: int = 64

    def validate(self) -> None:
        if type(self.clahe_clip) not in (int, float) or not isfinite(self.clahe_clip) or not 0.5 <= self.clahe_clip <= 8.0:
            raise ValueError("增强强度应在 0.5–8.0 之间")
        if self.threshold is not None and (type(self.threshold) is not int or not 0 <= self.threshold <= 255):
            raise ValueError("分割阈值应在 0–255 之间")
        if type(self.min_region_pixels) is not int or not 1 <= self.min_region_pixels <= 1_000_000:
            raise ValueError("最小区域面积必须为正数")


@dataclass(frozen=True)
class BackendConfig:
    """A versioned, immutable flat parameter snapshot, independent of demo fields."""

    backend_key: str
    config_version: str
    values: Mapping[str, str | int | float | bool | None]

    def __post_init__(self):
        if not isinstance(self.values, Mapping):
            raise ValueError("后端参数必须为对象")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        self.validate()

    def validate(self) -> None:
        for value in (self.backend_key, self.config_version):
            if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(c in value for c in "\r\n\x00"):
                raise ValueError("参数后端或配置版本无效")
        if len(self.values) > 64:
            raise ValueError("后端参数过多")
        for key, value in self.values.items():
            if not isinstance(key, str) or not key.isidentifier() or len(key) > 64:
                raise ValueError("参数名称无效")
            if value is None or type(value) is bool:
                continue
            if type(value) in (int, float) and isfinite(value):
                continue
            if type(value) is str and len(value) <= 1024 and "\x00" not in value:
                continue
            raise ValueError(f"参数 {key} 必须为有限数值、布尔值、字符串或空值")


@dataclass(frozen=True)
class BackendCapabilities:
    key: str
    name: str
    algorithm_version: str
    is_placeholder: bool
    color_space: str = "BGR"


@dataclass(frozen=True)
class ProcessingRequest:
    image_bgr: np.ndarray
    source_path: Path
    config: ProcessingConfig | BackendConfig
    is_synthetic: bool = False
    task_id: str = field(default_factory=lambda: uuid4().hex)
    submitted_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    source_bytes: bytes | None = None  # Optional exact user JPEG/PNG snapshot; never re-read during save.

    def validate(self) -> None:
        image = self.image_bgr
        if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
                or image.shape[2] != 3 or not image.size or not image.flags.c_contiguous):
            raise ValueError("处理输入必须为连续 uint8 BGR 三通道图像")
        if not isinstance(self.config, (ProcessingConfig, BackendConfig)):
            raise ValueError("处理参数类型无效")
        self.config.validate()


@dataclass(frozen=True)
class Measurements:
    area_px2: int
    max_feret_px: float | None
    source: str = "PC / 最终 Mask"
    # Pixel-center contour convention, explicitly recorded for reproducibility.
    diameter_definition: str = "max_distance_between_boundary_pixel_centers"


@dataclass(frozen=True)
class ProcessingResult:
    task_id: str
    enhanced_bgr: np.ndarray
    mask: np.ndarray
    overlay_bgr: np.ndarray
    measurements: Measurements
    capabilities: BackendCapabilities
    timings_ms: dict[str, float]
    metadata: dict

    def validate(self, request: ProcessingRequest) -> None:
        request.validate()
        if self.task_id != request.task_id:
            raise ValueError("处理结果与当前任务不匹配")
        if isinstance(request.config, BackendConfig) and request.config.backend_key != self.capabilities.key:
            raise ValueError("结果后端与参数后端不匹配")
        shape = request.image_bgr.shape
        for image in (self.enhanced_bgr, self.overlay_bgr):
            if image.shape != shape or image.dtype != np.uint8:
                raise ValueError("结果图像尺寸或编码错误")
        if self.mask.shape != shape[:2] or self.mask.dtype != np.uint8:
            raise ValueError("Mask 必须恢复到原图尺寸并采用 uint8 编码")
        if not np.isin(self.mask, (0, 255)).all():
            raise ValueError("Mask 必须为 0/255 二值图")
        area = int(np.count_nonzero(self.mask))
        if type(self.measurements.area_px2) is not int or self.measurements.area_px2 != area:
            raise ValueError("面积与最终 Mask 不一致")
        diameter = self.measurements.max_feret_px
        if (area == 0 and diameter is not None) or (
            area > 0 and (type(diameter) not in (int, float) or not np.isfinite(diameter) or diameter < 0)
        ):
            raise ValueError("最大径状态与 Mask 不一致")
        if "end_to_end" not in self.timings_ms or any(
            type(value) not in (int, float) or not np.isfinite(value) or value < 0 for value in self.timings_ms.values()
        ):
            raise ValueError("处理耗时缺失或无效")


class ProcessingBackend(Protocol):
    capabilities: BackendCapabilities

    def process(self, request: ProcessingRequest) -> ProcessingResult: ...
