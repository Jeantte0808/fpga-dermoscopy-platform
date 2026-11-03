"""Portable ancestors for ROI experiments; original records are never edited."""
from pathlib import Path
import json
from .storage import save_result
from .records import load_record
from .analysis_identity import result_identity
from .device.formal_session import write_json


def save(parent, pair, ancestors):
    if len(ancestors)>32:raise ValueError('ROI实验链超过32项，请开始新链')
    Path(parent).mkdir(parents=True,exist_ok=True)
    folder=save_result(parent,*pair)
    marker=folder/'ROI_CHAIN_INCOMPLETE.txt'
    if not ancestors:return folder
    marker.write_text('ROI ancestors not committed\n','utf-8')
    (folder/'roi_history').mkdir()
    rows=[]
    for ancestor in ancestors:
        saved=save_result(folder/'roi_history',*ancestor)
        rows.append(dict(path=saved.relative_to(folder).as_posix(),identity=result_identity(ancestor)))
    write_json(folder/'roi_chain.json',dict(schema='roi_chain_v1',current=result_identity(pair),ancestors=rows,
                                          note='不同ROI的模型256网格测量不可直接比较成病灶大小变化'))
    marker.unlink()
    return folder


def load(folder, pair):
    folder=Path(folder).resolve()
    if (folder/'ROI_CHAIN_INCOMPLETE.txt').exists():raise ValueError('ROI父子链未完整保存，当前原始结果仍可单独打开')
    index=folder/'roi_chain.json'
    if not index.exists():return []
    if index.stat().st_size>262144:raise ValueError('ROI链索引过大')
    doc=json.loads(index.read_text('utf-8'))
    if doc.get('schema')!='roi_chain_v1' or doc.get('current')!=result_identity(pair) or len(doc['ancestors'])>32:
        raise ValueError('ROI链身份无效')
    result=[]
    for row in doc['ancestors']:
        path=(folder/row['path']).resolve()
        if not path.is_relative_to(folder):raise ValueError('ROI父记录路径越界')
        record=load_record(path);ancestor=record.request,record.result
        if result_identity(ancestor)!=row['identity']:raise ValueError('ROI父记录身份不符')
        if result_identity(ancestor)['source_sha256']!=result_identity(pair)['source_sha256']:
            raise ValueError('ROI父子原图不同，不能叠加对照')
        result.append(ancestor)
    expansion=pair[1].metadata.get('roi_expansion')
    if expansion and (not result or expansion.get('parent_identity')!=result_identity(result[-1])):
        raise ValueError('ROI扩框父身份与保存链不符')
    return result
