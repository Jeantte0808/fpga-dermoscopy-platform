from dataclasses import replace
from pathlib import Path
import json
import sys

import numpy as np
import pytest

from dermademo.configuration import parse_parameters, serialize_config, strict_json
from dermademo.contracts import (BackendCapabilities, BackendConfig, Measurements,
                                ProcessingConfig, ProcessingRequest, ProcessingResult)
from dermademo.imaging import make_overlay, max_feret_diameter
from dermademo.records import load_record
from dermademo.registry import BackendDefinition, BackendRegistry, ParameterSpec, create_registry
from dermademo.storage import save_result


class ContractBackend:
    """Deterministic contract fixture, not a model or installed production backend."""

    capabilities = BackendCapabilities("contract_test", "仅用于接口测试", "fixture/1", True)

    def process(self, request):
        config = request.config.values
        mask = np.where(request.image_bgr[:, :, 0] / 255 < config["probability_threshold"], 255, 0).astype(np.uint8)
        result = ProcessingResult(request.task_id, request.image_bgr.copy(), mask,
            make_overlay(request.image_bgr, mask), Measurements(int(np.count_nonzero(mask)), max_feret_diameter(mask)),
            self.capabilities, {"end_to_end": 0.0, "backend_compute": 0.0},
            {"coordinate_mapping": {"type": "identity", "output_space": "original_image"},
             "calibration": {"status": "not_calibrated", "area_mm2": None, "diameter_mm": None}})
        return result


def contract_registry(factory=ContractBackend):
    registry = BackendRegistry()
    registry.register(BackendDefinition(ContractBackend.capabilities, factory, (
        ParameterSpec("probability_threshold", "概率阈值", "float", 0.5, 0, 1),
        ParameterSpec("output_policy", "输出策略", "choice", "binary", choices=("binary",)),
        ParameterSpec("enabled", "启用", "bool", True),
    )))
    return registry


def generic_request(tmp_path, registry=None):
    registry = registry or contract_registry()
    image = np.full((20, 30, 3), 200, np.uint8)
    image[4:12, 5:14] = 50
    image.setflags(write=False)
    return ProcessingRequest(image, tmp_path / "接口中文图.png", registry.get("contract_test").configure(), True)


def test_only_available_backend_is_advertised_without_instantiating_it():
    registry = create_registry()
    assert [entry["backend"]["key"] for entry in registry.describe()] == ["demo_cpu"]
    assert registry.describe()[0]["default_config"] == {"clahe_clip": 2.0, "threshold": None, "min_region_pixels": 64}
    with pytest.raises(ValueError, match="未安装"):
        registry.open("fpga")
    definition = registry.get("demo_cpu")
    with pytest.raises(ValueError, match="已注册"):
        registry.register(definition)


def test_factory_identity_and_input_limit_are_enforced(tmp_path):
    class WrongFactory(ContractBackend):
        capabilities = replace(ContractBackend.capabilities, algorithm_version="different")
    with pytest.raises(ValueError, match="注册能力"):
        contract_registry(WrongFactory).open("contract_test")
    definition = replace(contract_registry().get("contract_test"), max_input_pixels=100)
    registry = BackendRegistry()
    registry.register(definition)
    with pytest.raises(ValueError, match="像素限制"):
        registry.open("contract_test").process(generic_request(tmp_path))
    with pytest.raises(ValueError, match="声明类型"):
        ParameterSpec("example", "例子", "unknown", None, nullable=True)
    with pytest.raises(ValueError, match="上下界顺序"):
        ParameterSpec("example", "例子", "float", 0.5, 1.0, 0.0)


def test_parameter_snapshot_is_immutable_isolated_and_backend_specific(tmp_path):
    values = {"probability_threshold": 0.6, "output_policy": "binary", "enabled": True}
    config = BackendConfig("contract_test", "1", values)
    values["probability_threshold"] = 0.2
    assert config.values["probability_threshold"] == 0.6
    with pytest.raises(TypeError):
        config.values["probability_threshold"] = 0.1
    assert parse_parameters(serialize_config(config), backend_key="contract_test") == config
    with pytest.raises(ValueError, match="不匹配"):
        parse_parameters(serialize_config(config), backend_key="demo_cpu")
    registry = contract_registry()
    request = generic_request(tmp_path, registry)
    result = registry.open("contract_test").process(request)
    assert result.measurements.area_px2 == 72
    np.testing.assert_array_equal(result.mask != 0, request.image_bgr[:, :, 0] == 50)
    assert not hasattr(config, "clahe_clip")


@pytest.mark.parametrize("values", [
    {"probability_threshold": True}, {"probability_threshold": float("nan")},
    {"probability_threshold": 1.1}, {"output_policy": "unknown"}, {"enabled": 1}, {"clahe_clip": 2},
])
def test_capability_constraints_reject_invalid_parameters(values):
    with pytest.raises(ValueError):
        contract_registry().get("contract_test").configure(values)


@pytest.mark.parametrize("values", [{"field": float("inf")}, {"field": [1]}, {"field": {}}, {"bad-key": 1}])
def test_record_parameter_snapshot_rejects_nonportable_values(values):
    with pytest.raises(ValueError):
        BackendConfig("contract_test", "1", values)


def test_config_version_and_returned_backend_must_match(tmp_path):
    registry = contract_registry()
    request = generic_request(tmp_path, registry)
    for config in (ProcessingConfig(), replace(request.config, backend_key="other"),
                   replace(request.config, config_version="2"), BackendConfig("contract_test", "1", {})):
        with pytest.raises(ValueError):
            registry.open("contract_test").process(replace(request, config=config))
    with pytest.raises(ValueError):
        registry.get("contract_test").parse_config_document({"backend_key": "other", "config_version": "1", "values": {}})
    class WrongResult(ContractBackend):
        def process(self, request):
            return replace(super().process(request), capabilities=replace(self.capabilities, algorithm_version="wrong"))
    with pytest.raises(ValueError, match="注册后端"):
        contract_registry(WrongResult).open("contract_test").process(request)


def test_original_coordinate_contract_is_enforced(tmp_path):
    class WrongSpace(ContractBackend):
        def process(self, request):
            result = super().process(request)
            return replace(result, metadata={**result.metadata, "coordinate_mapping": {"output_space": "model"}})
    with pytest.raises(ValueError, match="原图坐标"):
        contract_registry(WrongSpace).open("contract_test").process(generic_request(tmp_path))


def test_inconsistent_diameter_is_rejected_before_export(tmp_path):
    class WrongDiameter(ContractBackend):
        def process(self, request):
            result = super().process(request)
            return replace(result, measurements=replace(result.measurements, max_feret_px=1.0))
    with pytest.raises(ValueError, match="最大径与最终 Mask"):
        contract_registry(WrongDiameter).open("contract_test").process(generic_request(tmp_path))


def test_generic_schema_roundtrip_and_unknown_backend_resave(tmp_path):
    registry = contract_registry()
    request = generic_request(tmp_path, registry)
    result = registry.open("contract_test").process(request)
    saved = save_result(tmp_path, request, result)
    document = json.loads((saved / "record.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.1"
    assert "clahe_clip" not in document["parameters"]
    loaded = load_record(saved)
    assert loaded.request.config == request.config
    assert loaded.result.capabilities == result.capabilities
    destination = save_result(tmp_path, loaded.request, loaded.result, origin_record=loaded.document)
    restored = load_record(destination)
    assert restored.request.config == request.config
    assert restored.document["restored_from"]["record_id"] == document["record_id"]
    assert restored.result.timings_ms == result.timings_ms
    np.testing.assert_array_equal(restored.result.mask, result.mask)
    document["parameters"]["backend_key"] = "other"
    (saved / "record.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="不匹配"):
        load_record(saved)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_strict_json_rejects_ambiguous_config(text):
    with pytest.raises(ValueError):
        strict_json(text)


def test_record_reader_has_no_qt_dependency_in_fresh_process():
    import subprocess
    result = subprocess.run([sys.executable, "-c", "import sys; import dermademo.records; import dermademo.batch; assert not any(k.startswith('PySide6') for k in sys.modules)"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
