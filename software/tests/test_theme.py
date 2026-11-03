from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest

from test_gui import app, window, process_sample


@pytest.mark.parametrize("mode", ["light", "dark", "system", "high-contrast"])
def test_theme_switch_preserves_images_and_operation_state(app, window, mode):
    process_sample(app, window)
    result = window.result
    image = window.panels[0].view.pixmap.toImage()
    window.theme_combo.setCurrentIndex(window.theme_combo.findData(mode))
    app.processEvents()
    assert "@{" not in app.styleSheet()
    assert window.result is result and window.panels[0].view.pixmap.toImage() == image
    assert window.save_button.isEnabled() and window.process_button.isEnabled()
    window.process_button.setFocus()
    assert window.process_button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.grab().width() >= 1060
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("system"))


def test_preset_selection_is_not_misrepresented_after_manual_edit(app, window):
    window.preset_combo.setCurrentIndex(2)
    assert window.preset_combo.currentText() == "默认手动"
    window.clip_spin.setValue(3)
    assert window.preset_combo.currentText() == "当前参数"


def test_submission_header_has_no_team_brand(app, window):
    from PySide6.QtWidgets import QLabel
    banned=''.join(map(chr,[22806,26143,20154,20043,23478]))
    assert banned not in window.windowTitle()
    assert banned not in window.findChild(QLabel, "subtitle").text()
    assert window.findChild(QLabel, "brand").text() == "D"
