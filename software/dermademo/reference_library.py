"""Exact-identity reference lookup and raw-Mask scoring; never supplies inference output."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from .analysis_identity import result_identity
from .resources import resource_path

IMPORT_SHA='99e7647446a5aa452b3b929736b3d035ec3b310448792467e6f3a316c714b5a3'
VERSION='DEMO100_REFERENCE_IMPORT_20261031_V1'
KEYS=('source_sha256','input_rgb_sha256','model_version','preprocess_version','roi')
SCOPE='既有验证集高分精选Demo100；非独立测试集，不代表新增100图实板验收。'


def key(identity):
    return tuple(json.dumps(identity.get(k),sort_keys=True,separators=(',',':')) for k in KEYS)


def checked_file(root,relative,spec):
    path=Path(relative)
    if path.is_absolute() or '..' in path.parts:raise ValueError('参考路径越界')
    target=(root/path).resolve()
    if not target.is_relative_to(root) or (root/path).is_symlink():raise ValueError('参考路径越界')
    raw=target.read_bytes()
    if len(raw)!=spec['bytes'] or sha256(raw).hexdigest()!=spec['sha256']:raise ValueError('参考资产摘要不符：'+relative)
    return raw


@lru_cache(maxsize=4)
def catalog(root):
    root=Path(root).resolve();raw=(root/'IMPORT_MANIFEST.json').read_bytes()
    if sha256(raw).hexdigest()!=IMPORT_SHA:raise ValueError('参考库导入清单摘要不符')
    manifest=json.loads(raw)
    index=json.loads(checked_file(root,'LIBRARY_INDEX.json',manifest['files']['LIBRARY_INDEX.json']))
    if index.get('schema')!='member1.demo100_reference_library.v1' or index['count']!=100 or len(index['entries'])!=100:
        raise ValueError('Demo100索引格式或数量不符')
    entries={}
    for entry in index['entries']:
        identity=entry['identity']
        if set(identity)!=set(KEYS) or identity['roi'] is not None:raise ValueError('Demo100仅提供整图身份')
        k=key(identity)
        if k in entries:raise ValueError('参考库出现重复身份')
        entries[k]=entry
    return manifest,entries


def read_decl(root,manifest,entry,kind):
    from .validation import binary_mask
    relative=entry[kind];raw=checked_file(root,relative,manifest['files'][relative]);doc=json.loads(raw)
    if doc.get('schema')!='alienhome.reference.v1' or doc['identity']!=entry['identity'] or doc['kind']!=kind:
        raise ValueError('参考声明与索引不符')
    mask_path=(Path(relative).parent/doc['mask_file']).as_posix()
    mask_raw=checked_file(root,mask_path,manifest['files'][mask_path])
    if sha256(mask_raw).hexdigest()!=doc['mask_sha256']:raise ValueError('参考Mask与声明摘要不符')
    if kind=='annotation':
        rule=doc.get('mapping_rule',{})
        if rule.get('confirmed_by')!='member2' or not rule.get('version') or rule.get('grid')!=[256,256] or rule.get('roi') is not None or rule.get('no_resize_or_threshold') is not True:
            raise ValueError('人工标注网格映射未确认')
    return dict(mask=binary_mask(mask_raw),kind=kind,note=doc['source_note']+'；'+SCOPE,
                provenance=dict(library=VERSION,library_manifest_sha256=IMPORT_SHA,declaration=relative,
                    declaration_sha256=sha256(raw).hexdigest(),reference_mask_sha256=doc['mask_sha256'],
                    mapping_rule=doc.get('mapping_rule'),dataset_scope='existing_validation_high_score_selection'))


def references(pair,*,root=None):
    """Read matching assets only; name is never part of the lookup key."""
    identity=result_identity(pair);out=dict(int8=None,annotation=None,errors={})
    if identity['source_sha256'] is not None:
        raw=pair[0].source_bytes
        if raw is None or sha256(raw).hexdigest()!=identity['source_sha256']:
            out['errors']={k:'原图字节缺失或SHA不符，不能匹配参考' for k in ('int8','annotation')};return out
    try:
        folder=Path(root or resource_path('reference_library')).resolve();manifest,entries=catalog(str(folder))
        entry=entries.get(key(identity))
        if entry:
            for kind in ('int8','annotation'):
                try:
                    value=read_decl(folder,manifest,entry,kind);value['identity']=identity;out[kind]=value
                except (OSError,ValueError,KeyError,TypeError) as exc:out['errors'][kind]=str(exc)
            return out
    except (OSError,ValueError,KeyError,TypeError) as exc:
        out['errors']={k:str(exc) for k in ('int8','annotation')}
    # Existing certified whole-image/ROI/fixed-RAW goldens stay available.
    from .validation import _legacy_bundled_reference
    try:out['int8']=_legacy_bundled_reference(pair)
    except (OSError,ValueError,KeyError,TypeError) as exc:out['errors']['int8']=str(exc)
    return out


def compare(pair,*,expected=None,root=None):
    from .validation import mask_comparison
    started=perf_counter();identity=result_identity(pair);refs=references(pair,root=root)
    report=dict(schema='derma.reference_comparison.v1',version=VERSION,identity=identity,
                mask_variant='raw_fpga',scope='model256_raw_mask_only',is_new_board_acceptance=False)
    for kind in ('int8','annotation'):
        ref=refs[kind];previous=(expected or {}).get(kind,{})
        if ref is None:
            report[kind]=dict(status='unavailable',reason=refs['errors'].get(kind,'无匹配参考；需原图SHA、实际RGB SHA、模型、预处理及ROI全部一致'))
            continue
        digest=sha256(ref['mask'].tobytes()).hexdigest()
        if expected and (expected.get('identity')!=identity or previous.get('status')=='matched' and previous.get('reference_mask_sha256')!=digest):
            report[kind]=dict(status='unavailable',reason='保存的对照身份或参考版本不符；未沿用旧分数');continue
        result=mask_comparison(pair[1].mask,ref['mask'],kind=kind)
        metrics={k:v for k,v in result.items() if k!='difference_bgr'}
        report[kind]=dict(status='matched',reference_mask_sha256=digest,provenance=ref.get('provenance',{'library':'legacy_certified_golden'}),
                          note=ref['note'],**metrics)
        if kind=='int8':report[kind]['different_bytes']=result['different_pixels']
    report['elapsed_ms']=(perf_counter()-started)*1000
    return report


def update(pair):
    """Derived comparison failures must not turn a successful segmentation into failure."""
    try:value=compare(pair)
    except Exception as exc:
        value=dict(schema='derma.reference_comparison.v1',version=VERSION,identity=result_identity(pair),mask_variant='raw_fpga',
                   int8=dict(status='unavailable',reason=type(exc).__name__+': '+str(exc)),
                   annotation=dict(status='unavailable',reason=type(exc).__name__+': '+str(exc)),is_new_board_acceptance=False)
    previous=pair[1].metadata.get('reference_comparison')
    if isinstance(previous,dict) and {k:v for k,v in previous.items() if k!='elapsed_ms'}=={k:v for k,v in value.items() if k!='elapsed_ms'}:
        value=previous  # Preserve the saved observation when the verified comparison has not changed.
    pair[1].metadata['reference_comparison']=value
    return value


def brief(report):
    a,b=report.get('int8',{}),report.get('annotation',{})
    integer=(f"{a['different_bytes']}字节差异"+('（一致）' if a['byte_exact'] else '（不一致）')) if a.get('status')=='matched' else '不可用'
    quality=f"Dice {b['dice']:.6f} / IoU {b['iou']:.6f}" if b.get('status')=='matched' else '不可用'
    return '整数一致性：'+integer+'；人工标注：'+quality


def verify_library():
    from .validation import mask_comparison
    import csv,io
    import numpy as np
    from PIL import Image
    folder=resource_path('reference_library');manifest,entries=catalog(str(folder.resolve()))
    for relative,spec in manifest['files'].items():checked_file(folder,relative,spec)
    rows={r['filename']:r for r in csv.DictReader((folder/'provenance/samples_member2.csv').read_text('utf-8-sig').splitlines())}
    if len(rows)!=100:raise ValueError('成员2来源索引不是100图')
    for entry in entries.values():
        source=rows[entry['filename']]
        expected=dict(source_sha256=source['original_sha256'],input_rgb_sha256=source['rgb888_sha256'],
            model_version=source['model'],preprocess_version='FULL_IMAGE_RGB888_V1_20261025',roi=None)
        if expected!=entry['identity']:raise ValueError('参考索引与成员2原图/输入身份不一致')
        a=read_decl(folder,manifest,entry,'int8');b=read_decl(folder,manifest,entry,'annotation')
        png=folder/Path(entry['annotation']).parent/'annotation_grid256_member2.png'
        with Image.open(png) as image:
            if image.mode!='L' or image.size!=(256,256) or not np.array_equal(np.asarray(image),b['mask']):
                raise ValueError('人工标注二值字节与成员2原始网格PNG不一致')
        if sha256(png.read_bytes()).hexdigest()!=source['gt256_sha256'] or sha256(a['mask'].tobytes()).hexdigest()!=source['int8_mask_bin_sha256']:
            raise ValueError('参考Mask/标注与成员2原资产摘要不符')
        result=mask_comparison(a['mask'],b['mask'],kind='annotation')
        if abs(result['dice']-entry['existing_reference_dice'])>1e-10 or abs(result['iou']-entry['existing_reference_iou'])>1e-10:
            raise ValueError('参考Mask/标注与既有指标不一致')
    return dict(status='PASS',pairs=len(entries),declarations=200,physical_FPGA_tests=0,scope=SCOPE,manifest_sha256=IMPORT_SHA)


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='本地参考资产校验与原始Mask对照；不发送FPGA或云端请求')
    p.add_argument('action',choices=('check','compare'));p.add_argument('--record',type=Path)
    args=p.parse_args(argv)
    try:
        if args.action=='check':value=verify_library()
        else:
            if args.record is None:raise ValueError('compare需要--record完整四窗记录目录')
            from .records import load_record
            record=load_record(args.record);value=compare((record.request,record.result))
        print(json.dumps(value,ensure_ascii=True));return 0
    except Exception as exc:print(json.dumps(dict(status='unavailable',error=str(exc)),ensure_ascii=True));return 1
