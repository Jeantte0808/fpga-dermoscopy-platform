from pathlib import Path
import time
from hashlib import sha256

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dermademo.cpu_ui import CPUDialog
from dermademo.cpu_baseline import load_comparison
from dermademo.formal_results import replay_pair
from test_gui import app,window
from test_rgb_device_gui import drain


def idle(app,d):
    end=time.monotonic()+60
    while d.busy and time.monotonic()<end:app.processEvents();time.sleep(.003)
    app.processEvents();assert not d.busy,d.status.text()


@pytest.fixture
def dialog(app,window):
    d=CPUDialog(window);d.show();drain(app)
    yield d
    if d.busy:d.cancel();idle(app,d)
    d.close();drain(app)


@pytest.mark.parametrize('theme',['light','dark','high-contrast'])
def test_cpu_workspace_no_implicit_run_and_failure_keeps_old_result(app,window,dialog,theme,tmp_path,monkeypatch):
    import dermademo.cpu_ui as ui
    class FakeClient:
        def __init__(self,path):self.python=Path(path).resolve()
        def run(self,rgb,**_k):
            return bytes(65536),{'device':'cpu','dtype':'float32','threshold':'sigmoid(logits)>0.5',
                 'rgb_sha256':sha256(rgb).hexdigest(),'runtime':{'load_ms':1.,'environment':{'device':'cpu'}},
                 'report':{'rgb_to_mask_ms':2.},'records':[]}
        def close(self):pass
    monkeypatch.setattr(ui,'CPUClient',FakeClient)
    app.fluent_theme.set_mode(theme)
    window.commit_formal_result(replay_pair())
    original=window.result
    dialog.use_current();idle(app,dialog)
    assert dialog.rgb is not None and dialog.success is None
    assert dialog.fpga_metadata['source_kind']=='saved_FPGA_capture'
    assert dialog.run_button.isEnabled()
    dialog.activateWindow();dialog.run_button.setFocus();drain(app)
    QTest.keyClick(dialog,Qt.Key.Key_Return);idle(app,dialog)
    assert dialog.success is not None and window.result is original
    assert '非标注精度' in dialog.result_label.text()
    previous=dialog.success
    def fail(*_a,**_k):raise RuntimeError('CPU dependency error')
    monkeypatch.setattr(dialog.client,'run',fail)
    dialog.run_cpu();idle(app,dialog)
    assert dialog.success is previous and dialog.save_button.isEnabled()
    monkeypatch.setattr(ui.QFileDialog,'getExistingDirectory',lambda *_a:str(tmp_path))
    QTest.keyClick(dialog,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier);idle(app,dialog)
    assert load_comparison(dialog.last_export)[0]==dialog.rgb
    dialog.open_folder(dialog.last_export);idle(app,dialog)
    assert dialog.success is not None and '本次未推理' in dialog.input_label.text()
    app.fluent_theme.set_mode('light')
