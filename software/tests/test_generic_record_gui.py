import json

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dermademo.storage import save_result
from test_gui import app, window, wait_for_idle
from test_registry import contract_registry, generic_request


def test_generic_record_view_and_continue_preserve_current_backend_settings(app, window, tmp_path):
    registry = contract_registry()
    request = generic_request(tmp_path, registry)
    result = registry.open("contract_test").process(request)
    saved = save_result(tmp_path, request, result)
    window.clip_spin.setValue(4)
    window.start_open_record(saved)
    wait_for_idle(app, window)
    assert window.record_view_only and window.result is not None
    assert not window.parameters_widget.isVisible()
    assert not window.generic_parameters.isVisible()
    window.technical_toggle.click()
    assert window.generic_parameters.isVisible() and window.generic_parameters.isReadOnly()
    assert json.loads(window.generic_parameters.toPlainText()) == dict(request.config.values)
    assert window.clip_spin.value() == 4
    assert "配置版本 1" in window.record_label.text()
    old = window.result
    QTest.keyClick(window.panels[0].view, Qt.Key.Key_Return)
    assert window.result is old and not window.busy
    window.export_to(tmp_path)
    wait_for_idle(app, window)
    exported = json.loads((window.last_export / "record.json").read_text(encoding="utf-8"))
    assert exported["parameters"]["values"] == dict(request.config.values)
    assert exported["schema_version"] == "1.1"
    window.continue_button.click()
    assert not window.record_view_only
    assert window.parameters_widget.isVisible() and not window.generic_parameters.isVisible()
    assert window.clip_spin.value() == 4 and window.result is old
    assert not window.restore_button.isEnabled()
    window.start_processing()
    wait_for_idle(app, window)
    assert window.result.capabilities.key == "demo_cpu" and window.request.config.clahe_clip == 4
