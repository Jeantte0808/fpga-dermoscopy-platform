"""Optional real-data Qt acceptance: enabled after prepare_isic2016.ps1."""

import json
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
import pytest

from dermademo.records import load_record
from dermademo.storage import file_sha256
from test_gui import app, window, wait_for_idle


ROOT = Path(os.environ.get("ISIC2016_ROOT", Path(__file__).resolve().parents[1] / "datasets/isic2016/prepared"))
pytestmark = pytest.mark.skipif(not (ROOT / "manifest.json").is_file(), reason="Prepare official ISIC 2016 data first")


def test_real_isic_enter_pending_save_reopen_and_large_view(app, window, tmp_path, monkeypatch):
    assert not (ROOT / "INCOMPLETE.txt").exists()
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    demo_ids = manifest["demo"]["ids"]
    # Three coverage ranks; batch verification covers all 30 originals.
    chosen = [demo_ids[0], demo_ids[len(demo_ids) // 2], demo_ids[-1]]
    for image_id in chosen:
        assert window.load_path(ROOT / "demo/images" / f"{image_id}.jpg")
        wait_for_idle(app, window, timeout=45)
        assert not window.is_synthetic and window.result is None
        window.restore_defaults()
        window.activateWindow()
        window.panels[0].view.setFocus()
        app.processEvents()
        QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
        wait_for_idle(app, window, timeout=45)
        result, request = window.result, window.request
        assert result is not None and all(panel.view.has_image for panel in window.panels)
        assert result.mask.shape == window.image.shape[:2]
        assert window.grab().save(str(tmp_path / f"{image_id}-preview.png"))
        before = [panel.view.pixmap.toImage() for panel in window.panels]
        window.threshold_mode.setCurrentIndex(1)
        window.threshold_spin.setValue(100)
        app.processEvents()
        assert window.parameters_pending and window.result is result
        assert before == [panel.view.pixmap.toImage() for panel in window.panels]
        panel = window.panels[0]
        QTest.mouseDClick(panel.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel.view.viewport().rect().center())
        app.processEvents()
        assert panel.viewer is not None
        panel.viewer.actual_button.click()
        panel.viewer.zoom_in_button.click()
        panel.viewer.close()
        window.activateWindow()
        window.panels[0].view.setFocus()
        app.processEvents()
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
        QTest.keyClick(window.panels[0].view, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        wait_for_idle(app, window, timeout=45)
        saved = load_record(window.last_export)
        assert saved.request.config == request.config and not saved.request.is_synthetic
        assert saved.request.task_id == request.task_id
        np.testing.assert_array_equal(saved.result.mask, result.mask)
        assert window.start_open_record(window.last_export)
        wait_for_idle(app, window, timeout=45)
        assert window.record_view_only
        np.testing.assert_array_equal(window.result.mask, result.mask)
        window.activateWindow()
        window.panels[0].view.setFocus()
        app.processEvents()
        QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
        assert not window.busy  # Record viewing stays read-only.


def test_isic_manifest_has_fixed_disjoint_splits_and_validation_demo():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"train": 718, "validation": 179, "test": 379, "excluded_test_duplicate": 3}
    by_id = {item["id"]: item for item in manifest["items"]}
    assert len(set(manifest["demo"]["ids"])) == 30
    assert all(by_id[image_id]["split"] == "validation" for image_id in manifest["demo"]["ids"])
    groups = [{item["id"] for item in manifest["items"] if item["split"] == split}
              for split in ("train", "validation", "test")]
    assert not groups[0] & groups[1] and not groups[0] & groups[2] and not groups[1] & groups[2]
    hashes = [{item["image_sha256"] for item in manifest["items"] if item["split"] == split}
              for split in ("train", "validation", "test")]
    assert not hashes[0] & hashes[1] and not hashes[0] & hashes[2] and not hashes[1] & hashes[2]
    reference = json.loads((Path(__file__).resolve().parents[1] / "examples/isic2016_reference.json").read_text(encoding="utf-8"))
    assert manifest["archives"] == reference["archives"]
    assert manifest["demo"] == reference["demo"]
    for split, ids in reference["split_ids"].items():
        assert sorted(item["id"] for item in manifest["items"] if item["split"] == split) == ids
    for item in reference["demo_items"]:
        assert file_sha256(ROOT / "demo/images" / Path(item["image"]).name) == item["image_sha256"]
        assert file_sha256(ROOT / "demo/labels" / Path(item["label"]).name) == item["mask_sha256"]
