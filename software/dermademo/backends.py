from time import perf_counter

import cv2
import numpy as np

from .contracts import BackendCapabilities, Measurements, ProcessingConfig, ProcessingRequest, ProcessingResult
from .imaging import largest_region, make_overlay, max_feret_diameter


class DemoCPUBackend:
    """Temporary dark-region segmentation; not a trained lesion model."""

    capabilities = BackendCapabilities(
        key="demo_cpu", name="CPU · 参考阈值分割", algorithm_version="clahe-otsu-demo/0.1.0",
        is_placeholder=True,
    )

    def process(self, request: ProcessingRequest) -> ProcessingResult:
        started = perf_counter()
        request.validate()
        if not isinstance(request.config, ProcessingConfig):
            raise ValueError("临时 CPU 后端只接受 demo_cpu 参数")
        original = request.image_bgr
        if original.dtype != np.uint8 or original.ndim != 3 or original.shape[2] != 3:
            raise ValueError("处理输入必须为 uint8 BGR 三通道图像")
        if original.size == 0:
            raise ValueError("图像为空")
        gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        preprocessed = perf_counter()

        lab = cv2.cvtColor(original, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(
            clipLimit=request.config.clahe_clip, tileGridSize=(8, 8)
        ).apply(lab[:, :, 0])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        automatic = request.config.threshold is None
        flags = cv2.THRESH_BINARY_INV | (cv2.THRESH_OTSU if automatic else 0)
        threshold, raw_mask = cv2.threshold(blurred, request.config.threshold or 0, 255, flags)
        if automatic and float(np.std(blurred)) < 1.0:
            raw_mask[:] = 0
        computed = perf_counter()

        mask = largest_region(raw_mask, request.config.min_region_pixels)
        measurements = Measurements(int(np.count_nonzero(mask)), max_feret_diameter(mask))
        overlay = make_overlay(original, mask)
        completed = perf_counter()
        result = ProcessingResult(
            task_id=request.task_id, enhanced_bgr=enhanced, mask=mask, overlay_bgr=overlay,
            measurements=measurements, capabilities=self.capabilities,
            timings_ms={
                "preprocessing": (preprocessed - started) * 1000,
                "backend_compute": (computed - preprocessed) * 1000,
                "postprocessing": (completed - computed) * 1000,
                "end_to_end": (completed - started) * 1000,
            },
            metadata={
                "threshold_mode": "otsu" if automatic else "manual",
                "effective_threshold": float(threshold),
                "segmentation_input": "original_BGR_to_GRAY_then_GaussianBlur_5x5",
                "postprocessing": {
                    "connectivity": 8, "region_policy": "largest_component",
                    "min_region_pixels": request.config.min_region_pixels,
                    "fill_holes": False,
                },
                "coordinate_mapping": {"type": "identity", "output_space": "original_image"},
                "calibration": {"status": "not_calibrated", "area_mm2": None, "diameter_mm": None},
                "status": "ok" if measurements.area_px2 else "no_foreground",
                "timing_scope": "loaded_image_to_complete_result_excluding_gui_queue_and_validation",
            },
        )
        result.validate(request)
        return result
