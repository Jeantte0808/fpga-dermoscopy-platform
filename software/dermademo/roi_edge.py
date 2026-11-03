"""Bind the member2 read-only reminder to a successful ROI result snapshot."""
from hashlib import sha256
import json

import numpy as np

from .roi_edge_contact import check_edge_contact

RULE_ID = 'ROI_EDGE_CONTACT_V1_R1'
IMPLEMENTATION_SHA256 = '6955f8c293e88ee682aafa95379640671a962e9f37a0e95be5f52146ef6509b5'
SIDE_NAMES = {'top':'上','bottom':'下','left':'左','right':'右'}


def result_edge_report(metadata, mask):
    """Only an existing successful ROI frame. Never reads the current GUI box."""
    context = metadata.get('input_context')
    if not isinstance(context, dict) or context.get('schema') != 'formal_roi_v1':
        return None
    from .roi import validate_rect
    width,height = context['roi']['source_size']
    rect = validate_rect(context['roi']['bounds'],width,height)
    prep = context['preprocessing']
    if [width,height] != [prep['oriented_width'],prep['oriented_height']]:
        raise ValueError('触边提醒原图坐标系不一致')
    if metadata['input_sha256'] != prep['rgb888_sha256']:
        raise ValueError('触边提醒实际RGB与提交上下文摘要不一致')
    frame,generation = metadata['frame_id'],metadata['generation']
    if type(frame) is not int or type(generation) is not int or frame<1 or generation<1:
        raise ValueError('触边提醒必须绑定成功帧身份')
    result = check_edge_contact(mask)
    x0,y0,x1,y1 = rect
    return {'schema':'roi_edge_contact_v1','rule_id':RULE_ID,'implementation_sha256':IMPLEMENTATION_SHA256,
            'mask_sha256':sha256(mask.tobytes(order='C')).hexdigest(),
            'submitted':{'source_filename':context['source_filename'],'source_sha256':context['source_sha256'],'oriented_size':[width,height],
                         'roi':list(rect),'frame_id':frame,'generation':generation,
                         'input_rgb_sha256':metadata['input_sha256'],'source_kind':metadata['source_kind']},
            'original_image_limits':{'top':y0==0,'bottom':y1==height,'left':x0==0,'right':x1==width},
            'result':result}


def validated_edge_report(metadata,mask):
    """Old records may omit the field; a present field must match actual raw Mask."""
    expected = result_edge_report(metadata,mask)
    if 'roi_edge_contact' in metadata:
        # Canonical JSON preserves distinctions such as true/1 and 1/1.0.
        try:
            stored=json.dumps(metadata['roi_edge_contact'],sort_keys=True,allow_nan=False)
            actual=json.dumps(expected,sort_keys=True,allow_nan=False)
        except (TypeError,ValueError) as exc:
            raise ValueError('触边提醒记录格式无效') from exc
        if expected is None or stored != actual:
            raise ValueError('触边提醒与实际原始Mask或成功帧上下文不一致')
    return expected


def reminder_text(metadata,mask,*,historical=False):
    report = validated_edge_report(metadata,mask)
    if report is None:
        return '',False
    submitted,result=report['submitted'],report['result']
    origin = ('历史结果' if historical else '上次成功结果')
    if submitted['source_kind']=='protocol_simulator':origin+=' · 本机协议模拟，非实板'
    prefix=f"{origin} · 帧 {submitted['frame_id']} · {submitted['source_filename']} · ROI {submitted['roi']}"
    if not result['edge_contact']:
        return prefix+'\n最外侧1像素未触发触边提醒；不代表病灶完整或质量合格。',False
    sides='、'.join(SIDE_NAMES[side] for side in result['touched_sides'])
    counts=' / '.join(f"{SIDE_NAMES[side]} {value}" for side,value in result['side_foreground_counts'].items())
    text=f'{prefix}\n预测接触推理框边缘，{result["message"]}。触及：{sides}；边缘前景像素：{counts}。'
    limited=[SIDE_NAMES[side] for side in result['touched_sides'] if report['original_image_limits'][side]]
    if limited:text+=' '+ '、'.join(limited)+'侧已受原图边界限制。'
    return text,True


def verify_edge_samples():
    """Software reference fixtures only; no inference, board or manual GT input."""
    from .resources import resource_path
    root=resource_path('edge_root');cases=[]
    for row in json.loads((root/'samples.json').read_text('utf-8')):
        raw=(root/row['raw_mask_path']).read_bytes()
        if len(raw)!=65536 or sha256(raw).hexdigest()!=row['raw_mask_sha256']:
            raise ValueError('触边样例原始Mask摘要不符')
        mask=np.frombuffer(raw,np.uint8).reshape(256,256)
        actual=check_edge_contact(mask)
        if actual!=row['result'] or mask.tobytes()!=raw:
            raise ValueError('触边提醒与成员2样例不一致')
        cases.append({'case':row['case'],'edge_contact':actual['edge_contact'],
                      'side_foreground_counts':actual['side_foreground_counts'],'raw_mask_unchanged':True})
    if len(cases)!=6:raise ValueError('触边样例必须包含6组')
    return {'rule_id':RULE_ID,'cases':cases,'model_inference_performed':False,'physical_FPGA_tested':False}
