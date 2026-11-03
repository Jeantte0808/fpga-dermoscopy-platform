from dataclasses import replace
import json

import numpy as np
from PIL import Image
import pytest

from dermademo.backends import DemoCPUBackend
from dermademo.contracts import ProcessingConfig, ProcessingRequest
from dermademo.records import load_record
from dermademo.storage import save_result


@pytest.fixture
def saved(tmp_path):
    image = np.full((80, 100, 3), 210, np.uint8)
    image[20:60, 30:70] = (60, 80, 100)
    request = ProcessingRequest(image, tmp_path / "中文原图.png", ProcessingConfig(threshold=120), True)
    result = DemoCPUBackend().process(request)
    directory = save_result(tmp_path, request, result)
    return directory, request, result


def rewrite(directory, change):
    path = directory / "record.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_old_record_roundtrip_preserves_exact_data_and_resave_provenance(saved, tmp_path):
    directory, request, result = saved
    def legacy(data):
        data.update(application_version="0.2.0")
        data.pop("output_sha256")
    rewrite(directory, legacy)
    loaded = load_record(directory)
    assert loaded.request.config == request.config
    assert loaded.request.task_id == request.task_id
    assert loaded.request.submitted_at == request.submitted_at
    assert loaded.request.source_path.name == request.source_path.name
    assert loaded.result.timings_ms == result.timings_ms
    assert loaded.result.metadata == result.metadata
    for actual, expected in zip((loaded.request.image_bgr, loaded.result.enhanced_bgr, loaded.result.mask,
                                 loaded.result.overlay_bgr), (request.image_bgr, result.enhanced_bgr, result.mask, result.overlay_bgr)):
        np.testing.assert_array_equal(actual, expected)
        assert not actual.flags.writeable
    copy = save_result(tmp_path, loaded.request, loaded.result, origin_record=loaded.document)
    restored = load_record(copy)
    assert restored.document["restored_from"]["record_id"] == loaded.document["record_id"]
    assert restored.document["environment"] == loaded.document["environment"]
    assert restored.request.task_id == request.task_id
    assert restored.result.timings_ms == result.timings_ms


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version="2.0"),
    lambda d: d["outputs"].update(mask="../mask.png"),
    lambda d: d["outputs"].update(mask="C:/mask.png"),
    lambda d: d["outputs"].update(mask=d["outputs"]["original"]),
    lambda d: d["source"].update(width=True),
    lambda d: d["source"].update(width=1000000),
    lambda d: d["source"].update(decoded_bgr_sha256="bad"),
    lambda d: d["source"].update(filename="../source.png"),
    lambda d: d["parameters"].update(threshold=True),
    lambda d: d["measurements"].update(area_px2=0),
    lambda d: d["measurements"].update(max_feret_px=1),
    lambda d: d["measurements"].update(diameter_definition="bbox_width"),
    lambda d: d["timings_ms"].update(end_to_end=-1),
    lambda d: d["timings_ms"].update(end_to_end=True),
    lambda d: d["backend"].update(is_placeholder="yes"),
    lambda d: d.update(saved_at="invalid"),
    lambda d: d["processing"]["calibration"].update(status="calibrated"),
    lambda d: d["processing"]["coordinate_mapping"].update(output_space="model"),
])
def test_invalid_manifest_is_rejected(saved, change):
    directory, *_ = saved
    rewrite(directory, change)
    with pytest.raises(ValueError, match="记录读取失败"):
        load_record(directory)


@pytest.mark.parametrize("damage", ["marker", "missing", "corrupt", "size", "mask_encoding", "mask_binary", "json", "duplicate_json", "nan_json"])
def test_incomplete_or_damaged_record_is_rejected(saved, damage):
    directory, *_ = saved
    if damage == "marker":
        (directory / "INCOMPLETE.txt").write_text("interrupted")
    elif damage == "missing":
        (directory / "enhanced.png").unlink()
    elif damage == "corrupt":
        (directory / "overlay.png").write_bytes(b"broken")
    elif damage == "size":
        Image.new("RGB", (20, 20)).save(directory / "enhanced.png")
    elif damage == "mask_encoding":
        Image.new("RGB", (100, 80)).save(directory / "mask.png")
    elif damage == "mask_binary":
        Image.new("L", (100, 80), 128).save(directory / "mask.png")
    elif damage == "json":
        (directory / "record.json").write_text("{")
    elif damage == "duplicate_json":
        (directory / "record.json").write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    else:
        (directory / "record.json").write_text('{"schema_version":"1.0","value":NaN}')
    with pytest.raises(ValueError):
        load_record(directory)


def test_empty_mask_and_unknown_backend_can_be_viewed(saved):
    directory, request, _ = saved
    empty = replace(request, image_bgr=np.full_like(request.image_bgr, 210), config=ProcessingConfig())
    result = DemoCPUBackend().process(empty)
    empty_dir = save_result(directory.parent, empty, result)
    rewrite(empty_dir, lambda d: d["backend"].update(key="uninstalled", name="历史后端", algorithm_version="test/1"))
    loaded = load_record(empty_dir)
    assert loaded.result.measurements.area_px2 == 0
    assert loaded.result.measurements.max_feret_px is None
    assert loaded.result.capabilities.key == "uninstalled"


def test_valid_png_changed_after_save_is_detected(saved):
    directory, *_ = saved
    Image.new("RGB", (100, 80), (50, 70, 90)).save(directory / "enhanced.png")
    with pytest.raises(ValueError, match="enhanced 图片校验"):
        load_record(directory)
