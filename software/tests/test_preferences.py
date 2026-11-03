from dataclasses import asdict
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QSettings

from dermademo.contracts import ProcessingConfig
from dermademo.preferences import Preferences, Preset, parse_config


def settings(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


@pytest.mark.parametrize("values", [
    {"clahe_clip": True}, {"clahe_clip": float("nan")}, {"clahe_clip": float("inf")},
    {"clahe_clip": "2"}, {"clahe_clip": 9}, {"threshold": True}, {"threshold": 12.5},
    {"threshold": -1}, {"min_region_pixels": False}, {"min_region_pixels": 1.5},
    {"min_region_pixels": 1_000_001},
])
def test_rejects_invalid_config(values):
    with pytest.raises(ValueError):
        parse_config({**asdict(ProcessingConfig()), **values})


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "x" * 41, "默认自动"])
def test_preset_names_rejected(tmp_path, name):
    prefs = Preferences(settings(tmp_path / "settings.ini"))
    with pytest.raises(ValueError):
        prefs.add_preset(name, ProcessingConfig())
    assert not prefs.presets


def test_roundtrip_session_presets_directories_and_deletion(tmp_path):
    path = tmp_path / "settings.ini"
    prefs = Preferences(settings(path))
    config = ProcessingConfig(3, None, 128)
    preset = prefs.add_preset(" 我的参数 ", config)
    with pytest.raises(ValueError):
        prefs.add_preset("我的参数", config)
    assert prefs.save_session(config, 95, QByteArray(b"geometry"))
    prefs.remember_directory(tmp_path, True)
    prefs.remember_directory(tmp_path, False)
    restored = Preferences(settings(path))
    assert restored.config == config and restored.manual_draft == 95
    assert restored.geometry == b"geometry"
    assert restored.directory(True) == str(tmp_path)
    assert restored.directory(False) == str(tmp_path)
    assert restored.presets == [preset]
    restored.delete_preset(preset.id)
    assert not Preferences(settings(path)).presets
    with pytest.raises(ValueError):
        restored.delete_preset("builtin:auto")


def test_corrupt_and_wrong_backend_presets_are_ignored(tmp_path):
    store = settings(tmp_path / "settings.ini")
    valid = Preset("unique", "正确参数", "demo_cpu", ProcessingConfig()).serialize()
    wrong = {**valid, "backend_key": "fpga"}
    future = {**valid, "schema_version": 2}
    store.setValue("presets/demo_cpu", json.dumps([wrong, future, valid, valid]))
    store.setValue("parameters/demo_cpu", "invalid-json")
    store.setValue("ui/manual_threshold_draft", 500)
    store.setValue("paths/import_dir", str(tmp_path / "deleted"))
    store.sync()
    prefs = Preferences(store)
    assert prefs.config == ProcessingConfig() and prefs.manual_draft == 110
    assert len(prefs.presets) == 1 and prefs.warning
    assert Path(prefs.directory(True)).is_dir()


def test_high_version_is_not_overwritten(tmp_path):
    path = tmp_path / "future.ini"
    store = settings(path)
    store.setValue("schema_version", 99)
    store.setValue("presets/demo_cpu", "opaque future content")
    store.sync()
    before = path.read_bytes()
    prefs = Preferences(store)
    assert not prefs.writable and prefs.config == ProcessingConfig()
    assert not prefs.save_session(ProcessingConfig(3), 110, QByteArray())
    prefs.add_preset("临时预设", ProcessingConfig())
    assert path.read_bytes() == before


def test_write_error_preserves_memory_and_reports_failure(tmp_path):
    class FailingSettings:
        def value(self, _key, default=None):
            return default
        def status(self):
            return QSettings.Status.NoError
        def fileName(self):
            return "unwritable.ini"
        def setValue(self, *_args):
            raise OSError("模拟只读目录")
    prefs = Preferences(FailingSettings())
    prefs.add_preset("本次会话", ProcessingConfig())
    assert prefs.presets and "设置未保存" in prefs.warning
    assert not prefs.save_session(ProcessingConfig(3), 80, QByteArray())
    assert prefs.config.clahe_clip == 3


def test_malformed_version_uses_defaults(tmp_path):
    store = settings(tmp_path / "bad.ini")
    store.setValue("schema_version", "broken")
    prefs = Preferences(store)
    assert prefs.warning and prefs.config == ProcessingConfig()
