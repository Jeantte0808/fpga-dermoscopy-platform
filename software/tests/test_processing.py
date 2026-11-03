from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from dermademo.backends import DemoCPUBackend
from dermademo.contracts import ProcessingConfig, ProcessingRequest
from dermademo.imaging import largest_region, max_feret_diameter


def make_request(image=None, **config):
    if image is None:
        image = np.full((80, 100, 3), 205, np.uint8)
        image[20:60, 25:75] = (55, 75, 100)
    return ProcessingRequest(image, Path("测试图.png"), ProcessingConfig(**config))


def test_empty_and_known_geometry():
    mask = np.zeros((10, 12), np.uint8)
    assert max_feret_diameter(mask) is None
    mask[2:5, 3:7] = 255
    assert np.count_nonzero(mask) == 12
    assert max_feret_diameter(mask) == pytest.approx(np.sqrt(13))
    mask[:] = 0
    mask[1, 1] = 255
    assert max_feret_diameter(mask) == 0
    mask[1, 5] = 255
    assert max_feret_diameter(mask) == 4


def test_calipers_match_brute_force_on_random_hulls():
    rng = np.random.default_rng(42)
    for _ in range(60):
        points = rng.integers(1, 99, (35, 2), dtype=np.int32)
        hull = cv2.convexHull(points)
        mask = np.zeros((100, 100), np.uint8)
        cv2.fillConvexPoly(mask, hull, 255)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boundary = cv2.convexHull(np.concatenate(contours)).reshape(-1, 2).astype(float)
        distances = np.linalg.norm(boundary[:, None] - boundary[None, :], axis=2)
        assert max_feret_diameter(mask) == pytest.approx(distances.max())


def test_largest_component_and_holes_policy():
    mask = np.zeros((20, 20), np.uint8)
    mask[1:5, 1:5] = 255
    mask[8:18, 8:18] = 255
    mask[11:14, 11:14] = 0
    final = largest_region(mask)
    assert np.count_nonzero(final) == 91
    assert final[2, 2] == 0
    assert final[12, 12] == 0
    assert not largest_region(mask, 92).any()
    diagonal = np.eye(3, dtype=np.uint8) * 255
    assert np.count_nonzero(largest_region(diagonal)) == 3


def test_complete_backend_is_same_size_and_does_not_mutate_input():
    request = make_request()
    before = request.image_bgr.copy()
    result = DemoCPUBackend().process(request)
    result.validate(request)
    np.testing.assert_array_equal(request.image_bgr, before)
    assert result.measurements.area_px2 > 0
    assert result.capabilities.is_placeholder
    assert result.metadata["calibration"]["area_mm2"] is None
    assert result.metadata["coordinate_mapping"]["type"] == "identity"
    assert result.metadata["threshold_mode"] == "otsu"
    assert set(np.unique(result.mask)) == {0, 255}
    assert result.timings_ms["end_to_end"] >= result.timings_ms["backend_compute"]


def test_uniform_image_has_no_foreground():
    result = DemoCPUBackend().process(make_request(np.full((80, 100, 3), 180, np.uint8)))
    assert result.measurements.area_px2 == 0
    assert result.measurements.max_feret_px is None
    assert result.metadata["status"] == "no_foreground"


def test_manual_threshold_uses_original_image():
    request = make_request(threshold=120)
    first = DemoCPUBackend().process(request)
    second = DemoCPUBackend().process(replace(request, config=ProcessingConfig(clahe_clip=5, threshold=120)))
    np.testing.assert_array_equal(first.mask, second.mask)
    assert first.metadata["effective_threshold"] == 120
    assert first.metadata["threshold_mode"] == "manual"


@pytest.mark.parametrize("config", [
    ProcessingConfig(clahe_clip=0), ProcessingConfig(threshold=256), ProcessingConfig(min_region_pixels=0),
])
def test_invalid_config_rejected(config):
    with pytest.raises(ValueError):
        DemoCPUBackend().process(replace(make_request(), config=config))


def test_result_contract_rejects_wrong_task_mask_and_measurement():
    request = make_request()
    result = DemoCPUBackend().process(request)
    cases = [
        replace(result, task_id="other-task"),
        replace(result, mask=result.mask[:10]),
        replace(result, mask=np.ones_like(result.mask)),
        replace(result, measurements=replace(result.measurements, area_px2=1)),
        replace(result, measurements=replace(result.measurements, max_feret_px=float("nan"))),
        replace(result, timings_ms={"end_to_end": float("inf")}),
    ]
    for invalid in cases:
        with pytest.raises(ValueError):
            invalid.validate(request)
