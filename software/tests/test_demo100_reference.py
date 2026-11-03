"""Offline reference integration. Fixture masks are never evidence of board inference."""
from copy import deepcopy
from hashlib import sha256
from itertools import count
import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtPdf import QPdfDocument

from dermademo.reference_library import references,compare,verify_library,catalog,brief,read_decl
from dermademo.resources import resource_path
from dermademo.formal_input import prepare_image,PreparedImage
from dermademo.formal_results import replay_pair,live_pair
from dermademo.device.formal_session import FormalOutcome,FormalConfig
from dermademo.formal_queue import FormalQueue,freeze_input
from dermademo.storage import save_result
from dermademo.records import load_record
from dermademo.analysis_identity import result_identity
from dermademo.unified_report import export_single,export_batch_pdf
from dermademo.review import save_review
from test_gui import app,window
from test_teaching_gui import settled
from test_rgb_device_gui import drain

SOURCE=Path('tests/fixtures/demo100/ISIC_0000365.jpg')
FRAMES=count(3100000001)


def outcome(prepared,directory,mask=None):
    # Explicit local test double, using delivered reference bytes rather than claiming hardware execution.
    root=resource_path('reference_library')
    mask=mask if mask is not None else (root/'ISIC_0000365/int8_reference.bin').read_bytes()
    report=deepcopy(replay_pair()[1].metadata['wire_report']);frame=next(FRAMES)
    report.update(origin='protocol_simulator',board=['127.0.0.1',5000],pc=['127.0.0.1',6102],
        input_context=prepared.context,input_sha256=sha256(prepared.rgb).hexdigest(),mask_sha256=sha256(mask).hexdigest(),
        hardware_finish_confirmed=True,local_artifacts_complete=True,frame_id=frame,official_fixed_sample_exact=False)
    report['last_reply'].update(frame=frame,result_frame=frame)
    return FormalOutcome(prepared.rgb,mask,report,None,directory,prepared.source_bytes)


def sample_pair(tmp_path,mask=None):
    return live_pair(outcome(prepare_image(SOURCE),tmp_path,mask))


def test_all_100_assets_mapping_and_existing_metrics_are_rechecked():
    verified=verify_library()
    assert verified['pairs']==100 and verified['declarations']==200 and verified['physical_FPGA_tests']==0


def test_365_actual_preprocessing_and_raw_mask_scores_match_handoff(tmp_path):
    p=prepare_image(SOURCE);e=json.loads((resource_path('reference_library')/'CURRENT_FRAME_COMPARISON.json').read_text('utf8'))
    assert sha256(p.source_bytes).hexdigest()==e['identity']['source_sha256']
    assert sha256(p.rgb).hexdigest()==e['identity']['input_rgb_sha256']
    pair=live_pair(outcome(p,tmp_path));report=compare(pair)
    assert report['int8']['byte_exact'] and report['int8']['different_bytes']==0
    assert report['annotation']['dice']==pytest.approx(.9544062795746813,abs=1e-12)
    assert report['annotation']['iou']==pytest.approx(.9127888403790864,abs=1e-12)
    assert not report['is_new_board_acceptance'] and 'dice' not in report['int8']
    assert pair[1].metadata['source_kind']=='protocol_simulator'


@pytest.mark.parametrize('field',['source_sha256','preprocess_version','model_version','roi','rgb'])
def test_same_filename_wrong_identity_or_ROI_is_unavailable(tmp_path,field):
    pair=sample_pair(tmp_path);meta=pair[1].metadata
    if field=='source_sha256':meta['input_context']['source_sha256']='0'*64
    elif field=='preprocess_version':meta['input_context']['preprocessing']['version']='different'
    elif field=='model_version':meta['model_version']='different_model'
    elif field=='roi':meta['input_context']['roi']=dict(bounds=[0,0,256,256])
    else:
        image=pair[0].image_bgr.copy();image[0,0,0]^=1
        from dataclasses import replace
        pair=(replace(pair[0],image_bgr=image),pair[1]);meta['input_sha256']=sha256(np.ascontiguousarray(image[:,:,::-1]).tobytes()).hexdigest()
    report=compare(pair)
    assert report['int8']['status']==report['annotation']['status']=='unavailable'
    assert 'dice' not in report['annotation']


def test_renamed_original_still_matches_and_one_mask_pixel_is_a_real_mismatch(tmp_path):
    p=tmp_path/'renamed.png';p.write_bytes(SOURCE.read_bytes())
    pair=live_pair(outcome(prepare_image(p),tmp_path))
    assert compare(pair)['annotation']['status']=='matched'
    changed=pair[1].mask.copy();changed[0,0]=255-changed[0,0]
    report=compare(sample_pair(tmp_path,changed.tobytes()))
    assert not report['int8']['byte_exact'] and report['int8']['different_bytes']==1
    assert report['annotation']['dice']!=pytest.approx(.9544062795746813,abs=1e-12)


@pytest.mark.parametrize('asset',['int8_reference.bin','annotation_grid256.bin','01_INT8参考.json'])
def test_tampered_assets_do_not_produce_stale_scores(tmp_path,asset):
    import shutil
    root=resource_path('reference_library');copy=tmp_path/'catalog';shutil.copytree(root,copy)
    file=copy/'ISIC_0000365'/asset;raw=file.read_bytes();file.write_bytes(bytes([raw[0]^1])+raw[1:])
    result=compare(sample_pair(tmp_path),root=copy)
    kind='annotation' if asset.startswith('annotation') else 'int8'
    assert result[kind]['status']=='unavailable'
    assert 'dice' not in result[kind] and 'byte_exact' not in result[kind]


def test_home_summary_compare_toggle_and_new_frame_clear_old_scores(app,window,tmp_path):
    pair=sample_pair(tmp_path);raw=pair[1].mask.tobytes();window.commit_formal_result(pair)
    assert '0字节差异' in window.reference_summary.text() and '0.954406' in window.reference_summary.text()
    window.reference_compare_button.click();d=window.validation_dialog
    settled(app,d,lambda:d.comparison is not None)
    assert d.reference_type.currentData()=='int8' and d.comparison['byte_exact']
    d.reference_type.setCurrentIndex(1)
    assert d.reference['kind']=='annotation' and '0.954406' in d.comparison_label.text()
    assert all(v.has_image for v in d.views) and pair[1].mask.tobytes()==raw
    d.reference_type.setCurrentIndex(0);assert d.comparison['byte_exact']
    d.close();drain(app)
    other=sample_pair(tmp_path);other[1].metadata['input_context']['preprocessing']['version']='no_match'
    from dermademo.reference_library import update
    update(other);window.commit_formal_result(other)
    assert '0.954406' not in window.reference_summary.text() and window.reference_summary.text().count('不可用')==2


def test_queue_per_frame_record_and_pdf_reference_scores(app,tmp_path,monkeypatch):
    p=prepare_image(SOURCE);items=[freeze_input(p.path,tmp_path/'snap',prepared=p) for _ in range(2)]
    def wire(rgb,config,parent,**kwargs):
        value=PreparedImage(Path(kwargs['input_context']['source_filename']),kwargs['source_bytes'],rgb,kwargs['input_context'])
        return outcome(value,tmp_path)
    monkeypatch.setattr('dermademo.formal_queue.run_formal',wire)
    q=FormalQueue(items,FormalConfig(),tmp_path/'queue');a,b=q.step(),q.step()
    for step in (a,b):
        document=json.loads((Path(step.row['record_directory'])/'record.json').read_text('utf8'))
        scored=document['processing']['reference_comparison']
        assert scored['identity']==step.row['identity'] and scored['identity']['frame_id']==step.row['frame_id']
        assert scored['annotation']['dice']==step.row['reference_comparison']['annotation']['dice']
        assert scored['int8']['different_bytes']==0
    export_batch_pdf(q.summary(),tmp_path/'batch.pdf')
    d=QPdfDocument();assert d.load(str(tmp_path/'batch.pdf'))==QPdfDocument.Error.None_
    text=''.join(d.getAllText(i).text() for i in range(d.pageCount()));d.close()
    assert '0.954406' in text and '0.912789' in text and '高分精选' in text
    assert '整数一致性' in text and '人工标注对照' in text


def test_reopen_old_record_recalculates_without_rewriting_and_revision_never_replaces_scores(app,tmp_path):
    pair=sample_pair(tmp_path);folder=save_result(tmp_path,*pair)
    path=folder/'record.json';document=json.loads(path.read_text('utf8'));document['processing'].pop('reference_comparison')
    path.write_text(json.dumps(document),'utf8');before=path.read_bytes()
    r=load_record(folder);assert r.result.metadata['reference_comparison']['annotation']['status']=='matched'
    assert path.read_bytes()==before and 'reference_comparison' not in r.document['processing']
    changed=np.zeros_like(r.result.mask);review=save_review(folder,changed)
    result=export_single(folder,review=review)
    assert result['records'][0]['reference_comparison']['annotation']['dice']==pytest.approx(.9544062795746813)
    d=QPdfDocument();d.load(result['pdf']);text=''.join(d.getAllText(i).text() for i in range(d.pageCount()));d.close()
    assert '所选人工修订未参与' in text and '0.954406' in text and path.read_bytes()==before


def test_real_ROI_preprocessing_cannot_use_whole_image_annotation(tmp_path):
    from dermademo.roi import decode_source,get_roi_processor
    source=decode_source(SOURCE);prepared=get_roi_processor().prepare(source,(16,16,200,200))
    report=compare(live_pair(outcome(prepared,tmp_path)))
    assert report['annotation']['status']=='unavailable' and report['int8']['status']=='unavailable'


def test_readonly_CLI_compares_saved_record_without_network(tmp_path):
    import subprocess,sys
    folder=save_result(tmp_path,*sample_pair(tmp_path));before=(folder/'record.json').read_bytes()
    process=subprocess.run([sys.executable,'-m','dermademo.tools_cli','references','compare','--record',str(folder)],capture_output=True,timeout=30)
    assert process.returncode==0,process.stderr
    report=json.loads(process.stdout);assert report['annotation']['dice']==pytest.approx(.9544062795746813)
    assert report['int8']['different_bytes']==0 and (folder/'record.json').read_bytes()==before
