from copy import deepcopy
from hashlib import sha256
import csv,json
from pathlib import Path
import pytest
from scripts.prepare_reference_import import prepare
from dermademo.formal_input import prepare_image
from dermademo.formal_results import replay_pair,live_pair
from dermademo.device.formal_session import FormalOutcome
from dermademo.validation import load_reference,mask_comparison


def test_twenty_bound_references_and_separate_annotation_metrics(tmp_path):
    index=Path('integrations/queue_reference_r1');gallery=Path('datasets/validation20_demo_gallery')
    output=tmp_path/'prepared';report=prepare(index,gallery,output)
    assert report['declarations']==40 and report['original_packages_modified'] is False
    rows=list(csv.DictReader((index/'reference_index_20.csv').read_text('utf-8-sig').splitlines()))
    for i,row in enumerate(rows):
        sid=row['sample_id'];prepared=prepare_image(gallery/'originals'/(sid+'.jpg'))
        assert sha256(prepared.rgb).hexdigest()==row['rgb888_sha256']
        mask=(gallery/'int8_reference/bin'/(sid+'.mask.bin')).read_bytes()
        wire=deepcopy(replay_pair()[1].metadata['wire_report']);frame=4000000100+i
        wire.update(origin='protocol_simulator',input_context=prepared.context,input_sha256=sha256(prepared.rgb).hexdigest(),mask_sha256=sha256(mask).hexdigest(),frame_id=frame,official_fixed_sample_exact=False)
        wire['last_reply'].update(frame=frame,result_frame=frame)
        pair=live_pair(FormalOutcome(prepared.rgb,mask,wire,None,tmp_path,prepared.source_bytes))
        integer=load_reference(output/sid/'int8'/(sid+'.json'),pair)
        annotation=load_reference(output/sid/'annotation'/(sid+'.json'),pair)
        exact=mask_comparison(pair[1].mask,integer['mask'],kind=integer['kind'])
        quality=mask_comparison(pair[1].mask,annotation['mask'],kind=annotation['kind'])
        assert exact['byte_exact'] and 'dice' not in exact
        assert quality['dice']==pytest.approx(float(row['current_dice']))
        assert quality['iou']==pytest.approx(float(row['current_iou']))
        pair[1].metadata['input_context']['roi']={'bounds':[0,0,256,256]}
        with pytest.raises(ValueError):load_reference(output/sid/'annotation'/(sid+'.json'),pair)
    aba=list(csv.DictReader((output/'aba_order_3_local.csv').read_text('utf-8-sig').splitlines()))
    assert [r['sample_id'] for r in aba]==['ISIC_0000079','ISIC_0000127','ISIC_0000079']
