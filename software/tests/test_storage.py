import json

import numpy as np
from PIL import Image
import pytest

from dermademo.backends import DemoCPUBackend
from dermademo.contracts import ProcessingConfig, ProcessingRequest
from dermademo.imaging import load_image
from dermademo import storage


def test_unicode_paths_export_and_json_roundtrip(tmp_path):
    source = tmp_path / "中文源图.png"
    rgb = np.full((60, 80, 3), (225, 195, 170), np.uint8)
    rgb[15:45, 20:60] = (105, 75, 55)
    Image.fromarray(rgb).save(source)
    original_bytes = source.read_bytes()
    request = ProcessingRequest(load_image(source), source, ProcessingConfig(), is_synthetic=True)
    result = DemoCPUBackend().process(request)
    destination = storage.save_result(tmp_path, request, result)
    assert set(path.name for path in destination.iterdir()) == {
        "original.png", "enhanced.png", "mask.png", "overlay.png", "record.json",
    }
    record = json.loads((destination / "record.json").read_text(encoding="utf-8"))
    assert record["source"]["filename"] == source.name
    assert record["source"]["is_synthetic"] is True
    assert record["backend"]["is_placeholder"] is True
    assert record["parameters"]["threshold"] is None
    assert record["measurements"]["area_px2"] == int(np.count_nonzero(result.mask))
    assert record["processing"]["calibration"]["diameter_mm"] is None
    np.testing.assert_array_equal(np.asarray(Image.open(destination / "mask.png")), result.mask)
    np.testing.assert_array_equal(load_image(destination / "original.png"), request.image_bgr)
    assert source.read_bytes() == original_bytes
    assert storage.save_result(tmp_path, request, result) != destination


def test_corrupt_unsupported_transparent_and_exif_images(tmp_path):
    broken = tmp_path / "坏图.png"
    broken.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="图片读取失败"):
        load_image(broken)
    with pytest.raises(ValueError, match="支持 PNG"):
        load_image(tmp_path / "a.bmp")
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (3, 4), (1, 2, 3, 0)).save(transparent)
    assert (load_image(transparent) == 255).all()
    oriented = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (20, 10), "red").save(oriented, exif=exif)
    assert load_image(oriented).shape == (20, 10, 3)


def test_large_images_rejected_before_decoding(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (20, 20)).save(source)
    monkeypatch.setattr("dermademo.imaging.MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="2400 万像素"):
        load_image(source)


def test_partial_export_is_marked_and_reports_completed_files(tmp_path, monkeypatch):
    request = ProcessingRequest(np.full((20, 30, 3), 180, np.uint8), tmp_path / "source.png", ProcessingConfig())
    result = DemoCPUBackend().process(request)
    real_write = storage.write_png

    def broken_write(path, array):
        if path.name == "mask.png":
            raise PermissionError("simulated disk error")
        real_write(path, array)

    monkeypatch.setattr(storage, "write_png", broken_write)
    with pytest.raises(storage.ExportError) as caught:
        storage.save_result(tmp_path, request, result)
    assert caught.value.completed == ["original.png", "enhanced.png"]
    assert (caught.value.path / "INCOMPLETE.txt").exists()
    assert not (caught.value.path / "record.json").exists()
    assert "不是完整处理记录" in str(caught.value)


def test_unwritable_destination_reports_failure(tmp_path):
    request = ProcessingRequest(np.full((20, 30, 3), 180, np.uint8), tmp_path / "source.png", ProcessingConfig())
    result = DemoCPUBackend().process(request)
    not_directory = tmp_path / "a-file"
    not_directory.write_text("occupied")
    with pytest.raises(storage.ExportError) as caught:
        storage.save_result(not_directory, request, result)
    assert caught.value.completed == []
