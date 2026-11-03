"""One explicitly confirmed cloud request; independent durable AI/PDF state."""
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import urllib.request
import urllib.error
import numpy as np

from .ai_protocol import MODEL,DEFAULT_BASE,SYSTEM,request_body,validate_base
from .analysis_identity import result_identity
from .ai_transport import opener,stage,stage_callback,runtime_info
from .ai_diagnostics import exception_diagnostic
from .ai_content_policy import prepare_draft,validate_schema
from .records import load_record
from .device.formal_session import write_json,write_bytes
from .device.formal_capture import png_bytes


def bundle(folder,*,analysis_context=None,include_analysis=False,review='auto'):
    rec=load_record(Path(folder));identity=result_identity((rec.request,rec.result));wire=rec.result.metadata['wire_report']
    if (not wire.get('complete') or any(wire.get(k) is False for k in ('hardware_finish_confirmed','local_artifacts_complete'))
            or wire.get('origin') not in (None,'live_FPGA_session')
            or rec.result.metadata.get('source_kind') not in ('live_FPGA_session','saved_FPGA_capture')):
        raise ValueError('AI只接受完整保存的真实FPGA记录；模拟结果不可上传')
    mask=rec.result.mask;edges=dict(top=int(np.count_nonzero(mask[0])),bottom=int(np.count_nonzero(mask[-1])),
                                  left=int(np.count_nonzero(mask[:,0])),right=int(np.count_nonzero(mask[:,-1])))
    if analysis_context not in (None,'current_result','historical_record'):raise ValueError('AI分析上下文无效')
    source_mode=identity['display_mode'] if identity['display_mode'] in ('live_result','offline_replay') else 'not_provided'
    facts=dict(source_kind=identity['source_kind'],result_display_mode=source_mode,analysis_context=analysis_context or 'not_provided',grid=[256,256],frame_id=identity['frame_id'],generation=identity['generation'],roi=identity['roi'],
               model_version=identity['model_version'],area_px2=rec.result.measurements.area_px2,
               max_feret_px=rec.result.measurements.max_feret_px,edge_foreground_pixels=edges,edge_contact=any(edges.values()),
               physical_scale='not_calibrated_model256_only',classification='not_enabled',image_policy='actual_RGB_no_CLAHE')
    result=dict(identity=identity,record_id=rec.document['record_id'],record_sha256=sha256((Path(folder)/'record.json').read_bytes()).hexdigest(),
                facts=facts,input_png=png_bytes(np.ascontiguousarray(rec.request.image_bgr[:,:,::-1]).tobytes(),3),mask_png=png_bytes(mask.tobytes(),1))
    if include_analysis:
        from .analysis_snapshot import build_snapshot,upload_facts
        snapshot=build_snapshot(folder,review=review,include_ai=False)
        facts['analyses']=upload_facts(snapshot);facts['classification']=facts['analyses']['classification'];result['context']=snapshot['context']
        if snapshot['review'] is not None and snapshot['context']['manual']['document']['changed_pixels']:
            result['revision_png']=png_bytes(snapshot['review']['mask'].tobytes(),1)
    return result


def post_once(base,key,payload):
    stage('https.request_build')
    req=urllib.request.Request(validate_base(base)+'/chat/completions',payload,
                               {'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    client=opener()
    stage('https.open')
    with client.open(req,timeout=60) as response:
        stage('https.response_headers',http_status=response.status)
        stage('https.response_read')
        raw=response.read(512*1024+1)
        if len(raw)>512*1024:raise ValueError('response_too_large')
        return raw


def clean(value,key):
    if isinstance(value,str):return value.replace(key,'[REDACTED]')
    if isinstance(value,list):return [clean(x,key) for x in value]
    if isinstance(value,dict):return {clean(k,key):clean(v,key) for k,v in value.items()}
    return value


def run_analysis(record_folder,base,key,*,cancel=None,progress=None,post=None,historical=False,review='auto',prepared=None):
    # No retries, even for HTTP/timeout/validation errors.
    started=perf_counter();folder=Path(record_folder).resolve()
    if type(historical) is not bool:raise ValueError('historical必须是布尔值')
    analysis_context='historical_record' if historical else 'current_result'
    b=prepared or bundle(folder,analysis_context=analysis_context,include_analysis=True,review=review)
    if b['facts'].get('analysis_context')!=analysis_context:raise ValueError('上传确认情境与准备结果不符')
    from .analysis_snapshot import validate_context
    if b.get('context'):validate_context(folder,b['context'])
    base=validate_base(base);key=key.strip()
    if not key or any(c in key for c in '\r\n\x00'):raise ValueError('密钥格式无效')
    task=folder/'ai'/('analysis_'+uuid4().hex);task.mkdir(parents=True,exist_ok=False)
    payload=json.dumps(request_body(b),ensure_ascii=False).encode('utf-8')
    meta=dict(schema='alienhome.ai.v1',task_id=task.name,identity=b['identity'],record_id=b['record_id'],record_sha256=b['record_sha256'],
              historical=historical,analysis_context=analysis_context,source_record=str(folder.resolve()),requested_model=MODEL,base_url=base,prompt_sha256=sha256(SYSTEM.encode()).hexdigest(),
              prompt_version='pc_analysis_context_v1_20261031',cloud_complete=False,pdf_complete=False,automatic_retry=False,
              request_max_tokens=700,transport_complete=False,response_schema_valid=False,review_state='not_available',cloud_transport='https' if post is None else 'offline_test_fixture',uploaded_images=3 if 'revision_png' in b else 2,cloud_elapsed_ms=None,clinical_reviewed=False,status='prepared')
    if b.get('context'):
        write_json(task/'context.json',b['context'])
        meta.update(facts_version='ANALYSIS_FACTS_V1',context_signature=b['context']['signature'],context_sha256=sha256((task/'context.json').read_bytes()).hexdigest(),
                    analysis_snapshot=dict(manual=dict(selection=b['context']['manual'].get('selection'))))
    write_json(task/'analysis_report.json',meta)
    write_json(task/'facts.json',b['facts'])
    failure_stage=['cloud.before_send']
    meta.update(stage_events=[],request_started_local=False,http_response_received=False,
                server_receipt_confirmed=False,cloud_elapsed_scope='client observed; includes setup; failure may precede connection')
    def advance(value,details=None):
        failure_stage[0]=value
        event=dict(stage=value,elapsed_ms=(perf_counter()-started)*1000)
        if details and 'http_status' in details:event['http_status']=details['http_status']
        if len(meta['stage_events'])<40:meta['stage_events'].append(event)
        if value=='https.open':meta['request_started_local']=True
        if value=='https.response_headers':meta.update(http_response_received=True,http_status=details['http_status'])
    token=stage_callback.set(advance)
    try:
        if cancel is not None and cancel.is_set():raise InterruptedError()
        if progress:progress('AI分析中（一次请求，最长60秒；中断等待不保证云端停止计费）',0)
        clock=perf_counter()
        raw=(post or post_once)(base,key,payload)
        meta['cloud_elapsed_ms']=(perf_counter()-clock)*1000
        meta['transport_complete']=True
        advance('response.decode_json')
        response=clean(json.loads(raw),key)
        advance('response.save')
        write_json(task/'response.json',response)
        advance('response.validate_answer')
        choice=response['choices'][0]
        if choice.get('finish_reason')!='stop':raise ValueError('incomplete_response')
        raw_answer=json.loads(choice['message']['content'])
        validate_schema(raw_answer)
        meta['response_schema_valid']=True
        answer,audit=prepare_draft(raw_answer)
        meta.update(review_state='draft_pending_review',content_policy_version=audit['policy_version'],answer_kind='postprocessed_AI_draft',content_quality_verified=False)
        write_json(task/'draft_audit.json',audit)
        advance('source.revalidate')
        if bundle(folder,analysis_context=analysis_context)['record_sha256']!=b['record_sha256']:raise ValueError('source_changed')
        if b.get('context'):validate_context(folder,b['context'])
        advance('analysis.save')
        write_json(task/'analysis.json',answer)
        meta.update(cloud_complete=True,status='draft_pending_review',response_sha256=sha256((task/'response.json').read_bytes()).hexdigest(),answer_sha256=sha256((task/'analysis.json').read_bytes()).hexdigest(),
                    response_id=response.get('id'),returned_model=response.get('model'),usage=response.get('usage'))
        write_json(task/'analysis_report.json',meta)
    except Exception as exc:
        if isinstance(exc,urllib.error.HTTPError):meta.update(http_response_received=True,http_status=exc.code)
        diagnostic=exception_diagnostic(exc,failure_stage[0],key)
        failed_status=('transport_failed' if not meta['transport_complete'] else 'response_save_failed' if failure_stage[0]=='response.save' else 'response_invalid' if not meta['response_schema_valid'] else 'analysis_save_failed')
        meta.update(failure_stage=failure_stage[0],missing_files=diagnostic['missing_files'],diagnostic=diagnostic,transport_runtime=runtime_info(),status=failed_status,error=('已取消，未自动重试' if isinstance(exc,InterruptedError) else
                    ('云端传输失败（' if not meta['transport_complete'] else '云端已返回，本地响应处理失败（')+type(exc).__name__+'）；未自动重试'),
                    cloud_elapsed_ms=meta['cloud_elapsed_ms'] if meta['cloud_elapsed_ms'] is not None else (perf_counter()-started)*1000)
        meta['error']+='；阶段 '+failure_stage[0]+'；'+diagnostic['exception_chain'][0]['summary']
        if diagnostic['missing_files']:meta['error']+='；缺失文件 '+', '.join(diagnostic['missing_files'])
        write_json(task/'analysis_report.json',meta)
        # A cloud failure does not block a local report of the already-saved frame.
        try:
            from .unified_report import export_single
            local=export_single(folder,review=b.get('context',{}).get('manual',{}).get('selection'),cancel=cancel,historical=historical,
                                ai=dict(meta=meta,context=b.get('context'),status='failed'))
            write_bytes(task/'analysis.pdf',Path(local['pdf']).read_bytes())
            meta.update(pdf_complete=True,pdf_kind='base_report_ai_unavailable',pdf_sha256=local['sha256'],pdf_pages=local['pages'],pdf_layout='unified_v1')
            write_json(task/'analysis_report.json',meta)
        except Exception:pass
        return dict(task=str(task),meta=meta)
    finally:
        stage_callback.reset(token)
    if progress:progress('正在生成PDF（本地，不再次调用AI）',0)
    return regenerate_pdf(task,cancel=cancel)


def load_analysis(task):
    task=Path(task);meta=json.loads((task/'analysis_report.json').read_text('utf-8'))
    if meta.get('schema')!='alienhome.ai.v1' or not meta.get('cloud_complete'):raise ValueError('尚无完整AI结果')
    context=meta.get('analysis_context')
    if context is not None:
        expected_context='historical_record' if meta.get('historical') is True else 'current_result'
        if type(meta.get('historical')) is not bool or context!=expected_context:raise ValueError('AI分析上下文与原任务不一致')
    b=bundle(meta['source_record'],analysis_context=context)
    if meta.get('facts_version')=='ANALYSIS_FACTS_V1' and context is None:raise ValueError('AI分析上下文缺失')
    if context is not None:
        facts=json.loads((task/'facts.json').read_text('utf-8'))
        if meta.get('facts_version')=='ANALYSIS_FACTS_V1':
            from .analysis_snapshot import validate_context,upload_facts
            context_raw=(task/'context.json').read_bytes()
            if sha256(context_raw).hexdigest()!=meta['context_sha256']:raise ValueError('AI分析版本快照摘要不符')
            selection=json.loads(context_raw);validate_context(meta['source_record'],selection)
            extra=upload_facts(dict(context=selection));expected=dict(b['facts'],analyses=extra,classification=extra['classification'])
            if selection['signature']!=meta['context_signature'] or facts!=expected:raise ValueError('AI分析上下文快照与上传事实不符')
        elif facts!=b['facts']:raise ValueError('AI上传事实与原任务上下文或源记录不一致')
    if b['identity']!=meta['identity'] or b['record_id']!=meta['record_id'] or b['record_sha256']!=meta['record_sha256']:raise ValueError('AI来源身份不匹配')
    raw=(task/'analysis.json').read_bytes()
    if sha256(raw).hexdigest()!=meta['answer_sha256']:raise ValueError('AI文本摘要不符')
    return meta,validate_schema(json.loads(raw))


def load_display_task(task):
    task=Path(task);meta=json.loads((task/'analysis_report.json').read_text('utf-8'))
    if meta.get('cloud_complete'):return load_analysis(task)
    b=bundle(meta['source_record'],analysis_context=meta.get('analysis_context'))
    if meta['identity']!=b['identity'] or meta['record_sha256']!=b['record_sha256']:raise ValueError('基础报告帧身份不符')
    if not meta.get('pdf_complete') or sha256((task/'analysis.pdf').read_bytes()).hexdigest()!=meta.get('pdf_sha256'):raise ValueError('基础报告尚未完整保存')
    return meta,None


def regenerate_pdf(task,*,cancel=None,include_attachments=False):
    task=Path(task)
    raw_meta=json.loads((task/'analysis_report.json').read_text('utf-8'))
    if not raw_meta.get('cloud_complete'):
        from .unified_report import export_single
        context=json.loads((task/'context.json').read_text('utf-8')) if raw_meta.get('context_sha256') else None
        if context is not None and sha256((task/'context.json').read_bytes()).hexdigest()!=raw_meta['context_sha256']:raise ValueError('分析快照不符')
        from .analysis_snapshot import validate_context
        if context:validate_context(raw_meta['source_record'],context)
        exported=export_single(raw_meta['source_record'],review=context['manual'].get('selection') if context else None,cancel=cancel,historical=raw_meta.get('historical',True),
                               ai=dict(meta=raw_meta,context=context,status='failed'),include_attachments=include_attachments)
        write_bytes(task/'analysis.pdf',Path(exported['pdf']).read_bytes())
        raw_meta.update(pdf_complete=True,pdf_kind='base_report_ai_unavailable',pdf_sha256=exported['sha256'],pdf_layout='unified_v1',pdf_pages=exported['pages'])
        write_json(task/'analysis_report.json',raw_meta);return dict(task=str(task),meta=raw_meta)
    meta,answer=load_analysis(task)
    try:
        from .batch_pdf import export_batch_pdf
        rec=load_record(Path(meta['source_record']));row=dict(item_id=meta['record_id'],filename=rec.request.source_path.name,
                status='success',attempt=1,frame_id=meta['identity']['frame_id'],identity=meta['identity'],record_directory=meta['source_record'])
        summary=dict(status='AI解读',inputs=[dict(item_id=row['item_id'],filename=row['filename'])],attempts=[row])
        context=json.loads((task/'context.json').read_text('utf-8')) if meta.get('context_sha256') else None
        exported=export_batch_pdf(summary,task/'analysis.pdf',historical=meta.get('historical',False) or rec.result.metadata['display_mode']=='offline_replay',
                                  cancel=cancel,ai=dict(meta=meta,answer=answer,context=context),include_attachments=include_attachments,unified=True)
        from PySide6.QtPdf import QPdfDocument
        document=QPdfDocument()
        if document.load(str(task/'analysis.pdf'))!=QPdfDocument.Error.None_ or document.pageCount()<1:raise OSError('PDF不可读取')
        document.close()
        meta.update(pdf_complete=True,status='draft_pending_review' if meta.get('content_policy_version') else 'complete',
                    pdf_sha256=exported['sha256'],pdf_layout=exported.get('report_layout'),pdf_pages=exported['pages'])
    except Exception as exc:
        meta.update(pdf_diagnostic=exception_diagnostic(exc,'pdf.generate_or_validate',include_message=False),pdf_complete=False,status='pdf_failed',pdf_error='PDF生成/保存失败；AI结果已保存，可仅本地重新生成PDF')
    write_json(task/'analysis_report.json',meta)
    return dict(task=str(task),meta=meta)


def recover_saved_response(original_task,record_folder=None,*,cancel=None,expected_identity=None):
    """Reprocess already-paid bytes into a NEW sibling task; no network or credential APIs."""
    started=perf_counter()
    original=Path(original_task).resolve()
    before_report=(original/'analysis_report.json').read_bytes()
    old=json.loads(before_report)
    if old.get('schema')!='alienhome.ai.v1':raise ValueError('不支持的原AI任务格式')
    if not (old.get('transport_complete') or old.get('http_response_received') and 200<=old.get('http_status',0)<300):
        raise ValueError('未记录成功HTTP传输，不能离线冒充成功响应')
    folder=Path(record_folder or old['source_record']).resolve()
    b=bundle(folder,analysis_context=old.get('analysis_context'))
    if (b['identity']!=old['identity'] or b['record_id']!=old['record_id'] or b['record_sha256']!=old['record_sha256']):
        raise ValueError('原记录/帧/RGB/Mask摘要与失败任务不一致')
    if expected_identity is not None and b['identity']!=expected_identity:raise ValueError('原任务不属于当前成功帧')
    facts_raw=(original/'facts.json').read_bytes();facts=json.loads(facts_raw)
    expected=dict(b['facts'])
    context_raw=None
    if old.get('facts_version')=='ANALYSIS_FACTS_V1':
        from .analysis_snapshot import validate_context,upload_facts
        context_raw=(original/'context.json').read_bytes()
        if sha256(context_raw).hexdigest()!=old['context_sha256']:raise ValueError('原AI分析快照摘要不符')
        selected=json.loads(context_raw);validate_context(folder,selected);extra=upload_facts(dict(context=selected))
        expected.update(analyses=extra,classification=extra['classification'])
    if old.get('analysis_context') is None:
        for key in ('analysis_context','source_kind','result_display_mode'):expected.pop(key)
    if facts!=expected:raise ValueError('原上传事实与对应记录不一致，未改写原事实')
    raw_path=original/'response.json'
    if raw_path.stat().st_size>512*1024:raise ValueError('原响应超过大小限制')
    raw=raw_path.read_bytes();digest=sha256(raw).hexdigest()
    if old.get('response_sha256') and old['response_sha256']!=digest:raise ValueError('原响应摘要不一致')
    response=json.loads(raw);choice=response['choices'][0]
    if choice.get('finish_reason')!='stop':raise ValueError('原响应未正常结束')
    if response.get('model')!=old.get('returned_model',old.get('requested_model')):raise ValueError('原响应模型与任务不一致')
    if old.get('response_id') and response.get('id')!=old['response_id']:raise ValueError('原响应ID不一致')
    answer,audit=prepare_draft(json.loads(choice['message']['content']))
    if cancel is not None and cancel.is_set():raise InterruptedError('离线处理已取消')
    if ((original/'analysis_report.json').read_bytes()!=before_report or raw_path.read_bytes()!=raw
            or (original/'facts.json').read_bytes()!=facts_raw):
        raise ValueError('读取期间原任务发生变化，拒绝恢复')
    task=folder/'ai'/('analysis_'+uuid4().hex);task.mkdir(parents=True,exist_ok=False)
    # Facts and raw provider response stay byte-identical; never add source fields retroactively.
    write_bytes(task/'facts.json',facts_raw);write_bytes(task/'response.json',raw)
    if context_raw is not None:write_bytes(task/'context.json',context_raw)
    write_json(task/'analysis.json',answer);write_json(task/'draft_audit.json',audit)
    meta=dict(old)
    for key in ('error','diagnostic','failure_stage','missing_files','pdf_error','pdf_diagnostic'):
        meta.pop(key,None)
    meta.update(original_stage_events=old.get('stage_events',[]),stage_events=[],request_started_local=False,
                original_http_response_received=old.get('http_response_received',False),http_response_received=False,
                cloud_elapsed_scope='original saved request; no new cloud request during offline recovery',
                original_cloud_elapsed_ms=old.get('cloud_elapsed_ms'),
                offline_prepare_ms=(perf_counter()-started)*1000,
                task_id=task.name,source_record=str(folder),cloud_complete=True,transport_complete=True,response_schema_valid=True,
                pdf_complete=False,status='draft_pending_review',review_state='draft_pending_review',answer_kind='postprocessed_AI_draft',
                content_quality_verified=False,content_policy_version=audit['policy_version'],cloud_transport='saved_https_response_offline_reprocess',
                offline_recovery=True,new_cloud_calls=0,automatic_retry=False,original_task_id=old['task_id'],
                original_task_directory=str(original),original_report_sha256=sha256(before_report).hexdigest(),response_sha256=digest,
                original_response_hash_previously_recorded=bool(old.get('response_sha256')),
                original_facts_sha256=sha256(facts_raw).hexdigest(),
                answer_sha256=sha256((task/'analysis.json').read_bytes()).hexdigest(),
                response_id=response.get('id'),returned_model=response.get('model'),usage=response.get('usage'))
    write_json(task/'analysis_report.json',meta)
    result=regenerate_pdf(task,cancel=cancel)
    result['meta']['offline_processing_ms']=(perf_counter()-started)*1000
    write_json(task/'analysis_report.json',result['meta'])
    return result
