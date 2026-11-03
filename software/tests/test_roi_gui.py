from pathlib import Path
from dataclasses import replace
import time

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QPointF,Qt
from PySide6.QtTest import QTest

from dermademo.roi_ui import ROIDialog
from dermademo.roi_selector import ROISelector
from dermademo.records import load_record
from test_gui import app,window
from test_rgb_device_gui import drain
from roi_test_support import FixtureROIProcessor


def idle(app,d,timeout=20):
    end=time.monotonic()+timeout
    while (d.busy or d._timer.isActive()) and time.monotonic()<end:
        app.processEvents();time.sleep(.003)
    app.processEvents()
    assert not d.busy,d.status.text()


@pytest.fixture
def dialog(app,window):
    d=ROIDialog(window);d.show();drain(app)
    yield d
    if d.busy:d.cancel();idle(app,d)
    d.close();drain(app)


@pytest.fixture
def image(tmp_path):
    p=tmp_path/'原图.png'
    Image.fromarray(np.random.default_rng(4).integers(0,256,(300,400,3),np.uint8)).save(p)
    return p


def test_selector_draw_reverse_move_resize_and_zoom(app):
    v=ROISelector();v.resize(620,440);v.show()
    try:
        v.set_image(np.full((300,400,3),150,np.uint8));drain(app)
        def drag(start,end):
            a=v.mapFromScene(QPointF(*start));b=v.mapFromScene(QPointF(*end))
            QTest.mousePress(v.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,a)
            QTest.mouseMove(v.viewport(),b);QTest.mouseRelease(v.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,b)
            drain(app)
        drag((240,200),(120,100))
        r=v.roi;assert abs(r[0]-120)<=1 and abs(r[1]-100)<=1 and abs(r[2]-240)<=1
        drag(((r[0]+r[2])/2,(r[1]+r[3])/2),((r[0]+r[2])/2+25,(r[1]+r[3])/2+15))
        moved=v.roi;assert moved[0]-r[0] in (24,25,26) and moved[2]-moved[0]==r[2]-r[0]
        drag((moved[2],moved[3]),(moved[2]+25,moved[3]+15))
        assert v.roi[2]>moved[2]+20 and v.roi[3]>moved[3]+10
        v.set_roi(None);v.zoom(1.4)
        drag((180,130),(220,170))
        assert all(abs(a-b)<=1 for a,b in zip(v.roi,(180,130,220,170)))
        v.set_roi(None);assert v.roi is None
    finally:
        v.close();v.deleteLater();drain(app)


def test_pending_rule_crop_draft_clear_and_invalid_coordinate_recovery(app,dialog,image,tmp_path,monkeypatch):
    import dermademo.roi_ui as ui
    dialog.processor=None  # Explicit missing-adapter fault; production now has member2 R1.
    monkeypatch.setattr(ui,'run_formal',lambda *_a,**_k:pytest.fail('preview opened FPGA'))
    dialog.load_source(image);idle(app,dialog)
    assert dialog.source.rgb.shape==(300,400,3)
    assert dialog.run_button.isEnabled()  # Existing whole-image route remains available.
    dialog._set_roi((20,30,220,230));idle(app,dialog)
    assert dialog.crop_view.pixmap.width()==200 and dialog.crop_view.pixmap.height()==200
    assert dialog.prepared is None and not dialog.run_button.isEnabled()
    drain(app)
    assert dialog.input_view.verticalScrollBar().maximum()==0
    assert not dialog.input_view.has_image and not dialog.prepare_button.isEnabled()
    dialog.start();assert dialog.last_pair is None
    dialog.export_draft(tmp_path);idle(app,dialog)
    saved=dialog.last_draft
    assert (saved/'roi_draft.json').exists() and not (saved/'record.json').exists()
    dialog.clear_roi();idle(app,dialog)
    assert dialog.selector.roi is None and dialog.run_button.isEnabled()
    dialog.open_folder(saved);idle(app,dialog)
    assert dialog.selector.roi==(20,30,220,230)
    assert not dialog.run_button.isEnabled()
    dialog.spins[0].setValue(350);drain(app)
    assert dialog._invalid and dialog.spins[0].isEnabled()  # Must be possible to correct invalid edits.
    dialog.spins[0].setValue(10);idle(app,dialog)
    assert not dialog._invalid and dialog.selector.roi[0]==10


def test_local_roi_inference_new_frame_overlay_pending_edit_save_reopen(app,window,image,tmp_path,monkeypatch):
    import dermademo.roi_ui as ui
    from formal_mask_peer import FormalPeer,local_config
    d=ROIDialog(window,processor=FixtureROIProcessor());d.show();drain(app)
    try:
        with FormalPeer() as peer:
            monkeypatch.setattr(ui,'FormalConfig',lambda **kwargs:replace(local_config(peer),**kwargs))
            assert d.hardware_combo.isHidden()
            assert d.hardware_combo.currentData()=='p7-flash'
            monkeypatch.setattr(ui,'default_output',lambda:tmp_path/'captures')
            d.load_source(image);idle(app,d)
            d._set_roi((20,30,220,230));idle(app,d)
            d.prepare_input();idle(app,d)
            assert not peer.requests and d.input_view.has_image
            d.activateWindow();d.run_button.setFocus();drain(app)
            QTest.keyClick(d,Qt.Key.Key_Return);idle(app,d)
            assert d.last_pair is not None,d.status.text()
            first=d.last_pair;frame=first[1].metadata['frame_id']
            assert first[1].metadata['hardware_declaration']['candidate']=='formal_perf_p7_flash'
            d.hardware_combo.setCurrentIndex(d.hardware_combo.findData('v6-r1-flash'))
            assert '本机协议模拟' in d.overlay_label.text()
            assert d.overlay.pixmap.width()==400 and d.mask_view.pixmap.width()==256
            d._set_roi((50,40,300,270));idle(app,d)
            assert d.last_pair is first and d.prepared is None and not d.run_button.isEnabled()
            assert f'{frame}' in d.overlay_label.text()  # Old result must not follow the new rectangle.
            monkeypatch.setattr(ui.QFileDialog,'getExistingDirectory',lambda *_a:str(tmp_path))
            QTest.keyClick(d,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier);idle(app,d)
            record=load_record(d.last_export)
            assert record.document['schema_version']=='1.2'
            assert record.result.metadata['input_context']['roi']['bounds']==[20,30,220,230]
            assert record.result.metadata['hardware_declaration']['candidate']=='formal_perf_p7_flash'
            d.prepare_input();idle(app,d);d.start();idle(app,d)
            assert d.last_pair[1].metadata['frame_id']>frame
            assert d.last_pair[1].metadata['hardware_declaration']['candidate']=='formal_perf_p7_flash'
            peer.mask=bytes(65536)
            d.prepare_input();idle(app,d);d.start();idle(app,d)
            assert d.last_pair[1].measurements.area_px2==0
            assert '不适用（无前景）' in d.result_label.text()
            d.open_folder(d.last_export);idle(app,d)
            assert d.selector.roi==(20,30,220,230)
            assert d.last_pair[1].metadata['frame_id']==frame
            assert not d.run_button.isEnabled()  # A reopened record isn't a new prepared request.
    finally:
        if d.busy:d.cancel();idle(app,d)
        d.close();drain(app)


@pytest.mark.parametrize('mode',['light','dark','high-contrast'])
def test_roi_workspace_themes_and_source_changes(app,dialog,image,mode):
    app.fluent_theme.set_mode(mode)
    dialog.load_source(image);idle(app,dialog)
    dialog._set_roi((20,30,220,230));idle(app,dialog)
    assert dialog.selection_label.text().startswith('当前草稿')
    assert dialog.selector.accessibleName() and dialog.spins[0].accessibleName()
    dialog.clear_roi();idle(app,dialog)
    assert dialog.prepared.context['roi'] is None
    app.fluent_theme.set_mode('light')


@pytest.mark.parametrize('finish_confirmed',[False,True])
def test_roi_failed_new_frame_preserves_completed_overlay(app,window,image,tmp_path,monkeypatch,finish_confirmed):
    import dermademo.roi_ui as ui
    from dermademo.device.formal_session import FormalOutcome
    from formal_mask_peer import FormalPeer,local_config
    d=ROIDialog(window,processor=FixtureROIProcessor());d.show();drain(app)
    try:
        with FormalPeer() as peer:
            monkeypatch.setattr(ui,'FormalConfig',lambda **_k:local_config(peer))
            monkeypatch.setattr(ui,'default_output',lambda:tmp_path/'captures')
            d.load_source(image);idle(app,d);d._set_roi((20,30,220,230));idle(app,d)
            d.prepare_input();idle(app,d);d.start();idle(app,d)
        previous=d.last_pair;old_image=d.overlay.pixmap.toImage();old_parent=window.result
        report={'complete':False,'hardware_finish_confirmed':finish_confirmed,
                'manual_recovery_required':not finish_confirmed,'error':'disk failure'}
        outcome=FormalOutcome(b'',b'',report,None,tmp_path)
        monkeypatch.setattr(ui,'run_formal',lambda *_a,**_k:outcome)
        d.start();idle(app,d)
        assert d.last_pair is previous and window.result is old_parent
        assert d.overlay.pixmap.toImage()==old_image
        assert ('FINISH已确认' if finish_confirmed else '释放状态未确认') in d.status.text()
    finally:
        if d.busy:d.cancel();idle(app,d)
        d.close();drain(app)
