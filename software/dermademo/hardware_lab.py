"""Serial, durable CPU/FPGA experiments. No cloud calls or protocol extensions."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import csv
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median
from threading import Event
from time import perf_counter
from uuid import uuid4
import shutil

from . import __version__
from .analysis_identity import result_identity
from .cpu_baseline import CPUClient, agreement
from .device.formal_session import run_formal, transaction_state, write_json, write_bytes
from .formal_queue import freeze_input, read_input
from .formal_results import live_pair
from .runtime_paths import data_root
from .storage import save_result
from .validation import performance_snapshot
from .mask_statistics import measure

SCHEMA = 'hardware_experiment_v1'
COMPARISON_KEYS = ('input_rgb_sha256', 'source_sha256', 'model_version', 'preprocess_version', 'roi')


def numbers(values):
    values = sorted(float(x) for x in values if type(x) in (int, float) and isfinite(x) and x >= 0)
    if not values:
        return dict(n=0, mean_ms=None, median_ms=None, p95_ms=None, min_ms=None, max_ms=None)
    # Nearest rank, explicitly persisted; no interpolation across missing trials.
    return dict(n=len(values), mean_ms=mean(values), median_ms=median(values),
                p95_ms=values[(95*len(values)+99)//100-1], min_ms=values[0], max_ms=values[-1])


def evidence(pair):
    snap = performance_snapshot(pair)
    report = pair[1].metadata.get('wire_report') or {}
    profile = pair[1].metadata.get('wire_profile') or {}
    snap.update(client_version=report.get('client_version'),
                fixed_threshold=report.get('fixed_logits_threshold'),
                stage_schema=profile.get('schema'), stage_clock_hz=profile.get('ui_clock_hz'),
                total_counters=profile.get('total') if profile.get('complete') else None,
                pc_statistics=measure(pair[1].mask.tobytes()),
                board_statistics_status='unavailable_protocol_not_frozen')
    # Missing summary profiles must not look like full stage observations.
    snap['stages'] = sorted(snap['stages'], key=lambda x: x.get('cycles', 0), reverse=True)
    return snap


def compare_evidence(before, after):
    reasons = [key for key in COMPARISON_KEYS if before['identity'].get(key) != after['identity'].get(key)]
    reasons.extend(key+'_missing' for key in COMPARISON_KEYS if key != 'roi' and
                   (before['identity'].get(key) is None or after['identity'].get(key) is None))
    for key in ('client_version', 'telemetry_mode', 'fixed_threshold'):
        if before.get(key) is None or after.get(key) is None or before.get(key) != after.get(key):
            reasons.append(key)
    if before['identity']['source_kind'] == 'protocol_simulator' or after['identity']['source_kind'] == 'protocol_simulator':
        reasons.append('protocol_simulator_not_hardware_evidence')
    if not before.get('hardware_declaration') or not after.get('hardware_declaration'):
        reasons.append('hardware_declaration_missing')
    metrics = {}
    for key in ('graph', 'e2e'):
        a, b = before['values'].get(key), after['values'].get(key)
        metrics[key] = dict(before_ms=a, after_ms=b,
                            reduction_percent=(a-b)/a*100 if not reasons and a is not None and b is not None and a > 0 else None)
    stage_compatible = (not reasons and before.get('stage_schema') == after.get('stage_schema')
                        and before.get('stage_clock_hz') == after.get('stage_clock_hz')
                        and before.get('stage_clock_hz') is not None
                        and bool(before.get('stages')) and bool(after.get('stages')))
    # A schema match alone cannot certify a changed RTL's stage layout.
    return dict(eligible=not reasons, reasons=reasons, metrics=metrics,
                stage_layout_requires_confirmation=bool(stage_compatible),
                mask_equal=before['identity']['raw_mask_sha256'] == after['identity']['raw_mask_sha256'],
                note='单帧条件核对，不构成多帧提速结论；阶段布局需硬件成员确认；部署声明非自动识别')


def summarize(doc):
    rows = doc['trials']
    result = {}
    for backend in ('cpu_fp32', 'fpga_int8'):
        selected = [r for r in rows if r['backend'] == backend and not r['warmup']]
        good = [r for r in selected if r['status'] == 'success']
        result[backend] = dict(success=len(good), failed=sum(r['status'] == 'failed' for r in selected),
            cancelled=sum(r['status'] == 'cancelled' for r in selected),
            not_run=sum(r['status'] == 'not_run' for r in selected),
            common_rgb_to_mask=numbers([r.get('common_rgb_to_mask_ms') for r in good]),
            graph=numbers([r.get('graph_ms') for r in good]))
    wall = doc.get('measurement_wall_ms')
    result['fpga_pipeline_frames_per_s'] = (result['fpga_int8']['success']*1000/wall
        if doc['mode'] == 'fpga_sequence' and doc['status'] == 'completed' and wall and result['fpga_int8']['success'] and
        all(r['origin'] == 'live_FPGA_session' for r in rows if not r['warmup'] and r['status'] == 'success') else None)
    result['throughput_scope'] = ('FPGA-only measured trial window including serial IO, stats, necessary saving; '
                                  'not compute-only FPS; partial runs and simulations have no FPS claim')
    return result


def persist(folder, doc):
    doc['summary'] = summarize(doc)
    write_json(folder/'experiment.json', doc)
    fields = ('trial_id','backend','input_index','repeat','warmup','status','frame_id','generation',
              'common_rgb_to_mask_ms','graph_ms','e2e_ms','model_load_ms','origin','record','error')
    import io
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(doc['trials'])
    write_bytes(folder/'trials.csv', ('\ufeff'+stream.getvalue()).encode('utf-8'))


def run(prepared_inputs, config, output, *, python='', warmup=5, repeats=20,
        mode='paired', cancel=None, progress=None, state_dir=None):
    """One resident CPU and one shared OS device lock for the whole experiment."""
    if (type(warmup) is not int or type(repeats) is not int or not 0 <= warmup <= 20 or not 1 <= repeats <= 200
            or mode not in ('paired','fpga_sequence') or not 1 <= len(prepared_inputs) <= 100
            or (warmup+repeats)*len(prepared_inputs)*(2 if mode == 'paired' else 1) > 1000):
        raise ValueError('实验输入/次数无效或超过1000次尝试上限')
    cancel, progress = cancel or Event(), progress or (lambda *_:None)
    folder = Path(output)/('experiment_'+uuid4().hex[:16]); folder.mkdir(parents=True, exist_ok=False)
    write_bytes(folder/'INCOMPLETE.txt', b'Experiment not fully committed\n')
    items = [freeze_input(p.path,folder,prepared=p) for p in prepared_inputs]
    plans = []
    for repeat in range(warmup+repeats):
        for i in range(len(items)):
            backends = ('cpu_fp32','fpga_int8') if mode == 'paired' else ('fpga_int8',)
            if repeat % 2: backends = tuple(reversed(backends))
            for backend in backends:
                plans.append(dict(trial_id=len(plans)+1,backend=backend,input_index=i,repeat=repeat+1,
                                  warmup=repeat<warmup,status='not_run',origin='not_run'))
    doc = dict(schema=SCHEMA,application_version=__version__,experiment_id=folder.name,
               created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
               status='running',mode=mode,warmup=warmup,repeats=repeats,config=asdict(config),
               p95_method='nearest_rank',cpu_precision='FP32',fpga_precision='INT8',
               timing_scope='backend ready: CPU host_request incl IPC; FPGA IMAGE_BEGIN through legal FINISH; excludes load/telemetry/save',
               trials=plans,measurement_wall_ms=None,load_ms=None,cloud_calls=0,
               inputs=[dict(folder=i.folder.relative_to(folder).as_posix(),item_id=i.item_id,filename=i.filename,
                            manifest_sha256=i.manifest_sha256,roi=i.roi) for i in items])
    persist(folder, doc)
    client = None; reuse = config.reuse_model; measured_started = None; outcome = None
    paired_masks={}
    try:
        with transaction_state(state_dir or data_root()/'formal_mask') as transaction:
            if mode == 'paired': client = CPUClient(python)
            for row in plans:
                if cancel.is_set():
                    doc['status']='cancelled'; break
                if not row['warmup'] and measured_started is None: measured_started=perf_counter()
                prepared = read_input(items[row['input_index']])
                row.update(status='running',input_rgb_sha256=sha256(prepared.rgb).hexdigest())
                persist(folder,doc)  # A journal failure prevents a new board frame.
                progress(('预热' if row['warmup'] else '测量')+' '+row['backend'],row['trial_id'])
                try:
                    if shutil.disk_usage(folder).free < 64*1024*1024:raise OSError('可用空间不足64MiB，实验停止并保留已有记录')
                    if row['backend'] == 'cpu_fp32':
                        mask, report = client.run(prepared.rgb,cancel=cancel)
                        rawdir=folder/'cpu'/str(row['trial_id']);rawdir.mkdir(parents=True)
                        write_bytes(rawdir/'mask.raw',mask);write_json(rawdir/'report.json',report)
                        row.update(common_rgb_to_mask_ms=report['host_request_ms'],
                                   cpu_rgb_to_mask_ms=report['report'].get('rgb_to_mask_ms'),
                                   model_load_ms=report['runtime']['load_ms'],origin='local_CPU_FP32',
                                   mask_sha256=sha256(mask).hexdigest(),mask_path=(rawdir/'mask.raw').relative_to(folder).as_posix())
                        doc['cpu_environment']=report['runtime']
                        paired_masks.setdefault((row['repeat'],row['input_index']),{})['cpu']=(mask,row)
                    else:
                        outcome=run_formal(prepared.rgb,replace(config,reuse_model=reuse),folder/'captures',
                            cancel=cancel,progress=progress,input_context=prepared.context,source_bytes=prepared.source_bytes,
                            cpu_preprocessing_ms=prepared.cpu_preprocessing_ms,_transaction=transaction)
                        report=outcome.report
                        row.update(frame_id=report.get('frame_id'),generation=report.get('generation'),
                                   origin=report['origin'],capture=outcome.directory.relative_to(folder).as_posix(),
                                   finish_confirmed=report.get('hardware_finish_confirmed'),
                                   diagnostics={k:report.get(k) for k in ('BUSY','retries','invalid_replies','error','failure_domain','manual_recovery_required')})
                        if not report.get('complete'): raise RuntimeError(report.get('error') or 'FPGA记录未完整保存')
                        pair=live_pair(outcome)
                        (folder/'records').mkdir(exist_ok=True)
                        record=save_result(folder/'records',*pair)
                        row.update(record=record.relative_to(folder).as_posix(),identity=result_identity(pair),
                                   common_rgb_to_mask_ms=report.get('rgb_to_valid_mask_ms'),
                                   graph_ms=pair[1].timings_ms.get('fpga_graph'),e2e_ms=report.get('image_E2E_ms'),
                                   model_load_ms=report.get('model_upload_ms'),stage_timings_ms=report.get('stage_timings_ms'),
                                   reference_comparison=pair[1].metadata.get('reference_comparison'))
                        row['mask_path']=(outcome.directory/'mask.raw').relative_to(folder).as_posix()
                        from .reference_library import references
                        paired_masks.setdefault((row['repeat'],row['input_index']),{})['fpga']=(outcome.mask,row,references(pair).get('annotation'))
                        reuse=True
                    paired=paired_masks.get((row['repeat'],row['input_index']),{})
                    if 'cpu' in paired and 'fpga' in paired:
                        cpu,cr=paired['cpu'];fpga,fr,annotation=paired['fpga']
                        cr['fpga_agreement']=agreement(cpu,fpga)
                        if annotation is not None:
                            from .validation import binary_mask,mask_comparison
                            score=mask_comparison(binary_mask(cpu),annotation['mask'],kind='annotation')
                            cr['annotation_scores']={key:score[key] for key in ('dice','iou')}
                        paired_masks.pop((row['repeat'],row['input_index']),None)
                    row['status']='success'
                except Exception as exc:
                    row.update(status='cancelled' if cancel.is_set() else 'failed',error=f'{type(exc).__name__}: {exc}')
                    doc.update(status=row['status'],error=row['error'])
                    break  # No unconfirmed recovery or implicit backend retry.
                finally:
                    if measured_started is not None:doc['measurement_wall_ms']=(perf_counter()-measured_started)*1000
                    persist(folder,doc)
            else: doc['status']='completed'
    except Exception as exc:
        doc.update(status='cancelled' if cancel.is_set() else 'failed',error=f'{type(exc).__name__}: {exc}')
    finally:
        if client is not None:client.close()
        doc['ended_at']=datetime.now(timezone.utc).isoformat(timespec='seconds')
        persist(folder,doc)
    # Seal all saved results; incomplete trials remain explicitly recorded.
    doc['files']={p.relative_to(folder).as_posix():dict(bytes=p.stat().st_size,sha256=sha256(p.read_bytes()).hexdigest())
                  for p in folder.rglob('*') if p.is_file() and p.name not in ('experiment.json','INCOMPLETE.txt')}
    write_json(folder/'experiment.json',doc)
    (folder/'INCOMPLETE.txt').unlink()
    return folder, doc


def reopen(folder):
    folder=Path(folder).resolve()
    if (folder/'experiment.json').stat().st_size > 16*1024*1024:raise ValueError('实验索引过大')
    doc=json.loads((folder/'experiment.json').read_text('utf-8'))
    if (doc.get('schema') != SCHEMA or not 1<=len(doc.get('inputs',[]))<=100
            or not 1<=len(doc.get('trials',[]))<=1000):raise ValueError('实验格式不支持')
    def path(relative):
        if not isinstance(relative,str) or Path(relative).is_absolute() or '..' in Path(relative).parts:raise ValueError('实验引用路径越界')
        p=(folder/relative).resolve()
        if not p.is_relative_to(folder):raise ValueError('实验引用路径越界')
        return p
    for relative,spec in doc.get('files',{}).items():
        p=path(relative)
        if not p.is_relative_to(folder) or not p.is_file() or p.stat().st_size != spec['bytes'] or sha256(p.read_bytes()).hexdigest()!=spec['sha256']:
            raise ValueError('实验附件校验失败：'+relative)
    inputs=[]
    from .formal_queue import QueueInput
    for row in doc['inputs']:
        item=QueueInput(row['item_id'],row['filename'],path(row['folder']),row['manifest_sha256'],tuple(row['roi']) if row['roi'] else None)
        inputs.append(sha256(read_input(item).rgb).hexdigest())
    for row in doc['trials']:
        index=row['input_index']
        if type(index) is not int or not 0<=index<len(inputs) or row['status'] not in ('not_run','running','success','failed','cancelled'):
            raise ValueError('实验尝试结构无效')
        if row.get('input_rgb_sha256') and row['input_rgb_sha256']!=inputs[index]:raise ValueError('实验尝试输入身份不符')
        if row.get('mask_path'):
            raw=path(row['mask_path']).read_bytes()
            if len(raw)!=65536 or not set(raw)<={0,255}:raise ValueError('实验Mask无效')
            expected=row.get('mask_sha256') or row.get('identity',{}).get('raw_mask_sha256')
            if expected!=sha256(raw).hexdigest():raise ValueError('实验Mask身份不符')
        if row.get('record'):
            from .records import load_record
            record=load_record(path(row['record']))
            if result_identity((record.request,record.result))!=row['identity']:raise ValueError('实验记录身份不符')
    if not (folder/'INCOMPLETE.txt').exists() and doc.get('summary') != summarize(doc):
        raise ValueError('实验汇总与尝试记录不符')
    return doc


def successful_frames(doc):
    """FPGA results only, preserving trial order and the warmup designation."""
    return [row for row in doc['trials'] if row['backend']=='fpga_int8' and row['status']=='success']


def load_frame(folder, doc, trial_id):
    """Read one sealed frame; never run a model or contact the board."""
    from .records import load_record
    from .formal_queue import QueueInput
    import numpy as np
    folder=Path(folder).resolve()
    matches=[r for r in successful_frames(doc) if r['trial_id']==trial_id]
    if len(matches)!=1:raise ValueError('所选尝试不是本实验唯一的成功FPGA帧')
    row=matches[0]
    def checked(relative, *, directory=False):
        if not isinstance(relative,str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('实验帧引用路径越界')
        full=(folder/relative).resolve()
        if not full.is_relative_to(folder):raise ValueError('实验帧引用路径越界')
        names=[n for n in doc.get('files',{}) if n.startswith(relative.rstrip('/')+'/')] if directory else [relative]
        if not names:raise ValueError('实验帧缺少保存文件清单')
        for name in names:
            p=(folder/name).resolve();spec=doc['files'].get(name)
            if (not p.is_relative_to(folder) or not spec or not p.is_file() or
                    p.stat().st_size!=spec['bytes'] or sha256(p.read_bytes()).hexdigest()!=spec['sha256']):
                raise ValueError('实验帧附件校验失败：'+name)
        return full
    source=doc['inputs'][row['input_index']]
    item=QueueInput(source['item_id'],source['filename'],checked(source['folder'],directory=True),
                    source['manifest_sha256'],tuple(source['roi']) if source['roi'] else None)
    prepared=read_input(item)
    record=load_record(checked(row['record'],directory=True));pair=record.request,record.result
    identity=result_identity(pair)
    rgb=np.ascontiguousarray(record.request.image_bgr[:,:,::-1]).tobytes()
    if (identity!=row['identity'] or rgb!=prepared.rgb or identity['frame_id']!=row['frame_id']
            or identity['generation']!=row['generation'] or sha256(rgb).hexdigest()!=row['input_rgb_sha256']):
        raise ValueError('所选帧与实验输入/记录/帧号不符')
    snap=evidence(pair)
    snap.update(experiment_id=doc['experiment_id'],trial_id=trial_id,filename=source['filename'],
                repeat=row['repeat'],warmup=row['warmup'],evidence_scope='selected_experiment_frame')
    previews=[record.request.image_bgr.copy(),None,record.result.mask.copy()]
    cpu=next((r for r in doc['trials'] if r['backend']=='cpu_fp32' and r['status']=='success'
              and r['input_index']==row['input_index'] and r['repeat']==row['repeat']
              and r['warmup']==row['warmup'] and r.get('input_rgb_sha256')==row['input_rgb_sha256']),None)
    if cpu is not None:
        try:
            raw=checked(cpu['mask_path']).read_bytes()
            if len(raw)!=65536 or not set(raw)<={0,255} or sha256(raw).hexdigest()!=cpu['mask_sha256']:
                raise ValueError('同轮CPU Mask身份无效')
            previews[1]=np.frombuffer(raw,np.uint8).reshape(256,256).copy()
        except (ValueError,OSError,KeyError) as exc:snap['cpu_preview_unavailable']=str(exc)
    return dict(snapshot=snap,previews=previews,row=row)
