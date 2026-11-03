"""Version-bound selections shared by reports and confirmed AI uploads."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from .analysis_identity import result_identity
from .records import load_record
from .morphology import measure
from .review import load_review,quality_bytes,digest
from .device.formal_session import write_json


def stable_hash(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def safe_file(root,relative):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts:raise ValueError('分析引用路径无效')
    full=(Path(root)/path).resolve()
    if not full.is_relative_to(Path(root).resolve()) or (Path(root)/path).is_symlink() or not full.is_file():raise ValueError('分析引用文件缺失或越界')
    return full


def select_review(folder,selection='auto'):
    root=Path(folder).resolve()
    if selection is None:return None
    if selection!='auto':
        p=Path(selection);p=p if p.is_absolute() else root/p
        if not p.resolve().is_relative_to(root/'reviews'):raise ValueError('修订选择不属于本图')
        return p
    index=root/'report_selection.json'
    if index.exists():
        data=json.loads(index.read_text('utf-8'))
        if data.get('review') is None:return None
        return safe_file(root,data['review']+'/review.json').parent
    candidates=[p for p in (root/'reviews').glob('review_*') if p.is_dir() and not (p/'INCOMPLETE.txt').exists()]
    if not candidates:return None
    # Saved_at is authoritative; malformed newest metadata is not replaced by an older review.
    return max(candidates,key=lambda p:json.loads((p/'review.json').read_text('utf-8'))['saved_at'])


def save_selection(folder,review):
    record=load_record(Path(folder));identity=result_identity((record.request,record.result))
    relative=None
    if review is not None:
        review=Path(review)
        if not review.is_absolute():review=Path(folder)/review
        if not review.resolve().is_relative_to(Path(folder).resolve()/'reviews'):raise ValueError('修订选择不属于本图')
        loaded=load_review(review)
        if loaded['document']['identity']!=identity:raise ValueError('修订不属于本图')
        relative=Path(review).resolve().relative_to(Path(folder).resolve()).as_posix()
    write_json(Path(folder)/'report_selection.json',dict(identity=identity,review=relative))


def build_snapshot(folder,*,review='auto',expected_classification=None,expected_reference=None,include_ai=True,ai_override=None):
    folder=Path(folder).resolve();record=load_record(folder);identity=result_identity((record.request,record.result))
    rgb=np.ascontiguousarray(record.request.image_bgr[:,:,::-1])
    pins={}
    def pin(path):
        path=Path(path);relative=path.relative_to(folder).as_posix();safe_file(folder,relative)
        pins[relative]=dict(sha256=digest(path),bytes=path.stat().st_size)
    for name in ('record.json','original.png','enhanced.png','mask.png','overlay.png','input.rgb','mask.raw','source_image.bin',
                 'source_oriented.png','roi_crop.png','mapped_mask.png','processed_region.png','mapped_overlay.png'):
        if (folder/name).is_file():pin(folder/name)
    raw_measurement=measure(rgb,record.result.mask,mask_source='raw_fpga')
    quality=quality_bytes(rgb.tobytes())
    manual=dict(status='not_run',selection=None);selected=None;review_loaded=None
    try:
        selected=select_review(folder,review)
        if selected:
            value=load_review(selected);review_loaded=value;doc=value['document']
            if doc['identity']!=identity:raise ValueError('修订帧身份不符')
            pin(selected/'review.json')
            for name in doc['files']:pin(selected/name)
            manual=dict(status='success',selection=selected.relative_to(folder).as_posix(),
                        document=doc,document_sha256=digest(selected/'review.json'))
    except Exception as exc:manual=dict(status='unavailable',selection=str(review),error=str(exc));selected=None;review_loaded=None
    classification=dict(status='not_run')
    try:
        expected=expected_classification or {}
        if expected.get('status') in ('failed','cancelled','not_run'):
            classification=dict(status=expected['status'],error=expected.get('error'),scope='whole_original_image')
        elif expected.get('status')=='success' or (folder/'classification_index.json').exists():
            from .classification import for_record,load
            if expected.get('status')=='success':
                cfolder=safe_file(folder,expected['folder']+'/classification.json').parent
                if expected.get('result_sha256')!=digest(cfolder/'result.json'):raise ValueError('队列分类摘要不符')
                value=load(cfolder,source_sha=sha256(record.request.source_bytes).hexdigest())
            else:
                value=for_record(record);index=json.loads((folder/'classification_index.json').read_text('utf-8'))
                cfolder=safe_file(folder,index['folder']+'/classification.json').parent
            for name in ('classification.json','result.json','actual_224_input.png',value['identity']['source_filename']):pin(cfolder/name)
            classification=dict(status='success',selection=cfolder.relative_to(folder).as_posix(),result=value,
                                result_sha256=digest(cfolder/'result.json'),scope='whole_original_image')
        elif expected.get('status')=='success':raise ValueError('本项分类关联文件缺失')
    except Exception as exc:classification=dict(status='unavailable',error=str(exc))
    context=dict(schema='derma.analysis_context.v1',identity=identity,record_id=record.document['record_id'],
                 record_sha256=pins['record.json']['sha256'],classification=classification,quality=quality,manual=manual,
                 original_measurement=raw_measurement,dependencies=pins)
    # Only actual data selections enter the semantic signature, not absolute directories or AI tasks.
    signature=stable_hash(dict(identity=identity,record_sha256=context['record_sha256'],
        classification={k:v for k,v in classification.items() if k not in ('error',)},
        quality=quality,manual={k:v for k,v in manual.items() if k not in ('error',)}))
    context['signature']=signature
    snapshot=dict(folder=folder,record=record,context=context,review=review_loaded,
                  ai=dict(status='not_run'))
    from .reference_library import compare
    snapshot['reference']=compare((record.request,record.result),expected=expected_reference)
    if ai_override is not None:
        meta=ai_override['meta']
        if meta['identity']!=identity:raise ValueError('AI帧身份不符')
        if ai_override.get('status') in ('failed','not_run','cancelled'):
            snapshot['ai']=dict(status=ai_override['status'],reason='AI未完成，基础结果独立保留。')
        elif meta.get('context_signature') not in (None,signature):
            snapshot['ai']=dict(status='not_run',reason='当前数据/修订版本未运行AI')
        elif manual['status']=='success' and not meta.get('context_signature'):
            snapshot['ai']=dict(status='not_run',reason='旧AI仅分析原始Mask，未分析选定修订')
        else:snapshot['ai']=dict(status='success',meta=meta,answer=ai_override['answer'],legacy_context=not bool(meta.get('context_signature')))
    elif include_ai:
        snapshot['ai']=find_ai(snapshot)
    return snapshot


def validate_context(folder,context):
    folder=Path(folder).resolve();record=load_record(folder)
    if result_identity((record.request,record.result))!=context['identity'] or digest(folder/'record.json')!=context['record_sha256']:
        raise ValueError('分析上下文的原帧已变化')
    for relative,spec in context['dependencies'].items():
        path=safe_file(folder,relative)
        if path.stat().st_size!=spec['bytes'] or digest(path)!=spec['sha256']:raise ValueError('分析上下文引用文件已变化')
    return record


def from_context(folder,context,*,ai=None):
    record=validate_context(folder,context);folder=Path(folder).resolve()
    manual=context['manual'];review=None
    if manual['status']=='success':review=load_review(folder/manual['selection'])
    value=dict(folder=folder,record=record,context=context,review=review,ai=dict(status='not_run'))
    from .reference_library import compare
    value['reference']=compare((record.request,record.result))
    if ai is not None:
        if ai['meta']['identity']!=context['identity'] or ai['meta'].get('context_signature')!=context['signature']:raise ValueError('AI不是所选分析版本')
        value['ai']=dict(status=ai.get('status','success'),meta=ai['meta'],answer=ai.get('answer'),legacy_context=False,
                         reason='AI分析未完成；保留基础结果。')
    return value


def find_ai(snapshot):
    from .ai_service import load_analysis
    context=snapshot['context'];candidates=sorted((snapshot['folder']/'ai').glob('analysis_*'),key=lambda p:p.name)
    matches=[];failed=[]
    for task in candidates:
        try:
            raw=json.loads((task/'analysis_report.json').read_text('utf-8'))
            if raw.get('identity')!=context['identity']:continue
            if raw.get('context_signature') not in (None,context['signature']):continue
            if context['manual']['status']=='success' and not raw.get('context_signature'):continue
            if not raw.get('cloud_complete'):
                failed.append(raw);continue
            matches.append((task,raw))
        except Exception:continue
    if matches:
        # Task ids are random. Saved response mtime is only a tie-break for selecting among valid same-context responses.
        for task,_ in sorted(matches,key=lambda v:(bool(v[1].get('context_signature')),(v[0]/'analysis_report.json').stat().st_mtime_ns),reverse=True):
            try:meta,answer=load_analysis(task)
            except Exception:continue
            return dict(status='success',task=str(task),meta=meta,answer=answer,legacy_context=not bool(meta.get('context_signature')))
    if failed:return dict(status='failed',reason='AI分析未完成；基础结果可独立导出')
    return dict(status='not_run',reason='当前图片和修订版本未运行AI')


def upload_facts(snapshot):
    """Whitelist: no local paths, dependency file names, reviewer identity or keys."""
    c=snapshot['context'];classified=c['classification'];manual=c['manual'];q=c['quality']
    data=dict(version='ANALYSIS_FACTS_V1',context_signature=c['signature'],quality=dict(version=q['version'],scores=q['scores'],
              triggered=q['triggered'],reminders=[r['message'] for r in q['reminders'] if r['triggered']],scope='actual_model_input_256'),
              classification=dict(status=classified['status'],scope='whole_original_image'),manual=dict(status=manual['status']))
    if classified['status']=='success':
        r=classified['result'];data['classification'].update(model_version=r['model_version'],predicted_class=r['predicted_class'],class_scores=r['class_scores'],
              source_sha256=r['identity']['source_sha256'],scores_are_disease_probability=False,non_melanoma_group_is_benign=False)
    if manual['status']=='success':
        d=manual['document'];data['manual'].update(review_id=d['review_id'],mask_variant=d['mask_variant'],changed_pixels=d['changed_pixels'],
             mask_sha256=d['review_measurement']['mask_sha256'],measurement=d['review_measurement'],review_texts=d['texts'])
    return data
