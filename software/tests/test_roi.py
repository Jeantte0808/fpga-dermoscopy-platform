from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket

import numpy as np
from PIL import Image
import pytest

from dermademo.roi import (decode_source,crop_rgb,map_mask,validate_rect,validate_roi_context,
                          save_draft,load_draft,get_roi_processor)
from dermademo.formal_input import prepare_image
from dermademo.device.formal_session import run_formal,FormalConfig
from dermademo.formal_results import live_pair
from dermademo.storage import save_result,ExportError,file_sha256
from dermademo.records import load_record
from formal_mask_peer import FormalPeer,local_config
from roi_test_support import FixtureROIProcessor


@pytest.fixture
def source(tmp_path):
    image=np.arange(31*47*3,dtype=np.uint8).reshape(31,47,3)
    path=tmp_path/'方向 原图.png';Image.fromarray(image).save(path)
    return decode_source(path)


def test_source_exif_coordinate_frame_and_snapshot(tmp_path):
    image=np.arange(7*11*3,dtype=np.uint8).reshape(7,11,3)
    exif=Image.Exif();exif[274]=6
    path=tmp_path/'rotate.png';Image.fromarray(image).save(path,exif=exif)
    s=decode_source(path);path.unlink()
    assert s.rgb.shape==(11,7,3)
    assert np.array_equal(s.rgb,np.rot90(image,3))
    assert np.array_equal(crop_rgb(s,(1,2,6,9)),np.rot90(image,3)[2:9,1:6])
    assert prepare_image(path,source_data=s.data).source_bytes==s.data


@pytest.mark.parametrize('rect',[(-1,0,5,5),(0,0,48,4),(2,2,2,4),(2,5,3,4),(0.,0,2,2),(False,0,2,2),[1,2,3]])
def test_invalid_roi_rejected(rect):
    with pytest.raises(ValueError):validate_rect(rect,47,31)


def test_upscaled_non_square_nearest_mapping(tmp_path):
    path=tmp_path/'large.png';Image.new('RGB',(803,601)).save(path)
    source=decode_source(path)
    mask=np.zeros((256,256),np.uint8);mask[20:200,30:250]=255
    out=map_mask(source,(1,5,802,600),mask)
    ys=np.floor(np.arange(595)*256/595).astype(int);xs=np.floor(np.arange(801)*256/801).astype(int)
    assert np.array_equal(out['mapped_mask.png'][5:600,1:802],mask[ys[:,None],xs[None,:]])


def test_nearest_mask_mapping_unknown_outside_and_model_grid_measurements(source):
    mask=np.zeros((256,256),np.uint8);mask[40:210,90:190]=255
    rect=(3,5,40,28);out=map_mask(source,rect,mask)
    xs=np.floor(np.arange(37)*256/37).astype(int);ys=np.floor(np.arange(23)*256/23).astype(int)
    assert np.array_equal(out['mapped_mask.png'][5:28,3:40],mask[ys[:,None],xs[None,:]])
    coverage=out['processed_region.png']
    assert np.count_nonzero(coverage)==37*23 and np.all(coverage[5:28,3:40]==255)
    assert not out['mapped_mask.png'][coverage==0].any()
    assert np.count_nonzero(mask)!=np.count_nonzero(out['mapped_mask.png'])
    assert set(np.unique(out['mapped_mask.png']))<={0,255}
    assert mask.shape==(256,256) and np.count_nonzero(mask)==17000
    empty=map_mask(source,rect,np.zeros((256,256),np.uint8))
    assert not empty['mapped_mask.png'].any()
    assert np.array_equal(empty['processed_region.png'],coverage)


def test_confirmed_default_and_test_converter_cannot_reach_real_board(source,tmp_path,monkeypatch):
    from dermademo.roi_preprocessing import ConfirmedROIProcessor
    assert isinstance(get_roi_processor(),ConfirmedROIProcessor)
    prepared=FixtureROIProcessor().prepare(source,(2,3,20,25))
    monkeypatch.setattr(socket,'socket',lambda *_a,**_k:pytest.fail('fixture reached network'))
    with pytest.raises(ValueError,match='测试夹具'):
        run_formal(prepared.rgb,FormalConfig(),tmp_path,input_context=prepared.context,source_bytes=prepared.source_bytes)
    assert not list(tmp_path.glob('formal_mask_*'))


def test_roi_source_context_tamper_and_crop_identity(source):
    p=FixtureROIProcessor().prepare(source,(2,3,20,25))
    for change in ('bounds','source_size','crop','RGB','source'):
        c=deepcopy(p.context)
        if change=='bounds':c['roi']['bounds'][0]+=1
        elif change=='source_size':c['roi']['source_size'][0]+=1
        elif change=='crop':c['preprocessing']['crop_rgb_sha256']='0'*64
        elif change=='RGB':c['preprocessing']['rgb888_sha256']='0'*64
        else:c['source_sha256']='0'*64
        with pytest.raises(ValueError):validate_roi_context(c,p.rgb,p.source_bytes)


def test_selection_draft_is_not_result_and_roundtrips_without_network(source,tmp_path,monkeypatch):
    monkeypatch.setattr(socket,'socket',lambda *_a,**_k:pytest.fail('draft opened network'))
    for rect in ((1,3,45,30),None):
        saved=save_draft(tmp_path,source,rect)
        s,r=load_draft(saved)
        assert s.data==source.data and r==rect
        assert json.loads((saved/'roi_draft.json').read_text())['no_inference_performed']
        with pytest.raises(ValueError):load_record(saved)
    (saved/'source_image.bin').write_bytes(b'bad')
    with pytest.raises(ValueError,match='校验'):load_draft(saved)


def test_two_roi_frames_clear_to_whole_and_schema12_reopen(source,tmp_path,capsys):
    pairs=[];frames=[];outcomes=[]
    state=tmp_path/'state'
    with FormalPeer() as peer:
        for rect in ((2,3,24,26),(10,7,40,29),None):
            p=FixtureROIProcessor().prepare(source,rect) if rect else prepare_image(source.path,source_data=source.data)
            outcome=run_formal(p.rgb,local_config(peer),tmp_path/'captures',state_dir=state,
                               input_context=p.context,source_bytes=p.source_bytes)
            assert outcome.report['complete'],outcome.report
            assert bytes(peer.rgb)==p.rgb
            frames.append(outcome.report['frame_id']);pairs.append(live_pair(outcome))
            outcomes.append(outcome)
    assert frames[0]<frames[1]<frames[2]
    from dermademo.device.formal_cli import main
    assert main(['replay','--capture',str(outcomes[0].directory),'--output',str(tmp_path)])==0
    cli=json.loads(capsys.readouterr().out)
    assert cli['source_kind']=='protocol_simulator' and cli['network_used'] is False
    assert load_record(Path(cli['record_directory'])).document['schema_version']=='1.2'
    for pair in pairs:
        saved=save_result(tmp_path,*pair);loaded=load_record(saved)
        assert loaded.result.metadata['source_kind']=='protocol_simulator'
        assert loaded.request.source_bytes==source.data
        assert np.array_equal(loaded.result.mask,pair[1].mask)
        assert loaded.result.measurements==pair[1].measurements
        roi=pair[1].metadata['input_context'].get('roi')
        assert loaded.document['schema_version']==('1.2' if roi else '1.1')
        if roi:
            assert loaded.document['roi_display']['outside_ROI']=='unprocessed_not_background'
            assert (saved/'mask.raw').stat().st_size==65536
            second=save_result(tmp_path,loaded.request,loaded.result,origin_record=loaded.document)
            assert load_record(second).result.measurements==loaded.result.measurements
    # Even matching updated file hashes must not permit a fake mapped/coverage image.
    saved=save_result(tmp_path,*pairs[0]);data=json.loads((saved/'record.json').read_text('utf-8'))
    Image.fromarray(np.zeros(source.rgb.shape[:2],np.uint8)).save(saved/'processed_region.png')
    data['roi_artifacts']['processed_region.png']={'bytes':(saved/'processed_region.png').stat().st_size,'sha256':file_sha256(saved/'processed_region.png')}
    (saved/'record.json').write_text(json.dumps(data),'utf-8')
    with pytest.raises(ValueError,match='映射'):load_record(saved)


def test_roi_export_failure_keeps_incomplete_marker(source,tmp_path,monkeypatch):
    import dermademo.storage as storage
    p=FixtureROIProcessor().prepare(source,(2,3,24,26))
    with FormalPeer() as peer:
        outcome=run_formal(p.rgb,local_config(peer),tmp_path/'captures',state_dir=tmp_path/'state',
                           input_context=p.context,source_bytes=p.source_bytes)
    pair=live_pair(outcome);real=storage.write_png
    def fail(path,array):
        if path.name=='mapped_overlay.png':raise OSError('disk full')
        real(path,array)
    monkeypatch.setattr(storage,'write_png',fail)
    with pytest.raises(ExportError) as e:save_result(tmp_path,*pair)
    assert (e.value.path/'INCOMPLETE.txt').exists()
    with pytest.raises(ValueError,match='INCOMPLETE'):load_record(e.value.path)
