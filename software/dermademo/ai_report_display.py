"""Presentation only: original AI files and their hashes are never edited."""
from copy import deepcopy

PLACEHOLDER='〔该句AI表述未纳入草稿正文，请结合本地指标与原始图像人工复核。〕'
OLD_NOTICE='AI草稿待人工复核；测量、网格、触边及坐标以本地记录为准。含数字或未经验证的相关表述可能已省略，原始响应另行保留。'
REPORT_NOTICE='AI解读待人工复核，部分未核实表述已省略；数值以本地记录为准。'

def presentation_copy(answer):
    """Return only usable text for UI/PDF. Empty sections should be hidden."""
    out=deepcopy(answer)
    def clean(text):
        return text.replace(PLACEHOLDER,'').replace(OLD_NOTICE,'').strip().rstrip('；;')
    for key,value in out.items():
        if isinstance(value,list):out[key]=[text for item in value if (text:=clean(item))]
        else:out[key]=clean(value)
    return out


def result_summary(answer):
    """A filtered fragment or a general teaching note is not a case conclusion.

    This is a display heuristic, not semantic/medical validation. Raw response,
    R2 audit and the saved answer remain intact.
    """
    text=str(answer.get('teaching_summary','')).strip()
    if not text or text.startswith(('此外','另外','同时','因此','前者','后者','综上','教学重点','本案例展示','仅用于')):
        return '未形成有效结果摘要，待人工审核。'
    if any(word in text for word in ('需明确区分','教学重点在于','不代表临床意义上的完整性')):
        return '未形成有效结果摘要，待人工审核。'
    return text
