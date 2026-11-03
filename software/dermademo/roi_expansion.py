"""Deterministic original-space expansion; does not prepare or send an image."""
from datetime import datetime, timezone
from uuid import uuid4
from .roi import validate_rect
from .analysis_identity import result_identity

VERSION = 'roi_expand_each_side_v1'


def expand(rect, width, height, percent=25):
    rect = validate_rect(rect, width, height)
    if type(percent) is not int or percent not in (10, 25, 50):
        raise ValueError('每侧扩展比例必须是10%、25%或50%')
    x0, y0, x1, y1 = rect
    dx = max(1, ((x1-x0)*percent+99)//100)
    dy = max(1, ((y1-y0)*percent+99)//100)
    return (max(0, x0-dx), max(0, y0-dy), min(width, x1+dx), min(height, y1+dy))


def proposal(pair, rect, width, height, percent, *, parent_record=None):
    identity = result_identity(pair)
    if identity['roi'] is None or tuple(identity['roi']) != tuple(rect):
        raise ValueError('只能根据最近成功ROI创建扩框，不引用未推理的草稿')
    previous = pair[1].metadata.get('roi_expansion') or {}
    return dict(schema=VERSION, chain_id=previous.get('chain_id') or uuid4().hex,
                parent_record=str(parent_record) if parent_record else None,
                parent_identity=identity, previous_roi=list(rect),
                proposed_roi=list(expand(rect, width, height, percent)), percent_each_side=percent,
                triggered_edges=(pair[1].metadata.get('roi_edge_contact') or {}).get('result'),
                created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                confirmed_at=None, status='draft')
