"""Validated user settings. No images or processing results are persisted."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

from .contracts import ProcessingConfig
from .configuration import parse_config
from .diagnostic_logging import record_event


def validate_name(name: object) -> str:
    if not isinstance(name, str) or any(char in name for char in "\r\n"):
        raise ValueError("预设名称不能包含换行")
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("请输入 1–40 个字符的预设名称")
    return name


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    backend_key: str
    config: ProcessingConfig

    def serialize(self) -> dict:
        return {"schema_version": 1, "id": self.id, "name": self.name,
                "backend_key": self.backend_key, "parameters": asdict(self.config)}

    @classmethod
    def parse(cls, data: object, backend_key: str):
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("预设版本无效")
        if data.get("backend_key") != backend_key:
            raise ValueError("预设后端不匹配")
        identifier = data.get("id")
        if not isinstance(identifier, str) or not identifier or identifier.startswith("builtin:"):
            raise ValueError("预设标识无效")
        return cls(identifier, validate_name(data.get("name")), backend_key, parse_config(data.get("parameters")))


class Preferences:
    def __init__(self, settings: QSettings | None = None, backend_key: str = "demo_cpu"):
        self.settings = settings if settings is not None else QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "FPGA Demo", "Derma Image Lab")
        self.backend_key = backend_key
        self.warning = ""
        self.writable = True
        self.config = ProcessingConfig()
        self.manual_draft = 110
        self.presets: list[Preset] = []
        self.geometry = QByteArray()
        self.import_dir = self.export_dir = ""
        self._load()

    @property
    def filename(self) -> str:
        return self.settings.fileName()

    def _warn(self, text: str) -> None:
        self.warning = text
        record_event("settings_warning", stage="settings", outcome="degraded")

    def _load(self) -> None:
        version = self.settings.value("schema_version", 1)
        try:
            if isinstance(version, bool) or str(version) != str(int(version)) or int(version) < 1:
                raise ValueError()
            if int(version) > 1:
                self.writable = False
                self._warn("设置版本不兼容 · 使用默认值，原文件不会被覆盖。")
                return
        except (ValueError, TypeError, OverflowError):
            self._warn("设置版本损坏 · 使用默认值。")
            return
        for key, attribute in (("paths/import_dir", "import_dir"), ("paths/export_dir", "export_dir")):
            value = self.settings.value(key, "")
            if isinstance(value, str):
                setattr(self, attribute, value)
        geometry = self.settings.value("window/geometry", QByteArray())
        if isinstance(geometry, QByteArray):
            self.geometry = geometry
        try:
            self.config = parse_config(json.loads(self.settings.value(
                f"parameters/{self.backend_key}", json.dumps(asdict(ProcessingConfig())))))
        except (TypeError, ValueError, OverflowError):
            self._warn("参数设置损坏 · 已恢复默认参数。")
        draft = self.settings.value("ui/manual_threshold_draft", 110)
        try:
            if isinstance(draft, bool) or str(draft) != str(int(draft)) or not 0 <= int(draft) <= 255:
                raise ValueError()
            self.manual_draft = int(draft)
        except (ValueError, TypeError, OverflowError):
            self._warn("手动阈值草稿无效 · 已恢复 110。")
        try:
            rows = json.loads(self.settings.value(f"presets/{self.backend_key}", "[]"))
            if not isinstance(rows, list):
                raise ValueError()
            for row in rows:
                try:
                    preset = Preset.parse(row, self.backend_key)
                    if any(p.id == preset.id or p.name == preset.name for p in self.presets):
                        raise ValueError()
                    self.presets.append(preset)
                except (ValueError, TypeError, OverflowError):
                    self._warn("已忽略无效或重复的用户预设。")
        except (ValueError, TypeError, OverflowError):
            self._warn("用户预设损坏 · 使用内置预设。")
        if self.settings.status() != QSettings.Status.NoError:
            self._warn("设置读取失败 · 部分设置使用默认值。")

    def directory(self, importing: bool) -> str:
        recent = self.import_dir if importing else self.export_dir
        if recent and Path(recent).is_dir():
            return recent
        kind = QStandardPaths.StandardLocation.PicturesLocation if importing else QStandardPaths.StandardLocation.DocumentsLocation
        fallback = QStandardPaths.writableLocation(kind)
        return fallback if fallback and Path(fallback).is_dir() else str(Path.home())

    def _write(self, values: dict) -> bool:
        if not self.writable:
            return False
        try:
            self.settings.setValue("schema_version", 1)
            for key, value in values.items():
                self.settings.setValue(key, value)
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                raise OSError("无法写入用户配置目录")
        except Exception as exc:
            self._warn(f"设置未保存 · {exc}")
            return False
        return True

    def remember_directory(self, path: Path, importing: bool) -> bool:
        attribute = "import_dir" if importing else "export_dir"
        setattr(self, attribute, str(path))
        return self._write({f"paths/{attribute}": str(path)})

    def save_session(self, config: ProcessingConfig, draft: int, geometry: QByteArray) -> bool:
        self.config = parse_config(asdict(config))
        if type(draft) is not int or not 0 <= draft <= 255:
            raise ValueError("手动阈值草稿无效")
        self.manual_draft, self.geometry = draft, geometry
        return self._write({f"parameters/{self.backend_key}": json.dumps(asdict(config)),
                            "ui/manual_threshold_draft": draft, "window/geometry": geometry})

    def add_preset(self, name: str, config: ProcessingConfig) -> Preset:
        name = validate_name(name)
        config = parse_config(asdict(config))
        if name in {"默认自动", "默认手动"} or any(p.name == name for p in self.presets):
            raise ValueError("预设名称已存在，请使用其他名称")
        preset = Preset(uuid4().hex, name, self.backend_key, config)
        self.presets.append(preset)
        self._save_presets()
        return preset

    def delete_preset(self, identifier: str) -> None:
        if not any(p.id == identifier for p in self.presets):
            raise ValueError("内置预设不可删除")
        self.presets = [p for p in self.presets if p.id != identifier]
        self._save_presets()

    def _save_presets(self) -> bool:
        return self._write({f"presets/{self.backend_key}": json.dumps(
            [p.serialize() for p in self.presets], ensure_ascii=False)})
