"""Explicit backend registration. Uninstalled models/devices are never advertised."""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from math import isclose, isfinite

from .configuration import parse_config, serialize_config
from .contracts import (BackendCapabilities, BackendConfig, ProcessingBackend,
                        ProcessingConfig, ProcessingRequest, ProcessingResult)
from .imaging import MAX_IMAGE_PIXELS, max_feret_diameter


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    label: str
    kind: str
    default: str | int | float | bool | None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    nullable: bool = False

    def __post_init__(self):
        if self.kind not in {"int", "float", "bool", "str", "choice"} or type(self.nullable) is not bool:
            raise ValueError("参数声明类型无效")
        object.__setattr__(self, "choices", tuple(self.choices))
        for bound in (self.minimum, self.maximum):
            if bound is not None and (type(bound) not in (int, float) or not isfinite(bound)):
                raise ValueError("参数上下界无效")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("参数上下界顺序无效")
        self.validate(self.default)

    def validate(self, value):
        if value is None and self.nullable:
            return None
        valid = {"int": type(value) is int, "float": type(value) in (int, float),
                 "bool": type(value) is bool, "str": type(value) is str,
                 "choice": type(value) is str and value in self.choices}.get(self.kind, False)
        if not valid:
            raise ValueError(f"{self.label} 参数类型或选项无效")
        if self.kind in {"int", "float"}:
            if (not isfinite(value) or self.minimum is not None and value < self.minimum
                    or self.maximum is not None and value > self.maximum):
                raise ValueError(f"{self.label} 超出允许范围")
        if self.kind in {"str", "choice"} and (len(value) > 1024 or "\x00" in value):
            raise ValueError(f"{self.label} 字符串无效")
        return float(value) if self.kind == "float" else value


@dataclass(frozen=True)
class BackendDefinition:
    capabilities: BackendCapabilities
    factory: Callable[[], ProcessingBackend]
    parameters: tuple[ParameterSpec, ...]
    config_version: str = "1"
    max_input_pixels: int = MAX_IMAGE_PIXELS
    legacy_demo_config: bool = False

    def configure(self, values: Mapping | None = None) -> ProcessingConfig | BackendConfig:
        values = {} if values is None else values
        if not isinstance(values, Mapping):
            raise ValueError("后端参数必须为对象")
        allowed = {spec.name for spec in self.parameters}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"后端 {self.capabilities.key} 不支持参数：{', '.join(map(str, unknown))}")
        validated = {spec.name: spec.validate(values.get(spec.name, spec.default)) for spec in self.parameters}
        if self.legacy_demo_config:
            return parse_config(validated)
        return BackendConfig(self.capabilities.key, self.config_version, validated)

    def parse_config_document(self, data: object):
        """Accept a flat CLI config, or the versioned envelope used in model records."""
        if not isinstance(data, dict):
            raise ValueError("参数 JSON 必须为对象")
        if isinstance(data, dict) and set(data) == {"backend_key", "config_version", "values"}:
            if data["backend_key"] != self.capabilities.key or data["config_version"] != self.config_version:
                raise ValueError("参数后端或配置版本不匹配")
            if not isinstance(data["values"], dict):
                raise ValueError("后端参数 values 必须为对象")
            return self.configure(data["values"])
        return self.configure(data)

    def validate_request(self, request: ProcessingRequest):
        request.validate()
        if request.image_bgr.shape[0] * request.image_bgr.shape[1] > self.max_input_pixels:
            raise ValueError("输入超过此后端的像素限制")
        config = request.config
        if self.legacy_demo_config:
            if not isinstance(config, ProcessingConfig):
                raise ValueError("当前后端需要临时 CPU 参数")
            values = serialize_config(config)
        else:
            if (not isinstance(config, BackendConfig) or config.backend_key != self.capabilities.key
                    or config.config_version != self.config_version):
                raise ValueError("请求参数与后端或配置版本不匹配")
            values = config.values
        if set(values) != {spec.name for spec in self.parameters}:
            raise ValueError("处理请求必须包含完整参数快照")
        self.configure(values)

    def describe(self) -> dict:
        return {"backend": asdict(self.capabilities), "config_version": self.config_version,
                "parameters": [{**asdict(spec), "choices": list(spec.choices)} for spec in self.parameters],
                "default_config": serialize_config(self.configure()),
                "input": {"color_space": "BGR", "dtype": "uint8", "layout": "HWC",
                          "max_pixels": self.max_input_pixels, "coordinate_space": "EXIF_normalized_original"},
                "output": {"mask_encoding": "uint8_0_255", "coordinate_space": "original_image"}}


class BackendSession:
    """Validate capabilities, configuration and returned results around an adapter."""

    def __init__(self, definition: BackendDefinition):
        self.definition = definition
        self._backend = definition.factory()
        self.capabilities = definition.capabilities
        if self._backend.capabilities != self.capabilities:
            raise ValueError("创建的后端与注册能力不一致")

    def process(self, request: ProcessingRequest) -> ProcessingResult:
        self.definition.validate_request(request)
        result = self._backend.process(request)
        if not isinstance(result, ProcessingResult) or result.capabilities != self.capabilities:
            raise ValueError("返回结果与已注册后端不一致")
        result.validate(request)
        mapping = result.metadata.get("coordinate_mapping", {})
        if mapping.get("output_space") != "original_image":
            raise ValueError("结果必须先恢复到原图坐标")
        if result.measurements.diameter_definition != "max_distance_between_boundary_pixel_centers":
            raise ValueError("后端最大径定义不支持")
        measured = max_feret_diameter(result.mask)
        actual = result.measurements.max_feret_px
        if measured is not None and not isclose(measured, actual, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("后端最大径与最终 Mask 不一致")
        return result


class BackendRegistry:
    def __init__(self):
        self._definitions: dict[str, BackendDefinition] = {}

    def register(self, definition: BackendDefinition):
        caps = definition.capabilities
        # Validate the envelope even if this definition uses legacy demo settings.
        BackendConfig(caps.key, definition.config_version, {})
        if caps.key in self._definitions:
            raise ValueError(f"后端 {caps.key} 已注册")
        if caps.color_space != "BGR" or type(definition.max_input_pixels) is not int or not 1 <= definition.max_input_pixels <= MAX_IMAGE_PIXELS:
            raise ValueError("后端输入约束无效")
        names = [spec.name for spec in definition.parameters]
        if len(set(names)) != len(names) or any(not name.isidentifier() for name in names):
            raise ValueError("参数名称无效或重复")
        definition.configure()
        self._definitions[caps.key] = definition

    def get(self, key: str) -> BackendDefinition:
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise ValueError(f"后端 {key} 未安装或未注册") from exc

    def describe(self) -> list[dict]:
        return [definition.describe() for definition in self._definitions.values()]

    def open(self, key: str) -> BackendSession:
        return BackendSession(self.get(key))


def create_registry() -> BackendRegistry:
    from .backends import DemoCPUBackend

    registry = BackendRegistry()
    registry.register(BackendDefinition(DemoCPUBackend.capabilities, DemoCPUBackend, (
        ParameterSpec("clahe_clip", "增强强度", "float", 2.0, 0.5, 8.0),
        ParameterSpec("threshold", "暗区域阈值", "int", None, 0, 255, nullable=True),
        ParameterSpec("min_region_pixels", "最小区域面积", "int", 64, 1, 1_000_000),
    ), legacy_demo_config=True))
    return registry
