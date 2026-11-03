from pathlib import Path
import json
import pytest
from PySide6.QtCore import Qt
from test_gui import app,window
from test_rgb_device_gui import drain
from test_roi_gui import image,idle
from dermademo.hardware_lab_ui import HardwareLabDialog
from dermademo.formal_results import replay_pair
from dermademo.hardware_lab import evidence


def wait(app,d):
    import time
    until=time.monotonic()+30
    while d.busy and time.monotonic()<until:app.processEvents();time.sleep(.003)
    assert not d.busy,d.status.text()


@pytest.mark.parametrize('theme',['light','dark','high-contrast'])
def test_lab_is_read_only_until_confirmed_and_has_no_invented_statistics(app,window,theme):
    app.fluent_theme.set_mode(theme);d=HardwareLabDialog(window);d.show();drain(app)
    try:
        assert not d.run_button.isEnabled() and not d.pdf_button.isEnabled()
        d.show_evidence(evidence(replay_pair()));d.refresh()
        assert d.stats_model.rowCount()>0 and '尚未冻结' in d.stats_notice.text()
        d.pin_baseline();d.show_evidence(evidence(replay_pair()))
        assert '不能计算正式提速' in d.compare_label.text()
        assert d.pin.isEnabled() and not d.cancel_button.isEnabled()
        assert sum(a.text()=='硬件对照实验台' for a in window.analysis_menu.actions())==1
    finally:d.close();drain(app);app.fluent_theme.set_mode('light')


def test_manual_roi_replaces_percentage_controls_and_legacy_chain_still_reopens(app,window,image,tmp_path,monkeypatch):
    from dermademo.roi_ui import ROIDialog
    import dermademo.roi_ui as ui
    from roi_test_support import FixtureROIProcessor
    from formal_mask_peer import FormalPeer,local_config
    d=ROIDialog(window,processor=FixtureROIProcessor());d.show();drain(app)
    try:
        with FormalPeer() as peer:
            peer.mask=bytes([255])+bytes(65535)
            monkeypatch.setattr(ui,'FormalConfig',lambda **_:local_config(peer))
            monkeypatch.setattr(ui,'default_output',lambda:tmp_path/'captures')
            d.load_source(image);idle(app,d);d._set_roi((40,40,200,200));idle(app,d)
            d.prepare_input();idle(app,d);d.start();idle(app,d)
            assert d.last_pair is not None,d.status.text()
            parent=d.last_pair;frame=parent[1].metadata['frame_id'];old=d.edge_label.text()
            assert not hasattr(d,'expand_button') and not hasattr(d,'expand_percent')
            d._set_roi((0,0,240,240));idle(app,d)
            assert d.selector.roi==(0,0,240,240) and d.last_pair is parent
            assert d.edge_label.text()==old and d.prepared is None and not d.run_button.isEnabled()
            d.prepare_input();idle(app,d);d.start();idle(app,d)
            assert d.last_pair[1].metadata['frame_id']!=frame
            assert 'roi_expansion' not in d.last_pair[1].metadata
            assert d.previous_group.isHidden()
            d.export_result(tmp_path/'saved');idle(app,d)
            saved=d.last_export;assert not (saved/'roi_chain.json').exists()
            d.open_folder(saved);idle(app,d)
            assert len(d._ancestors)==0
            from dermademo.roi_chain import save
            from dermademo.analysis_identity import result_identity
            child=d.last_pair
            child[1].metadata['roi_expansion']={'parent_identity':result_identity(parent),'status':'success'}
            legacy=save(tmp_path/'legacy',child,[parent])
            d.open_folder(legacy);idle(app,d)
            assert len(d._ancestors)==1 and d.previous_overlay.has_image and not d.previous_group.isHidden()
            d._set_roi((10,10,210,210));idle(app,d)
            assert not d.run_button.isEnabled() and d.prepared is None
    finally:
        if d.busy:d.cancel();idle(app,d)
        d.close();drain(app)


def test_failed_experiment_pdf_and_offline_reexport(app,tmp_path):
    from threading import Event
    from dermademo.hardware_lab import run
    from dermademo.hardware_lab_pdf import export
    from dermademo.device.formal_session import FormalConfig
    from teaching_fixtures import prepared_case
    cancel=Event();cancel.set()
    folder,doc=run([prepared_case()],FormalConfig(),tmp_path,warmup=0,repeats=1,
                   mode='fpga_sequence',cancel=cancel,state_dir=tmp_path/'state')
    p=export(folder);assert p.read_bytes().startswith(b'%PDF-') and p.stat().st_size>3000
    assert export(folder,historical=True)==p
    from dermademo.hardware_lab_cli import main
    assert main(['report','--experiment',str(folder)])==0


def test_parent_close_cancels_lab_worker_before_destroying_widgets(app,window):
    window.open_hardware_lab();d=window.hardware_lab_dialog;drain(app)
    def action(cancel,progress):
        cancel.wait(5)
        return None
    d.start_job('shutdown_fixture',action)
    assert d.busy and not window.close()
    wait(app,d);drain(app)
    assert window.hardware_lab_dialog is None
