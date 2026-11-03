import json

import pytest

from dermademo.timing_view import timing_rows
from test_gui import app, window, process_sample, wait_for_idle


@pytest.mark.parametrize("number", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_timing_is_rejected(number):
    with pytest.raises(ValueError):
        timing_rows({"end_to_end": number})


def test_missing_and_zero_are_distinct():
    rows = {key: value for key, caption, value in timing_rows({"end_to_end": 0})}
    assert rows["preprocessing"] == "不可用"
    assert rows["end_to_end"] == "0.00 ms"


def test_timing_matches_saved_snapshot_and_preserves_on_failed_import(app, window, tmp_path):
    process_sample(app, window)
    window.open_timing()
    detail = window.timing_dialog
    assert detail.result is window.result
    expected = dict(window.result.timings_ms)
    for key, number in expected.items():
        assert detail.values[key].text() == f"{number:.2f} ms"
    window.clip_spin.setValue(4)
    assert window.save_preset("耗时快照保持")
    assert window.timing_dialog is detail
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    record = json.loads((window.last_export / "record.json").read_text(encoding="utf-8"))
    assert record["timings_ms"] == expected
    window.start_import(tmp_path / "missing.png")
    wait_for_idle(app, window)
    assert window.timing_dialog is detail and detail.isVisible()
    window.start_import(window.source_path)
    wait_for_idle(app, window)
    assert window.timing_dialog is None and not window.timing_button.isEnabled()


def test_new_processing_closes_old_timing(app, window):
    process_sample(app, window)
    window.open_timing()
    window.start_processing()
    assert window.timing_dialog is None and not window.timing_button.isEnabled()
    wait_for_idle(app, window)
    assert window.timing_button.isEnabled()


def test_empty_mask_still_has_timing_details(app, window, tmp_path):
    from PIL import Image
    Image.new("RGB", (80, 60), (200, 200, 200)).save(tmp_path / "flat.png")
    window.start_import(tmp_path / "flat.png")
    wait_for_idle(app, window)
    window.start_processing()
    wait_for_idle(app, window)
    assert window.result.measurements.area_px2 == 0
    window.open_timing()
    assert "ms" in window.timing_dialog.values["end_to_end"].text()
