import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from dermademo.resources import resource_files, resource_path, verify_resources
from dermademo.runtime_paths import bundle_root, data_root, default_output, distribution_root


def test_source_locations_ignore_cwd_and_user_directory_is_separate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resource_path("sample").is_file()
    assert resource_path("rgb_root").joinpath("IMAGE.RAW").stat().st_size == 196608
    assert data_root() == tmp_path / "user-data"
    assert distribution_root() == bundle_root()
    assert default_output() == Path("outputs")


def test_frozen_layout_resource_inventory_and_missing_files(tmp_path, monkeypatch):
    files = resource_files()
    root = bundle_root()
    for source in files:
        destination = tmp_path / source.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    assert verify_resources(root=tmp_path) == verify_resources()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AlienHomeDerma.exe"))
    assert distribution_root() == tmp_path
    assert default_output().is_absolute()
    (tmp_path / "dermademo/_fluent/resources/qt-token-map.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_resources(root=tmp_path)


def test_invalid_override_does_not_create_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALIENHOME_DATA_DIR", "relative-private-data")
    with pytest.raises(ValueError):
        data_root()
    assert not (tmp_path / "relative-private-data").exists()


def test_import_has_no_qt_file_or_thread_side_effects(tmp_path):
    script = "import sys, threading; from pathlib import Path; before=set(threading.enumerate()); import dermademo.runtime_paths, dermademo.resources, dermademo.diagnostic_logging; assert set(threading.enumerate())==before; assert 'PySide6' not in sys.modules; assert not Path(__import__('os').environ['ALIENHOME_DATA_DIR']).exists()"
    env = {**os.environ, "ALIENHOME_DATA_DIR": str(tmp_path / "untouched")}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
