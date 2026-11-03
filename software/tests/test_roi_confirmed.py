from hashlib import sha256
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import pytest

from dermademo.roi import decode_source,get_roi_processor,validate_roi_context,map_mask
from dermademo.roi_preprocessing import verify_roi_preprocessing
from dermademo.formal_preprocessing import JPEG_OPTIONS,prepare_input
from dermademo.resources import resource_path
from dermademo.device.formal_session import run_formal
from dermademo.formal_results import live_pair
from dermademo.storage import save_result
from dermademo.records import load_record
from formal_mask_peer import FormalPeer,local_config


def test_three_delivered_goldens_new_frames_mapping_save_reopen(tmp_path):
    check=verify_roi_preprocessing();assert len(check['cases'])==3
    root=resource_path('roi_root');frames=[]
    for row in json.loads((root/'samples.json').read_text('utf-8')):
        folder=root/row['directory'];source=decode_source(folder/row['input_file'])
        p=get_roi_processor().prepare(source,tuple(row['roi']))
        assert p.rgb==(folder/'input.rgb888.bin').read_bytes()
        assert p.context['rule_confirmation']['status']=='confirmed'
        assert not p.context['rule_confirmation']['test_only']
        validate_roi_context(p.context,p.rgb,p.source_bytes)
        # Protocol simulator returns the supplied integer reference, not an actual CNN.
        with FormalPeer() as peer:
            peer.mask=(folder/'mask.bin').read_bytes()
            outcome=run_formal(p.rgb,local_config(peer),tmp_path/'captures',state_dir=tmp_path/'state',
                               input_context=p.context,source_bytes=p.source_bytes)
            assert outcome.report['complete'],outcome.report
            assert bytes(peer.rgb)==p.rgb
        frames.append(outcome.report['frame_id'])
        pair=live_pair(outcome);saved=save_result(tmp_path,*pair);loaded=load_record(saved)
        assert loaded.document['schema_version']=='1.2'
        assert loaded.result.mask.tobytes()==(folder/'mask.bin').read_bytes()
        assert loaded.result.metadata['source_kind']=='protocol_simulator'
        assert loaded.result.metadata['input_context']['roi']['bounds']==row['roi']
    assert frames==sorted(set(frames))


@pytest.mark.parametrize('fmt',['PNG','JPEG'])
@pytest.mark.parametrize('orientation',range(1,9))
def test_roi_exif_single_rotation_and_full_image_equivalence(tmp_path,fmt,orientation):
    raw=np.random.default_rng(6).integers(0,256,(57,89,3),np.uint8)
    path=tmp_path/f'test.{fmt.lower()}';exif=Image.Exif();exif[274]=orientation
    Image.fromarray(raw).save(path,format=fmt,exif=exif)
    unrotated=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR|cv2.IMREAD_IGNORE_ORIENTATION)
    transforms={1:lambda a:a,2:lambda a:a[:,::-1],3:lambda a:a[::-1,::-1],4:lambda a:a[::-1],
                5:lambda a:a.transpose(1,0,2),6:lambda a:np.rot90(a,3),
                7:lambda a:a.transpose(1,0,2)[::-1,::-1],8:lambda a:np.rot90(a,1)}
    expected=np.ascontiguousarray(transforms[orientation](unrotated));source=decode_source(path)
    assert np.array_equal(source.rgb,expected[:,:,::-1])
    rect=(3,4,source.rgb.shape[1]-2,source.rgb.shape[0]-3);x0,y0,x1,y1=rect
    params=[v for k,val in JPEG_OPTIONS.items() for v in (getattr(cv2,k),val)]
    resized=cv2.resize(expected[y0:y1,x0:x1],(256,256),interpolation=cv2.INTER_AREA)
    oracle=cv2.imdecode(cv2.imencode('.jpg',resized,params)[1],cv2.IMREAD_COLOR)[:,:,::-1]
    prepared=get_roi_processor().prepare(source,rect)
    assert prepared.rgb==np.ascontiguousarray(oracle).tobytes()
    full=get_roi_processor().prepare(source,(0,0,source.rgb.shape[1],source.rgb.shape[0]))
    assert full.rgb==prepare_input(path,input_kind='raw')[0].tobytes()
    path.unlink()  # Use immutable cached pixels and bytes after source removal.
    assert get_roi_processor().prepare(source,rect).rgb==prepared.rgb


@pytest.mark.parametrize('rect',[(0,0,15,16),(0,0,16,15),(False,0,16,16),(0.,0,16,16)])
def test_model_roi_minimum_rejected(tmp_path,rect):
    path=tmp_path/'small.png';Image.new('RGB',(32,32)).save(path)
    with pytest.raises(ValueError):get_roi_processor().prepare(decode_source(path),rect)
