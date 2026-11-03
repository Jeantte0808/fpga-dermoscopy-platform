"""Explicit automated GUI acceptance mode; never enabled on ordinary startup."""

import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer

from .diagnostic_logging import record_exception


class SmokeRun:
    def __init__(self, app, window, directory):
        from .gui import SAMPLE_PATH
        self.app, self.window, self.directory = app, window, Path(directory)
        self.started = time.monotonic()
        self.phase, self.record, self.failure = 0, None, None
        self.sample = SAMPLE_PATH
        self.report = {"format": "alienhome.gui-smoke", "status": "RUNNING", "checks": [],
                       "clean_machine_verified": False, "hardware_verified": False, "model_verified": False}
        self.timer = QTimer(window)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    def check(self, condition, name):
        if not condition:
            raise RuntimeError(f"smoke check failed: {name}")
        self.report["checks"].append(name)

    def advance(self):
        window = self.window
        try:
            if time.monotonic() - self.started > 90 and self.failure is None:
                raise TimeoutError("GUI smoke deadline")
            if window.busy or (window.diagnostics_dialog is not None and window.diagnostics_dialog.busy):
                return
            if window.formal_dialog is not None and window.formal_dialog.busy:
                return
            if window.roi_dialog is not None and window.roi_dialog.busy:
                return
            lab=getattr(window,'hardware_lab_dialog',None)
            if lab is not None and lab.busy:return
            if any(dialog is not None and dialog.busy for dialog in (window.validation_dialog, window.queue_dialog, window.ai_dialog, window.review_dialog,window.report_dialog,window.cpu_settings_dialog)):
                return
            if self.failure is not None:
                return self.finish(False)
            if self.phase == 0:
                from PySide6.QtWidgets import QLabel
                self.check('Demo' not in window.windowTitle() and window.findChild(QLabel, 'badge') is None,
                           'delivery_brand_without_demo_badge')
                self.check(all(panel.detail_label.isHidden() for panel in window.panels),
                           'delivery_four_images_without_footer_clutter')
                self.check(not window.sample_button.isVisible() and not window.device_button.isVisible()
                           and not window.backend_combo.isVisible() and not window.parameters_widget.isVisible(),
                           'delivery_no_demo_tools_in_primary_workspace')
                window.enable_reference_mode()
                window.load_path(self.sample, is_synthetic=True)
            elif self.phase == 1:
                self.check(window.image is not None, "sample_import")
                window.start_processing()
            elif self.phase == 2:
                self.check(window.result is not None and all(panel.view.has_image for panel in window.panels), "processing_four_images")
                self.saved_result = window.result
                window.clip_spin.setValue(3)
                self.check(window.result is self.saved_result and window.parameters_pending, "parameters_wait_for_processing")
                window.export_to(self.directory)
            elif self.phase == 3:
                self.record = window.last_export
                self.check(self.record is not None and (self.record / "record.json").exists(), "save_record")
                window.start_open_record(self.record)
            elif self.phase == 4:
                self.check(window.opened_record is not None and window.result is not None, "reopen_record")
                window.open_diagnostics()
                window.diagnostics_dialog.export_to(self.directory)
            elif self.phase == 5:
                self.check(window.diagnostics_dialog.last_export is not None, "diagnostic_export")
                window.diagnostics_dialog.close()
            elif 6 <= self.phase <= 8:
                self.mode = ("light", "dark", "high-contrast")[self.phase - 6]
                self.app.fluent_theme.set_mode(self.mode)
                # Capture only on the next tick so palette/layout changes have painted.
                self.phase += 10
                return
            elif 16 <= self.phase <= 18:
                self.check(window.grab().save(str(self.directory / f"gui-{self.mode}.png")), f"theme_{self.mode}")
                self.phase -= 10
            elif self.phase == 9:
                self.phase = 100
                return
            elif self.phase == 100:
                window.open_formal_device()
                window.formal_dialog.mode_combo.setCurrentIndex(window.formal_dialog.mode_combo.findData('replay'))
                window.formal_dialog.start()
            elif self.phase == 101:
                self.check(window.result.capabilities.key == 'fpga_formal_v5'
                           and window.result.metadata['display_mode'] == 'offline_replay'
                           and all(panel.view.has_image for panel in window.panels), 'formal_replay_four_views')
                self.check(abs(window.result.timings_ms['fpga_graph'] - 619.28689) < 1e-8,
                           'formal_graph_and_e2e_distinct')
                window.formal_dialog.close()
                window.export_to(self.directory)
            elif self.phase == 102:
                self.check((window.last_export / 'input.rgb').exists()
                           and (window.last_export / 'mask.raw').exists(), 'formal_raw_export')
                window.start_open_record(window.last_export)
            elif 103 <= self.phase <= 105:
                self.check(window.result.capabilities.key == 'fpga_formal_v5', 'formal_reopen')
                self.check(not window.technical_details.isVisible() and not window.generic_parameters.isVisible(),
                           'delivery_technical_details_collapsed')
                window.technical_toggle.click()
                self.check(window.generic_parameters.isVisible() and 'frame ' in window.technical_origin.text(),
                           'delivery_technical_details_available')
                window.technical_toggle.click()
                self.mode = ('light', 'dark', 'high-contrast')[self.phase - 103]
                self.app.fluent_theme.set_mode(self.mode)
                self.phase += 10
                return
            elif 113 <= self.phase <= 115:
                self.check(window.grab().save(str(self.directory / f'formal-gui-{self.mode}.png')),
                           f'formal_theme_{self.mode}')
                window.open_formal_device()
                self.check(window.formal_dialog.grab().save(str(self.directory / f'formal-dialog-{self.mode}.png')),
                           f'formal_dialog_theme_{self.mode}')
                window.formal_dialog.close()
                self.phase -= 10
            elif self.phase == 106:
                from .resources import resource_path
                window.open_formal_device()
                dialog = window.formal_dialog
                dialog.mode_combo.setCurrentIndex(1)
                dialog.source_combo.setCurrentIndex(2)
                self.previous = window.result
                dialog.prepare_selected_image(resource_path('full_image_root') / 'samples/01_ISIC_0000000/ISIC_0000000.jpg')
            elif self.phase == 107:
                dialog = window.formal_dialog
                self.check(dialog.prepared_image is not None and dialog.preview.has_image, 'formal_image_prepared')
                self.check(window.result is self.previous, 'formal_preview_does_not_refresh_result')
            elif 108 <= self.phase <= 110:
                self.mode = ('light', 'dark', 'high-contrast')[self.phase - 108]
                self.app.fluent_theme.set_mode(self.mode)
                self.phase += 20
                return
            elif 128 <= self.phase <= 130:
                self.check(window.formal_dialog.grab().save(str(self.directory / f'full-image-dialog-{self.mode}.png')),
                           f'full_image_dialog_theme_{self.mode}')
                self.phase -= 20
                if self.phase == 110:
                    window.formal_dialog.close()
            elif self.phase == 111:
                self.phase=200
                return
            elif self.phase == 200:
                from .resources import resource_path
                window.open_roi_device()
                self.roi_previous=window.result
                window.roi_dialog.load_source(resource_path('full_image_root') / 'samples/01_ISIC_0000000/ISIC_0000000.jpg')
            elif self.phase == 201:
                self.check(window.roi_dialog.source is not None,'roi_original_oriented')
                window.roi_dialog._set_roi((100,80,700,600))
            elif self.phase == 202:
                d=window.roi_dialog
                self.check(d.crop_view.pixmap.width()==600 and not d.run_button.isEnabled()
                           and d.prepared is None and d.prepare_button.isEnabled(),'roi_explicit_prepare_and_actual_crop')
                d.export_draft(self.directory)
            elif self.phase == 203:
                self.check(window.roi_dialog.last_draft is not None,'roi_draft_save_no_inference')
                window.roi_dialog.open_folder(window.roi_dialog.last_draft)
            elif self.phase == 204:
                self.check(window.roi_dialog.selector.roi==(100,80,700,600)
                           and window.result is self.roi_previous,'roi_draft_reopen_preserves_last_frame')
                window.roi_dialog.prepare_input()
            elif 205 <= self.phase <= 207:
                self.check(window.roi_dialog.prepared is not None and window.roi_dialog.run_button.isEnabled(),
                           'roi_confirmed_input_prepared_no_network')
                self.mode=('light','dark','high-contrast')[self.phase-205]
                self.app.fluent_theme.set_mode(self.mode)
                self.phase+=20
                return
            elif 225 <= self.phase <= 227:
                self.check(window.roi_dialog.grab().save(str(self.directory/f'roi-dialog-{self.mode}.png')),
                           f'roi_theme_{self.mode}')
                self.phase-=20
            elif self.phase == 208:
                window.roi_dialog.clear_roi()
            elif self.phase == 209:
                self.check(window.roi_dialog.prepared is not None and window.roi_dialog.run_button.isEnabled(),
                           'roi_clear_returns_whole_image')
                window.roi_dialog.close()
            elif self.phase == 210:
                window.open_cpu_baseline()
            elif 211 <= self.phase <= 213:
                self.mode=('light','dark','high-contrast')[self.phase-211]
                self.app.fluent_theme.set_mode(self.mode)
                self.phase+=30
                return
            elif 241 <= self.phase <= 243:
                self.check(window.cpu_dialog.grab().save(str(self.directory/f'cpu-dialog-{self.mode}.png')),
                           f'cpu_theme_{self.mode}')
                self.phase-=30
                if self.phase==213:window.cpu_dialog.close()
            elif self.phase == 214:
                import os
                record=os.environ.get('ALIENHOME_SMOKE_EDGE_RECORD')
                if not record:
                    self.phase=300
                    return
                self.edge_smoke_record=Path(record)
                window.open_roi_device();window.roi_dialog.open_folder(self.edge_smoke_record)
            elif 215 <= self.phase <= 217:
                label=window.roi_dialog.edge_label
                self.check('预测接触推理框边缘' in label.text() and '历史结果' in label.text(),
                           'historical_edge_warning_actual_raw_mask')
                self.mode=('light','dark','high-contrast')[self.phase-215]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=30;return
            elif 245 <= self.phase <= 247:
                self.check(window.roi_dialog.grab().save(str(self.directory/f'roi-edge-{self.mode}.png')),
                           f'roi_edge_theme_{self.mode}')
                self.phase-=30
                if self.phase==217:window.roi_dialog.close()
            elif self.phase == 218:
                from .records import load_record
                window.commit_record(load_record(self.edge_smoke_record))
                self.check('历史结果' in window.edge_reminder.text() and '预测接触' in window.edge_reminder.text(),
                           'main_window_historical_edge_warning')
                self.check(window.grab().save(str(self.directory/'main-roi-edge.png')),'main_edge_warning_screenshot')
            elif self.phase == 219:
                self.phase=300
                return
            elif self.phase == 300:
                from .formal_results import replay_pair
                window.commit_formal_result(replay_pair())
                self.analysis_previous=window.result
                window.open_validation()
            elif self.phase == 301:
                d=window.validation_dialog
                self.check(d.pair is not None and d.comparison is not None and d.comparison['byte_exact'],
                           'teaching_fixed_reference_readonly')
                self.check(window.result is self.analysis_previous,'teaching_preserves_workbench')
                d.history_button.click()
            elif self.phase == 302:
                d=window.validation_dialog
                self.check(d.history_model.rowCount()==2 and d.history_model.item(0,3).text()=='9.29%',
                           'teaching_p7_five_frame_history_recomputed')
                self.check('原报告旧V6' in d.history_note.toPlainText(),'teaching_history_keeps_old_labels')
            elif 303 <= self.phase <= 305:
                self.mode=('light','dark','high-contrast')[self.phase-303]
                self.app.fluent_theme.set_mode(self.mode)
                window.validation_dialog.tabs.setCurrentIndex(self.phase-303)
                self.phase+=20
                return
            elif 323 <= self.phase <= 325:
                self.check(window.validation_dialog.grab().save(str(self.directory/f'validation-{self.mode}.png')),
                           f'teaching_theme_{self.mode}')
                self.phase-=20
                if self.phase==305:window.validation_dialog.close()
            elif self.phase == 306:
                from .resources import resource_path
                window.open_formal_queue()
                d=window.queue_dialog
                d.output_parent=self.directory/'prepared_queue'
                root=resource_path('full_image_root')
                d.add_paths([root/'samples/01_ISIC_0000000/ISIC_0000000.jpg',
                             root/'samples/02_ISIC_0000017/ISIC_0000017.jpg'])
            elif self.phase == 307:
                d=window.queue_dialog
                if len(d.inputs)==2 and not d.preview.has_image:
                    return  # Wait for the queued selection/preview task, bounded by smoke deadline.
                self.check(len(d.inputs)==2 and d.session is None and d.preview.has_image,
                           'queue_freeze_preview_without_network')
                self.check(window.result is self.analysis_previous,'queue_preparation_preserves_workbench')
            elif 308 <= self.phase <= 310:
                self.mode=('light','dark','high-contrast')[self.phase-308]
                self.app.fluent_theme.set_mode(self.mode)
                self.phase+=20
                return
            elif 328 <= self.phase <= 330:
                self.check(window.queue_dialog.grab().save(str(self.directory/f'queue-{self.mode}.png')),
                           f'queue_theme_{self.mode}')
                self.phase-=20
                if self.phase==310:self.phase=330
            elif self.phase == 331:
                from .storage import save_result
                from .analysis_identity import result_identity
                d=window.queue_dialog
                record_parent=self.directory/'pdf-source-record';record_parent.mkdir()
                pair=(window.request,window.result)
                record=save_result(record_parent,*pair)
                self.ai_source_record=record
                identity=result_identity(pair)
                snapshot=dict(status='completed', inputs=[dict(item_id='historical',filename=pair[0].source_path.name)],
                              attempts=[dict(item_id='historical',filename=pair[0].source_path.name,status='success',attempt=1,
                                             frame_id=identity['frame_id'],identity=identity,record_directory=str(record))])
                d._export_pdf(snapshot,self.directory/'historical-batch.pdf',True)
            elif self.phase == 332:
                d=window.queue_dialog
                self.check(d.pdf_path is not None and d.pdf_path.read_bytes().startswith(b'%PDF'), 'batch_pdf_saved_frame_export')
                self.check(d.pdf_view_button.isEnabled() and d.pdf_folder_button.isEnabled(), 'batch_pdf_open_actions')
                self.check(d.hardware_combo.isHidden() and d.hardware_combo.currentData()=='p7-flash', 'flash_deployment_without_daily_choice')
                d.close()
            elif self.phase == 333:
                window.open_ai()
                self.check(window.ai_dialog is not None,'ai_entry_saved_historical_frame')
                self.app.fluent_theme.set_mode('light')
            elif self.phase == 334:
                d=window.ai_dialog
                self.check(not d.busy and d.task is None,'ai_open_does_not_call_cloud')
                self.check(d.key.echoMode()==d.key.EchoMode.Password,'ai_key_password_display')
                self.check(d.grab().save(str(self.directory/'ai-dialog.png')),'ai_dialog_screenshot')
            elif self.phase == 335:
                from .ai_service import run_analysis
                answer=dict(image_observations=['离线夹具，未调用云端。'],segmentation_review='供界面检查。',review_points=['人工复核。'],teaching_summary='教学示例。',limitations=['未实际调用云端。'])
                raw=json.dumps(dict(model='offline-smoke',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(answer,ensure_ascii=False)))])).encode()
                window.ai_dialog.start_job('cloud',lambda cancel,progress:run_analysis(self.ai_source_record,'https://dashscope.aliyuncs.com','OFFLINE-TEST-KEY',post=lambda *_:raw,historical=True,cancel=cancel,progress=progress))
            elif self.phase == 336:
                d=window.ai_dialog
                self.check(d.task is not None and d.pdf_button.isEnabled(),'ai_offline_response_to_pdf')
                self.check(json.loads((d.task/'analysis_report.json').read_text('utf-8'))['cloud_transport']=='offline_test_fixture','ai_smoke_no_paid_request')
                d.regenerate()
            elif self.phase == 337:
                self.check(window.ai_dialog.pdf_button.isEnabled(),'ai_pdf_regeneration_without_cloud')
                window.ai_dialog.close()
            elif self.phase == 338:
                from .records import load_record
                from hashlib import sha256
                window.commit_record(load_record(self.ai_source_record))
                self.review_original_sha=sha256(window.result.mask.tobytes()).hexdigest()
                window.open_review()
            elif self.phase == 339:
                d=window.review_dialog
                self.check(d.editor is not None and not d.busy,'review_workstation_prepared_no_network')
                d.editor.line((10,10),(14,14),radius=2,value=255 if d.editor.mask[10,10]==0 else 0)
                d.editor.end();d.changed()
            elif self.phase == 340:
                from hashlib import sha256
                d=window.review_dialog
                self.check(d.editor.changed_pixels>0 and sha256(window.result.mask.tobytes()).hexdigest()==self.review_original_sha,
                           'manual_revision_preserves_raw_fpga_mask')
                d.note.setText('自动化离线界面验收，不是实际人工诊断或新增实板测试。')
                d.texts['summary'].setPlainText('自动化离线修订演示；只验证独立副本及保存流程。')
                d.save()
            elif self.phase == 341:
                from .review import load_review
                from .review_pdf import valid_review_pdf
                d=window.review_dialog
                self.check(d.saved is not None and load_review(d.saved)['document']['changed_pixels']>0
                           and valid_review_pdf(d.saved),'manual_review_saved_pdf_frame_bound')
            elif 342 <= self.phase <= 344:
                self.mode=('light','dark','high-contrast')[self.phase-342]
                self.app.fluent_theme.set_mode(self.mode);window.review_dialog.tabs.setCurrentIndex(self.phase-342)
                self.phase+=20;return
            elif 362 <= self.phase <= 364:
                self.check(window.review_dialog.grab().save(str(self.directory/f'review-{self.mode}.png')),
                           f'review_theme_{self.mode}')
                self.phase-=20
                if self.phase==344:window.review_dialog.close()
            elif self.phase==345:
                self.check(window.input_quality_banner.isVisible(),'home_input_quality_on_saved_frame')
                import os
                self.smoke_cpu=os.environ.get('ALIENHOME_SMOKE_CPU_PYTHON')
                window.open_cpu_settings(self.smoke_cpu)
                if self.smoke_cpu:window.cpu_settings_dialog.inspect()
            elif 346<=self.phase<=348:
                self.mode=('light','dark','high-contrast')[self.phase-346]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 366<=self.phase<=368:
                d=window.cpu_settings_dialog
                self.check(not d.busy and (d.checked is not None if self.smoke_cpu else not d.save.isEnabled()),'cpu_path_dependency_check_'+self.mode)
                self.check(d.grab().save(str(self.directory/f'cpu-config-{self.mode}.png')),'cpu_config_theme_'+self.mode)
                self.phase-=20
                if self.phase==348:window.cpu_settings_dialog.close()
            elif self.phase==349:window.open_report()
            elif self.phase==350:
                d=window.report_dialog
                self.check(d.folder is not None and d.revision.count()>=2,'unified_report_lists_same_frame_revisions')
                d.generate.click()
            elif self.phase==351:
                d=window.report_dialog
                self.check(d.pdf is not None and d.open_pdf.isEnabled(),'unified_report_gui_local_export: '+d.status.text())
                meta=json.loads(d.pdf.with_suffix('.pdf.json').read_text('utf-8'))
                self.check(meta['records'][0]['review'] is not None and meta['records'][0]['ai_status']=='not_run',
                           'unified_report_rejects_AI_from_other_revision')
            elif 352<=self.phase<=354:
                self.mode=('light','dark','high-contrast')[self.phase-352]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 372<=self.phase<=374:
                self.check(window.report_dialog.grab().save(str(self.directory/f'unified-report-{self.mode}.png')),'unified_report_theme_'+self.mode)
                self.phase-=20
                if self.phase==354:window.report_dialog.close()
            elif self.phase==355:
                import os
                self.demo100_record=os.environ.get('ALIENHOME_SMOKE_DEMO100_RECORD')
                if not self.demo100_record:return self.finish(True)
                window.start_open_record(self.demo100_record);self.app.fluent_theme.set_mode('light')
            elif self.phase==356:
                self.check('0字节差异' in window.reference_summary.text() and '0.954406' in window.reference_summary.text(),
                           'demo100_main_automatic_raw_comparison')
                self.demo100_result=window.result
                self.check(window.grab().save(str(self.directory/'demo100-main.png')),'demo100_main_screenshot')
                window.reference_compare_button.click()
            elif self.phase==357:
                d=window.validation_dialog
                self.check(d.reference_type.count()==2 and d.comparison['byte_exact'],'demo100_INT8_automatic_reference')
                self.check(d.grab().save(str(self.directory/'demo100-int8.png')),'demo100_INT8_screenshot')
                d.reference_type.setCurrentIndex(1)
                self.check(abs(d.comparison['dice']-.9544062795746813)<1e-12,'demo100_annotation_automatic_score')
            elif 358<=self.phase<=360:
                self.mode=('light','dark','high-contrast')[self.phase-358]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 378<=self.phase<=380:
                self.check(window.validation_dialog.grab().save(str(self.directory/f'demo100-annotation-{self.mode}.png')),
                           'demo100_annotation_theme_'+self.mode)
                self.check(window.result is self.demo100_result,'demo100_comparison_preserves_original_'+self.mode)
                self.phase-=20
                if self.phase==360:window.validation_dialog.close();self.phase=380
            elif self.phase==381:
                window.open_hardware_lab();window.hardware_lab_dialog.current()
            elif self.phase==382:
                d=window.hardware_lab_dialog
                self.check(d.snapshot is not None and d.stats_model.rowCount()>0,'hardware_lab_reads_raw_frame')
                self.check('尚未冻结' in d.stats_notice.text(),'hardware_statistics_not_claimed_as_available')
                d.pin_baseline();d.show_evidence(d.snapshot)
                self.check('不能计算正式提速' in d.compare_label.text(),'hardware_lab_refuses_simulated_speedup')
            elif 383<=self.phase<=385:
                self.mode=('light','dark','high-contrast')[self.phase-383]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 403<=self.phase<=405:
                d=window.hardware_lab_dialog
                for tab,name in ((1,'bottleneck'),(2,'mask-statistics')):
                    d.tabs.setCurrentIndex(tab);self.app.processEvents()
                    self.check(d.grab().save(str(self.directory/f'hardware-{name}-{self.mode}.png')),'hardware_'+name+'_'+self.mode)
                self.phase-=20
                if self.phase==385:window.hardware_lab_dialog.close()
            elif self.phase==386:
                import os
                self.edge_record=os.environ.get('ALIENHOME_SMOKE_EDGE_RECORD')
                if not self.edge_record:return self.finish(True)
                window.open_roi_device();window.roi_dialog.open_folder(Path(self.edge_record))
            elif self.phase==387:
                d=window.roi_dialog
                self.check(d.last_pair is not None and not hasattr(d,'expand_button'),'roi_manual_without_percentage_controls')
                self.expand_frame=d.last_pair[1].metadata['frame_id']
                d._set_roi((0,0,min(850,d.source.rgb.shape[1]),min(730,d.source.rgb.shape[0])))
            elif self.phase==388:
                d=window.roi_dialog
                self.check(d.prepared is None and d.last_pair[1].metadata['frame_id']==self.expand_frame,'roi_manual_only_draft_no_inference')
                d.prepare_input()
            elif self.phase==389:
                d=window.roi_dialog
                self.check(d.prepared is not None and len(d.prepared.rgb)==196608,'roi_manual_confirmed_preprocessing_preview')
                self.check(d.last_pair[1].metadata['frame_id']==self.expand_frame,'roi_preview_preserves_old_frame')
            elif 390<=self.phase<=392:
                self.mode=('light','dark','high-contrast')[self.phase-390]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 410<=self.phase<=412:
                self.check(window.roi_dialog.grab().save(str(self.directory/f'roi-manual-{self.mode}.png')),'roi_manual_theme_'+self.mode)
                self.phase-=20
                if self.phase==392:window.roi_dialog.close();self.phase=459
            elif self.phase==460:
                import os
                folder=os.environ.get('ALIENHOME_SMOKE_EXPERIMENT')
                if not folder:return self.finish(True)
                window.open_hardware_lab();window.hardware_lab_dialog.load_folder(Path(folder))
            elif self.phase==461:
                d=window.hardware_lab_dialog
                from .hardware_lab import successful_frames
                rows=successful_frames(d.document);self.experiment_last=rows[-1]['trial_id']
                self.experiment_folder=d.folder;self.experiment_summary=json.dumps(d.document['summary'],sort_keys=True)
                self.check(d.snapshot['trial_id']==self.experiment_last,'experiment_auto_last_formal_frame')
                self.check(d.stage_model.rowCount()==82 and d.stats_model.rowCount()>0,'experiment_auto_stage_and_pc_statistics')
                self.check(d.baseline is None,'experiment_no_automatic_speedup_baseline')
                d.frame_combo.setCurrentIndex(0)
            elif self.phase==462:
                d=window.hardware_lab_dialog
                self.check(d.snapshot['trial_id']==d.frame_combo.itemData(0),'experiment_success_frame_switch')
                self.check(d.pdf_button.isEnabled() and d.open_pdf.isEnabled() and d.open_dir.isEnabled(),'experiment_pdf_directory_preserved')
            elif 463<=self.phase<=465:
                self.mode=('light','dark','high-contrast')[self.phase-463]
                self.app.fluent_theme.set_mode(self.mode);self.phase+=20;return
            elif 483<=self.phase<=485:
                d=window.hardware_lab_dialog
                for tab,name in ((0,'summary'),(1,'stages'),(2,'pc-statistics')):
                    d.tabs.setCurrentIndex(tab);self.app.processEvents()
                    self.check(d.grab().save(str(self.directory/f'experiment-context-{name}-{self.mode}.png')),'experiment_context_'+name+'_'+self.mode)
                self.phase-=20
                if self.phase==465:self.phase=489
            elif self.phase==490:
                window.hardware_lab_dialog.load_folder(Path(self.demo100_record))
            elif self.phase==491:
                d=window.hardware_lab_dialog
                self.check(d.folder==self.experiment_folder and json.dumps(d.document['summary'],sort_keys=True)==self.experiment_summary,'experiment_context_survives_external_record')
                self.check(d.stage_model.rowCount()==0 and d.frame_combo.currentIndex()==-1,'summary_external_clears_previous_stages')
                d.frame_combo.setCurrentIndex(d.frame_combo.count()-1)
            elif self.phase==492:
                d=window.hardware_lab_dialog
                self.check(d.stage_model.rowCount()==82 and d.snapshot['trial_id']==self.experiment_last,'experiment_frame_returns_without_import')
                d.export_pdf()
            elif self.phase==493:
                d=window.hardware_lab_dialog
                self.check(d.folder==self.experiment_folder and d.snapshot['trial_id']==self.experiment_last and d.open_pdf.isEnabled(),'experiment_pdf_reexport_preserves_frame')
                d.close()
            else:
                return self.finish(True)
            self.phase += 1
        except Exception as exc:
            record_exception("gui_smoke_failed", exc, stage="release_smoke")
            self.failure = f"{type(exc).__name__}: {exc}"
            self.report["error"] = self.failure

    def finish(self, success):
        self.timer.stop()
        self.report["status"] = "PASS" if success else "FAIL"
        self.report["elapsed_seconds"] = time.monotonic() - self.started
        (self.directory / "gui-smoke.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.window.review_dialog is not None:self.window.review_dialog.dirty=False
        self.window.close()
        self.app.exit(0 if success else 1)
