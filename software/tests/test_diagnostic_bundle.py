from hashlib import sha256
import json
from pathlib import Path
import time
from zipfile import ZipFile

import numpy as np
import pytest
from PySide6.QtTest import QTest

from dermademo.diagnostic_bundle import export_diagnostics
from dermademo.diagnostic_logging import initialize


def test_default_archive_sanitized_snapshot_inventory_and_unique_names(tmp_path):
    current = initialize(tmp_path / "logs")
    try:
        raise ValueError(r"C:\Users\Private\photo.png secret")
    except ValueError as exc:
        current.event("import_failed", exception=exc, job_id="job-1", private_path="secret")
    first = export_diagnostics(tmp_path)
    second = export_diagnostics(tmp_path)
    assert first != second
    with ZipFile(first) as archive:
        assert set(archive.namelist()) == {"version.json", "events.jsonl", "resources.json", "log-status.json", "manifest.json"}
        combined = b"".join(archive.read(name) for name in archive.namelist())
        assert b"secret" not in combined and b"Private" not in combined
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        assert events[-1]["job_id"] == "job-1" and events[-1]["stack"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["includes_images"] is False
        for name, spec in manifest["files"].items():
            data = archive.read(name)
            assert spec == {"bytes": len(data), "sha256": sha256(data).hexdigest()}


def test_images_opt_in_limits_and_partial_cleanup(tmp_path, monkeypatch):
    image = np.zeros((8, 10, 3), np.uint8)
    image[:] = (1, 2, 3)
    result = export_diagnostics(tmp_path, images={"original": image})
    with ZipFile(result) as archive:
        from io import BytesIO
        from PIL import Image
        assert Image.open(BytesIO(archive.read("images/original.png"))).getpixel((0, 0)) == (3, 2, 1)
    with pytest.raises(ValueError):
        export_diagnostics(tmp_path, images={"../private": image})
    with pytest.raises(ValueError, match="大小上限"):
        export_diagnostics(tmp_path, images={"original": image}, limit=10)
    original = ZipFile.writestr
    def failing(self, name, data):
        original(self, name, data)
        raise OSError("disk full")
    monkeypatch.setattr(ZipFile, "writestr", failing)
    with pytest.raises(OSError):
        export_diagnostics(tmp_path)
    assert not list(tmp_path.glob("*.partial"))
    assert list(tmp_path.glob("*.zip")) == [result]


def _idle(app, dialog):
    deadline = time.monotonic() + 6
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        # Explicitly release the GIL to the Python export worker; QTest's native
        # wait can starve it when the full resource inventory is being hashed.
        time.sleep(.005)
    app.processEvents()
    assert not dialog.busy


def test_failed_exclusive_create_does_not_remove_an_existing_partial(tmp_path, monkeypatch):
    real_open = Path.open
    created = []
    def collide(path, mode="r", *args, **kwargs):
        if path.suffix == ".partial" and mode == "xb":
            with real_open(path, "wb") as stream:
                stream.write(b"another export owns this file")
            created.append(path)
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", collide)
    with pytest.raises(FileExistsError):
        export_diagnostics(tmp_path)
    assert created[0].read_bytes() == b"another export owns this file"


@pytest.mark.parametrize("mode", ["light", "dark", "high-contrast"])
def test_diagnostic_dialog_theme_export_failure_recovery_and_close(tmp_path, mode, monkeypatch):
    from dermademo.gui import MainWindow, create_application
    from dermademo.diagnostics_view import DiagnosticsDialog
    app = create_application()
    # Use the application's actual mode keys.
    app.fluent_theme.set_mode(mode)
    initialize(tmp_path / "logs")
    window = MainWindow()
    window.show()
    window.open_diagnostics()
    app.processEvents()
    dialog = window.diagnostics_dialog
    assert isinstance(dialog, DiagnosticsDialog)
    assert not dialog.include_images.isChecked()
    assert dialog.image_list.text().startswith("默认")
    dialog.export_to(tmp_path / "missing")
    _idle(app, dialog)
    assert "导出失败" in dialog.status.text() and "错误编号" in dialog.status.text()
    dialog.export_to(tmp_path)
    _idle(app, dialog)
    assert dialog.last_export.exists()
    assert dialog.export_button.geometry().right() <= dialog.width()
    # A slow worker must not be destroyed when either window is closed.
    import dermademo.diagnostics_view as view
    real = view.export_diagnostics
    def delayed(*args, **kwargs):
        time.sleep(0.1)
        return real(*args, **kwargs)
    monkeypatch.setattr(view, "export_diagnostics", delayed)
    dialog.export_to(tmp_path)
    window.close()
    assert window.isVisible() and dialog.busy
    # finished closes and deletes this WA_DeleteOnClose dialog asynchronously.
    deadline = time.monotonic() + 6
    while window.diagnostics_dialog is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert window.diagnostics_dialog is None
    window.close()
    window.deleteLater()
    app.processEvents()
