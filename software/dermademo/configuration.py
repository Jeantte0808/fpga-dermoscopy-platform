"""No-Qt configuration codecs shared by GUI settings, records and batch tools."""

from dataclasses import asdict
import json
from pathlib import Path

from .contracts import BackendConfig, ProcessingConfig


def parse_config(data: object) -> ProcessingConfig:
    """Legacy demo configuration; kept byte-for-byte compatible in schema 1.0."""
    if not isinstance(data, dict) or set(data) != {"clahe_clip", "threshold", "min_region_pixels"}:
        raise ValueError("参数字段不完整")
    config = ProcessingConfig(**data)
    config.validate()
    return ProcessingConfig(float(config.clahe_clip), config.threshold, config.min_region_pixels)


def serialize_config(config: ProcessingConfig | BackendConfig) -> dict:
    config.validate()
    if isinstance(config, ProcessingConfig):
        return asdict(config)
    return {"backend_key": config.backend_key, "config_version": config.config_version,
            "values": dict(config.values)}


def parse_parameters(data: object, *, backend_key: str | None = None) -> ProcessingConfig | BackendConfig:
    if isinstance(data, dict) and set(data) == {"backend_key", "config_version", "values"}:
        config = BackendConfig(**data)
        if backend_key is not None and config.backend_key != backend_key:
            raise ValueError("记录参数与后端不匹配")
        return config
    return parse_config(data)


def strict_json(text: str):
    def constant(value):
        raise ValueError(f"JSON 含有无效数值：{value}")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    return json.loads(text, parse_constant=constant, object_pairs_hook=unique)


def read_config(path: Path):
    if path.stat().st_size > 1_048_576:
        raise ValueError("参数 JSON 超过 1 MiB 限制")
    data = strict_json(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("参数 JSON 顶层必须为对象")
    return data
