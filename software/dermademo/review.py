"""Independent, immutable manual-review records. Never edits a device result."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from .analysis_identity import result_identity
from .device.formal_session import write_json
from .imaging import make_overlay
from .morphology import measure, quality, validate_mask
from .records import load_record
from .resources import resource_path


TEXT_FIELDS=('observations','summary','advice')
FILES={'input.rgb','raw_mask.png','review_mask.png','review_overlay.png'}


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('复核任务已取消')


def quality_bytes(raw):
    if not isinstance(raw,bytes) or len(raw)!=196608:raise ValueError('质量检查需要实际256×256 RGB888')
    result=quality(np.frombuffer(raw,np.uint8).reshape(256,256,3))
    result.update(input_rgb_sha256=sha256(raw).hexdigest(),config_sha256=digest(resource_path('quality_rules')))
    return result


def quality_message(result):
    warnings=[r['message'] for r in result['reminders'] if r['triggered']]
    return '；'.join(warnings) if warnings else '未触发固定质量提醒；不代表图像或分割质量已获保证。'


def prepare(folder,*,cancel=None):
    check_cancel(cancel);record=load_record(Path(folder));identity=result_identity((record.request,record.result))
    rgb=np.ascontiguousarray(record.request.image_bgr[:,:,::-1])
    data=measure(rgb,record.result.mask,mask_source='raw_fpga')
    check_cancel(cancel)
    return dict(record=record,identity=identity,measurement=data,quality=quality_bytes(rgb.tobytes()))


class MaskEditor:
    """One undo item per complete stroke, bounded to 64 binary 256-grid snapshots."""
    def __init__(self,mask):
        validate_mask(mask);self.original=mask.copy();self.original.setflags(write=False)
        self.mask=mask.copy();self.undo_stack=[];self.redo_stack=[];self._before=None

    @property
    def changed_pixels(self):return int(np.count_nonzero(self.mask!=self.original))

    def begin(self):
        if self._before is None:self._before=self.mask.copy()

    def line(self,start,end,*,radius,value):
        if type(radius) is not int or not 1<=radius<=32 or value not in (0,255):raise ValueError('画笔参数无效')
        points=[]
        for point in (start,end):
            if len(point)!=2 or any(not np.isfinite(v) for v in point):raise ValueError('画笔坐标无效')
            points.append(tuple(int(np.clip(round(v),0,255)) for v in point))
        self.begin();cv2.line(self.mask,*points,value,2*radius-1,cv2.LINE_8)
        for point in points:cv2.circle(self.mask,point,radius-1,value,-1,cv2.LINE_8)

    def end(self):
        if self._before is None:return False
        before,self._before=self._before,None
        if np.array_equal(before,self.mask):return False
        self.undo_stack.append(before);self.undo_stack=self.undo_stack[-64:];self.redo_stack.clear();return True

    def undo(self):
        self.end()
        if self.undo_stack:self.redo_stack.append(self.mask.copy());self.mask=self.undo_stack.pop();return True
        return False

    def redo(self):
        self.end()
        if self.redo_stack:self.undo_stack.append(self.mask.copy());self.mask=self.redo_stack.pop();return True
        return False

    def replace(self,mask):
        validate_mask(mask);self.begin();self.mask=mask.copy();return self.end()


def normalized_texts(texts):
    if not isinstance(texts,dict) or set(texts)!=set(TEXT_FIELDS):raise ValueError('复核文本字段不完整')
    if any(not isinstance(t,str) or len(t)>4000 for t in texts.values()):raise ValueError('单项复核文本限4000字')
    return {key:value.strip() for key,value in texts.items()}


def save_review(folder,mask,*,operator='',note='',texts=None,ai_draft=None,cancel=None):
    """Final marker only after every file and the original record are revalidated."""
    folder=Path(folder).resolve();record=load_record(folder);identity=result_identity((record.request,record.result))
    before=digest(folder/'record.json');validate_mask(mask);mask=mask.copy()
    if not isinstance(operator,str) or len(operator)>80 or not isinstance(note,str) or len(note)>2000:raise ValueError('复核人或修订说明过长')
    texts=normalized_texts(texts or {key:'' for key in TEXT_FIELDS})
    if ai_draft is not None:
        if not isinstance(ai_draft,dict) or ai_draft.get('identity')!=identity:raise ValueError('AI草稿不属于当前帧')
        normalized_texts(ai_draft['texts'])
    check_cancel(cancel)
    changed=int(np.count_nonzero(mask!=record.result.mask))
    if (folder/'reviews').is_symlink():raise ValueError('复核目录不得为符号链接')
    identifier='review_'+uuid4().hex;out=folder/'reviews'/identifier
    out.mkdir(parents=True,exist_ok=False);(out/'INCOMPLETE.txt').write_text('复核保存未完成\n','utf-8')
    rgb=np.ascontiguousarray(record.request.image_bgr[:,:,::-1])
    variant='manual_revision:'+identifier if changed else 'raw_fpga'
    data=dict(schema='derma.manual_review.v1',review_id=identifier,
              saved_at=datetime.now().astimezone().isoformat(timespec='seconds'),identity=identity,
              record_id=record.document['record_id'],record_sha256=before,mask_variant=variant,
              changed_pixels=changed,operator=operator.strip(),note=note.strip(),texts=texts,
              human_entered=True,clinical_review_verified=False,ai_draft=ai_draft,
              original_measurement=measure(rgb,record.result.mask,mask_source='raw_fpga'),
              review_measurement=measure(rgb,mask,mask_source=variant),quality=quality_bytes(rgb.tobytes()))
    (out/'input.rgb').write_bytes(rgb.tobytes())
    Image.fromarray(record.result.mask).save(out/'raw_mask.png')
    Image.fromarray(mask).save(out/'review_mask.png')
    Image.fromarray(make_overlay(record.request.image_bgr,mask)[:,:,::-1]).save(out/'review_overlay.png')
    check_cancel(cancel)
    data['files']={name:dict(sha256=digest(out/name),bytes=(out/name).stat().st_size) for name in sorted(FILES)}
    write_json(out/'review.json',data)
    # Read back all bytes before committing. An incomplete result is never loadable.
    _load_review(out,allow_incomplete=True)
    check_cancel(cancel)
    if digest(folder/'record.json')!=before:raise ValueError('保存期间原记录发生变化；复核未标完成')
    (out/'INCOMPLETE.txt').unlink()
    return out


def _load_review(out,*,allow_incomplete=False):
    out=Path(out).absolute()
    if out.is_symlink() or out.parent.is_symlink() or out.parent.name!='reviews' or (out/'INCOMPLETE.txt').exists() and not allow_incomplete:
        raise ValueError('不是完整的复核记录目录')
    folder=out.parent.parent
    data=json.loads((out/'review.json').read_text('utf-8'))
    if data.get('schema')!='derma.manual_review.v1' or data.get('review_id')!=out.name:raise ValueError('复核记录格式无效')
    if digest(folder/'record.json')!=data['record_sha256']:raise ValueError('复核原记录摘要不符')
    record=load_record(folder);identity=result_identity((record.request,record.result))
    if identity!=data['identity'] or data['record_id']!=record.document['record_id']:raise ValueError('复核记录帧身份不符')
    if set(data['files'])!=FILES:raise ValueError('复核文件集合不符')
    for name,spec in data['files'].items():
        path=out/name
        if path.is_symlink() or path.stat().st_size!=spec['bytes'] or digest(path)!=spec['sha256']:raise ValueError('复核文件缺失或摘要不符')
    rgb=np.ascontiguousarray(record.request.image_bgr[:,:,::-1])
    if (out/'input.rgb').read_bytes()!=rgb.tobytes():raise ValueError('复核输入与原帧不一致')
    with Image.open(out/'raw_mask.png') as image:raw=np.array(image)
    with Image.open(out/'review_mask.png') as image:mask=np.array(image)
    validate_mask(raw);validate_mask(mask)
    if not np.array_equal(raw,record.result.mask):raise ValueError('复核原始Mask不符')
    changed=int(np.count_nonzero(mask!=raw))
    variant='manual_revision:'+out.name if changed else 'raw_fpga'
    if data['changed_pixels']!=changed or data['mask_variant']!=variant:raise ValueError('修订状态与实际修改不符')
    if (data['original_measurement']!=measure(rgb,raw,mask_source='raw_fpga')
            or data['review_measurement']!=measure(rgb,mask,mask_source=variant)
            or data['quality']!=quality_bytes(rgb.tobytes())):raise ValueError('复核测量或质量数据不符')
    normalized_texts(data['texts'])
    with Image.open(out/'review_overlay.png') as image:overlay=np.array(image)
    if not np.array_equal(overlay,make_overlay(record.request.image_bgr,mask)[:,:,::-1]):raise ValueError('修订叠加图不符')
    return dict(folder=out,document=data,record=record,mask=mask)


def load_review(out):
    try:return _load_review(out)
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        raise ValueError('复核记录读取失败：'+str(exc)) from exc


def measurement_overlay(image,mask,measurement,*,feret=True,axes=True):
    """Display-only lines on the model grid; not a modified model input."""
    output=make_overlay(image,mask)
    def draw(points,color):
        if points:
            a,b=(tuple(int(round(v)) for v in point) for point in points)
            cv2.line(output,a,b,color,1,cv2.LINE_AA)
    if feret:draw(measurement['max_feret_endpoints_xy'],(0,210,255))
    if axes and measurement['axes'].get('valid'):
        draw(measurement['axes']['major_endpoints_xy'],(255,200,0))
        draw(measurement['axes']['minor_endpoints_xy'],(255,80,220))
    return output
