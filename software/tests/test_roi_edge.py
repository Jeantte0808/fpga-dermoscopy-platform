from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from dermademo.roi_edge_contact import check_edge_contact
from dermademo.roi_edge import (verify_edge_samples,result_edge_report,validated_edge_report,reminder_text,
                                IMPLEMENTATION_SHA256)
from dermademo.roi import decode_source,get_roi_processor
from dermademo.resources import resource_path
from dermademo.device.formal_session import run_formal
from dermademo.formal_results import live_pair
from dermademo.storage import save_result
from dermademo.records import load_record
from formal_mask_peer import FormalPeer,local_config


@pytest.mark.parametrize('name,points,counts',[
    ('empty',[],[0,0,0,0]),('interior',[(1,1),(254,254)],[0,0,0,0]),
    ('top',[(0,80)],[1,0,0,0]),('bottom',[(255,80)],[0,1,0,0]),
    ('left',[(80,0)],[0,0,1,0]),('right',[(80,255)],[0,0,0,1]),
    ('top-left',[(0,0)],[1,0,1,0]),('top-right',[(0,255)],[1,0,0,1]),
    ('bottom-left',[(255,0)],[0,1,1,0]),('bottom-right',[(255,255)],[0,1,0,1]),
    ('four-corners',[(0,0),(0,255),(255,0),(255,255)],[2,2,2,2])])
def test_confirmed_exact_outer_pixel_rule_readonly(name,points,counts):
    a=np.zeros((256,256),np.uint8)
    for point in points:a[point]=255
    a.setflags(write=False);before=a.tobytes();result=check_edge_contact(a)
    assert list(result['side_foreground_counts'].values())==counts
    assert result['touched_sides']==[s for s,n in zip(('top','bottom','left','right'),counts) if n]
    assert result['edge_contact']==bool(sum(counts)) and a.tobytes()==before


def test_all_foreground_noncontiguous_and_delivered_function_identity():
    a=np.full((256,256),255,np.uint8)[:,::-1]
    assert list(check_edge_contact(a)['side_foreground_counts'].values())==[256]*4
    root=resource_path('edge_root')
    assert sha256((root/'software/roi_edge_contact.py').read_bytes()).hexdigest()==IMPLEMENTATION_SHA256
    assert sha256(Path('dermademo/roi_edge_contact.py').read_bytes()).hexdigest()==IMPLEMENTATION_SHA256
    checked=verify_edge_samples()
    assert len(checked['cases'])==6 and sum(r['edge_contact'] for r in checked['cases'])==3


@pytest.mark.parametrize('bad',[None,[[0]],np.zeros((255,256),np.uint8),np.zeros((256,256,1),np.uint8),
    np.zeros((256,256),bool),np.zeros((256,256),np.int8),np.zeros((256,256),np.float32),
    np.full((256,256),1,np.uint8),np.full((256,256),254,np.uint8),
    np.ma.array(np.zeros((256,256),np.uint8),mask=False)])
def test_invalid_raw_masks_are_not_coerced(bad):
    with pytest.raises((ValueError,TypeError)):check_edge_contact(bad)


@pytest.fixture
def edge_pair(tmp_path):
    root=resource_path('edge_root');row=json.loads((root/'samples.json').read_text('utf-8'))[2]
    p=get_roi_processor().prepare(decode_source(root/row['original_path']),tuple(row['roi']))
    with FormalPeer() as peer:
        peer.mask=(root/row['raw_mask_path']).read_bytes()
        out=run_formal(p.rgb,local_config(peer),tmp_path/'captures',state_dir=tmp_path/'state',
                       input_context=p.context,source_bytes=p.source_bytes)
        assert out.report['complete'],out.report
    return live_pair(out)


def test_successful_context_binding_save_reopen_old_records_and_tampering(edge_pair,tmp_path):
    request,result=edge_pair;raw=result.mask.tobytes()
    edge=result.metadata['roi_edge_contact'];submitted=edge['submitted']
    assert submitted['roi']==[680,470,1022,767] and submitted['frame_id']==result.metadata['frame_id']
    assert edge['result']['side_foreground_counts']=={'top':67,'bottom':0,'left':22,'right':0}
    assert submitted['source_sha256']==sha256(request.source_bytes).hexdigest()
    assert edge['mask_sha256']==sha256(raw).hexdigest()
    text,warning=reminder_text(result.metadata,result.mask)
    assert warning and '预测接触推理框边缘' in text and '可能截断病灶' in text
    saved=save_result(tmp_path,request,result);restored=load_record(saved)
    assert restored.result.metadata['roi_edge_contact']==edge and restored.result.mask.tobytes()==raw
    assert '历史结果' in reminder_text(restored.result.metadata,restored.result.mask,historical=True)[0]
    doc=json.loads((saved/'record.json').read_text('utf-8'))
    for key in ('roi','source_sha256','frame_id'):
        altered=deepcopy(doc)
        target=altered['processing']['roi_edge_contact']['submitted']
        target[key]=[0,0,16,16] if key=='roi' else '0'*64 if key=='source_sha256' else target[key]+1
        (saved/'record.json').write_text(json.dumps(altered),'utf-8')
        with pytest.raises(ValueError,match='触边'):load_record(saved)
    # Legacy schema1.2 omission remains supported; re-export adds metadata without mutating old snapshot.
    doc['processing'].pop('roi_edge_contact');(saved/'record.json').write_text(json.dumps(doc),'utf-8')
    old=load_record(saved);assert 'roi_edge_contact' not in old.result.metadata
    assert reminder_text(old.result.metadata,old.result.mask,historical=True)[1]
    new=save_result(tmp_path,old.request,old.result)
    assert load_record(new).result.metadata['roi_edge_contact']==edge
    assert 'roi_edge_contact' not in old.result.metadata and result.mask.tobytes()==raw


def test_whole_image_is_not_roi_and_non_roi_cannot_carry_stale_reminder(edge_pair):
    mismatched=deepcopy(edge_pair[1].metadata);mismatched['input_sha256']='0'*64
    with pytest.raises(ValueError,match='实际RGB'):result_edge_report(mismatched,edge_pair[1].mask)
    meta=deepcopy(edge_pair[1].metadata);meta.pop('input_context')
    assert result_edge_report(meta,edge_pair[1].mask) is None
    with pytest.raises(ValueError,match='触边'):validated_edge_report(meta,edge_pair[1].mask)
    meta.pop('roi_edge_contact');assert reminder_text(meta,edge_pair[1].mask)==('',False)
