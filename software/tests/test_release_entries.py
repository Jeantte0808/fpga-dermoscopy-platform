import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _run(tmp_path, *arguments):
    return subprocess.run([sys.executable, "-m", "dermademo.tools_cli", *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "ALIENHOME_DATA_DIR": str(tmp_path / "logs")})


def test_console_dispatch_resources_no_qt_or_network_and_preserves_exit_codes(tmp_path):
    assert _run(tmp_path, "--help").returncode == 0
    from dermademo import __version__
    assert _run(tmp_path, "--version").stdout.strip() == __version__
    result = _run(tmp_path, "verify-resources")
    assert result.returncode == 0
    assert json.loads(result.stdout)["format"] == "alienhome.resources"
    assert _run(tmp_path, "acceptance-check").returncode == 0
    assert _run(tmp_path, "batch", str(tmp_path / "missing")).returncode == 2
    assert _run(tmp_path, "unknown").returncode == 2
    assert _run(tmp_path, "diagnostics", str(tmp_path)).returncode == 0


def test_explicit_gui_smoke_covers_save_reopen_themes_and_diagnostics(tmp_path):
    target = tmp_path / "smoke"
    result = subprocess.run([sys.executable, "-m", "dermademo", "--smoke-output", str(target)],
        capture_output=True, timeout=100, env={**os.environ, "ALIENHOME_DATA_DIR": str(tmp_path / "logs")})
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    report = json.loads((target / "gui-smoke.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert {"sample_import", "save_record", "reopen_record", "diagnostic_export", "theme_high-contrast"} <= set(report["checks"])
    assert report["clean_machine_verified"] is False
    assert len(list(target.glob("gui-*.png"))) == 3


def test_frozen_resource_check_detects_modified_theme_file(tmp_path, monkeypatch):
    import shutil
    from dermademo.runtime_paths import bundle_root
    from dermademo.resources import resource_files, verify_resources
    inventory = verify_resources()
    for source in resource_files():
        target = tmp_path / source.relative_to(bundle_root())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (tmp_path / "resource-manifest.json").write_text(json.dumps(inventory), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert verify_resources(root=tmp_path) == inventory
    (tmp_path / "dermademo/_fluent/fluent.qss.in").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        verify_resources(root=tmp_path)


def test_local_wheel_reconstruction_rejects_modified_installed_bytes(tmp_path):
    import base64
    from hashlib import sha256
    from scripts.cache_runtime_wheels import repack
    payload = b"installed original\n"
    hashed = "sha256=" + base64.urlsafe_b64encode(sha256(payload).digest()).decode().rstrip("=")
    (tmp_path / "package.py").write_bytes(payload)
    class Distribution:
        version = "1.0"
        metadata = {"Name": "package"}
        def read_text(self, name):
            return "Tag: py3-none-any\n" if name == "WHEEL" else f"package.py,{hashed},{len(payload)}\n"
        def locate_file(self, name):
            return tmp_path / name
    (tmp_path / "package.py").write_bytes(b"unexpected modification")
    with pytest.raises(ValueError, match="RECORD mismatch"):
        repack(Distribution(), tmp_path)


def test_release_inventory_rejects_extra_files_and_tampering(tmp_path):
    from hashlib import sha256
    from scripts.verify_release import verify_inventory
    data = b"executable placeholder"
    (tmp_path / "app.exe").write_bytes(data)
    spec = {"bytes": len(data), "sha256": sha256(data).hexdigest()}
    (tmp_path / "release-manifest.json").write_text(json.dumps({"files": {"app.exe": spec}}), encoding="utf-8")
    (tmp_path / "SHA256SUMS.txt").write_text(f"{spec['sha256']}  app.exe\n", encoding="utf-8")
    verify_inventory(tmp_path)
    (tmp_path / "private.png").write_bytes(b"private")
    with pytest.raises(ValueError, match="unexpected"):
        verify_inventory(tmp_path)
    (tmp_path / "private.png").unlink()
    (tmp_path / "app.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest"):
        verify_inventory(tmp_path)


def test_license_collection_preserves_raw_multiline_metadata(tmp_path, monkeypatch):
    from scripts import build_release
    original = "Metadata-Version: 2.1\nName: legacy\nDescription: first line\nsecond line: embedded header\n"
    class Distribution:
        files = []
        version = "1.0"
        def read_text(self, name):
            return original if name == "METADATA" else None
    monkeypatch.setattr(build_release, "pinned_packages", lambda: {"legacy": "1.0"})
    monkeypatch.setattr(build_release.metadata, "distribution", lambda _: Distribution())
    build_release.copy_licenses(tmp_path / "licenses")
    assert (tmp_path / "licenses/legacy/PACKAGE-METADATA.txt").read_text(encoding="utf-8") == original
