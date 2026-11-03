from pathlib import Path
from dataclasses import replace
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_gui import app,window
from test_roi_gui import idle,image
from test_rgb_device_gui import drain
from roi_test_support import FixtureROIProcessor
from formal_mask_peer import FormalPeer,local_config
from dermademo.roi_ui import ROIDialog
from dermademo.records import load_record
from dermademo.roi import decode_source
from dermademo.formal_results import replay_pair
from dermademo.device.formal_session import FormalOutcome


@pytest.mark.parametrize('theme',['light','dark','high-contrast'])
def test_warning_tracks_successful_frame_not_draft_or_failed_request(app,window,image,tmp_path,monkeypatch,theme):
    import dermademo.roi_ui as ui
    app.fluent_theme.set_mode(theme)
    d=ROIDialog(window,processor=FixtureROIProcessor());d.show();drain(app)
    try:
        with FormalPeer() as peer:
            peer.mask=bytes([255])+bytes(65535)
            monkeypatch.setattr(ui,'FormalConfig',lambda **_k:local_config(peer))
            monkeypatch.setattr(ui,'default_output',lambda:tmp_path/'captures')
            d.load_source(image);idle(app,d);d._set_roi((20,30,220,230));idle(app,d)
            assert '尚无成功ROI结果' in d.edge_label.text()
            d.prepare_input();idle(app,d);d.start();idle(app,d)
            first=d.last_pair;old_text=d.edge_label.text();old_main=window.edge_reminder.text()
            assert '预测接触推理框边缘' in old_text and '[20, 30, 220, 230]' in old_text
            assert d.save_button.isEnabled() and d.edge_label.property('fluentSeverity')=='warning'
            assert '本机协议模拟' in old_text and '预测接触' in old_main
            d._set_roi((50,40,300,270));idle(app,d)
            assert d.edge_label.text()==old_text and window.edge_reminder.text()==old_main
            assert d.last_pair is first
            # Save remains available even with contact and a different pending box.
            d.export_result(tmp_path);idle(app,d);saved=d.last_export
            assert load_record(saved).result.metadata['roi_edge_contact']['submitted']['roi']==[20,30,220,230]
            d.prepare_input();idle(app,d)
            real_run=ui.run_formal
            monkeypatch.setattr(ui,'run_formal',lambda *_a,**_k:FormalOutcome(b'',b'',
                  {'complete':False,'hardware_finish_confirmed':True,'error':'local save failure'},None,tmp_path))
            d.start();idle(app,d)
            assert d.edge_label.text()==old_text and window.edge_reminder.text()==old_main
            monkeypatch.setattr(ui,'run_formal',real_run)
            peer.mask=bytes(65536);d.start();idle(app,d)
            assert '未触发' in d.edge_label.text() and '不代表' in d.edge_label.text()
            assert d.edge_label.property('fluentSeverity')=='info'
            # An actual successful whole-image frame clears the ROI warning.
            d.clear_roi();idle(app,d);d.start();idle(app,d)
            assert '整图模式' in d.edge_label.text() and not window.edge_reminder.isVisible()
        d.open_folder(saved);idle(app,d)
        assert '历史结果' in d.edge_label.text() and '[20, 30, 220, 230]' in d.edge_label.text()
        window.commit_record(load_record(saved))
        assert '历史结果' in window.edge_reminder.text()
        window.clear_result();assert not window.edge_reminder.isVisible()
    finally:
        if d.busy:d.cancel();idle(app,d)
        d.close();drain(app);app.fluent_theme.set_mode('light')
