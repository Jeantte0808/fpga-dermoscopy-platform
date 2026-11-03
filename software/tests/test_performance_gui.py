from dermademo.roi_ui import ROIDialog
from dermademo.cpu_ui import CPUDialog
from test_gui import app, window
from test_rgb_device_gui import drain


def test_diagnostic_modes_default_summary_and_do_not_change_last_result(app,window):
    window.open_formal_device()
    dialog=window.formal_dialog
    drain(app)
    assert dialog.telemetry_combo.currentData()=='summary'
    assert not dialog.telemetry_combo.isEnabled()  # Offline replay does not re-query.
    dialog.mode_combo.setCurrentIndex(1)
    assert dialog.telemetry_combo.isEnabled()
    previous=window.result
    dialog.telemetry_combo.setCurrentIndex(1)
    assert dialog.telemetry_combo.currentData()=='full' and window.result is previous
    dialog.close();drain(app)
    roi=ROIDialog(window)
    assert roi.telemetry_combo.currentData()=='summary'
    roi.telemetry_combo.setCurrentIndex(1)
    assert roi.telemetry_combo.currentData()=='full' and window.result is previous
    roi.close();drain(app)
