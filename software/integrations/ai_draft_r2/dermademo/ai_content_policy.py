"""Reference repair: schema failures are errors; prose numbers become review edits."""
from copy import deepcopy
import re

FIELDS={'image_observations':list,'segmentation_review':str,'review_points':list,'teaching_summary':str,'limitations':list}
NOTICE='AI草稿待人工复核；测量、网格、触边及坐标以本地记录为准。含数字或未经验证的相关表述可能已省略，原始响应另行保留。'
OMITTED='〔该句AI表述未纳入草稿正文，请结合本地指标与原始图像人工复核。〕'

def validate_schema(obj):
    if not isinstance(obj,dict) or set(obj)!=set(FIELDS):
        raise ValueError('云端输出字段与约定不一致，原响应保留供核对。')
    for key,kind in FIELDS.items():
        value=obj[key]
        if not isinstance(value,kind) or not value:raise ValueError('云端字段为空或类型错误：'+key)
        texts=value if kind is list else [value]
        if len(texts)>10 or any(not isinstance(x,str) or not x.strip() or len(x)>2000 for x in texts):
            raise ValueError('云端文字超过约定范围。')
    return obj

def review_sentence(sentence):
    # No model-produced number is promoted to a program-generated measurement.
    # Filtering instead of rejecting the entire successful HTTP response removes
    # dependence on wording, units, decimal precision or ROI/version formatting.
    if re.search(r'\d',sentence):return 'numeric_prose'
    if re.search(r'概率.{0,12}(?:响应|图|输出)',sentence):return 'binary_mask_is_not_probability'
    if 'CLAHE' in sentence and re.search(r'由于|因为|导致|未能|精度受限',sentence):
        return 'unverified_CLAHE_causality'
    if re.search(r'未发生截断|当前网格内未截断|病灶主体未截断',sentence):
        return 'no_edge_does_not_prove_completeness'
    return None

def prepare_draft(obj):
    validate_schema(obj);out=deepcopy(obj);edits=[]
    for key,kind in FIELDS.items():
        texts=out[key] if kind is list else [out[key]]
        for index,text in enumerate(texts):
            # Delimit on Chinese/strong sentence marks; never split decimal dots.
            parts=re.split(r'(?<=[。！？!?；;\n])',text);kept=[]
            for part in parts:
                reason=review_sentence(part)
                if reason:
                    edits.append({'field':key,'item':index,'reason':reason,'original_sentence':part})
                    if not kept or kept[-1]!=OMITTED:kept.append(OMITTED)
                else:kept.append(part)
            texts[index]=''.join(kept).strip() or OMITTED
        out[key]=texts if kind is list else texts[0]
    if NOTICE not in out['limitations']:
        if len(out['limitations'])<10:out['limitations'].append(NOTICE)
        elif not out['limitations'][0].startswith(NOTICE):
            out['limitations'][0]=(NOTICE+' '+out['limitations'][0])[:2000]
    validate_schema(out)
    return out,{'policy_version':'AI_DRAFT_R2_20261030','review_state':'pending_human_review',
                'content_quality_verified':False,'numeric_measurements_from_local_record_only':True,
                'draft_edits':edits,'original_response_preserved':True}
