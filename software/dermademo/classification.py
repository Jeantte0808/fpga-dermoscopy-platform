"""Source-bound standalone classification records, independent of segmentation/AI."""
import base64,io,json,math
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from PIL import Image
from .cpu_baseline import CPUClient
from .device.formal_session import write_json,write_bytes

MODEL='HAM10000_SMALL900_MOBILENETV2_CLASSIFIER_FP32_V2'
WEIGHT='654cfab8e37e2a9b7bf5eb24c62ae1c1f1e7e4247a9dcbfc5531c1d1af4f3965'
CLASSES=['non_melanoma_group','melanoma']
NOTICE='教学候选：非黑色素瘤组包含其他皮肤癌，不等于良性；模型分数不是患病概率。仅整张原图分类。'

def validate(result,raw,preview):
    if (result.get('schema')!='skin.classification.candidate.v1' or result.get('model_version')!=MODEL
        or result.get('weight_sha256')!=WEIGHT or result.get('best_epoch')!=7
        or result.get('device')!='cpu' or result.get('dtype')!='float32'
        or result['identity']['source_sha256']!=sha256(raw).hexdigest()
        or result['identity']['preprocess_version']!='CLASS_RGB_EXIF_SHORT232_CENTERCROP224_IMAGENET_V1'
        or result['identity'].get('jpeg_reencoding') is not False):raise ValueError('分类版本/原图身份不匹配')
    scores=result['class_scores']
    if len(scores)!=2 or [s['category'] for s in scores]!=CLASSES or [s['index'] for s in scores]!=[0,1]:raise ValueError('分类顺序不匹配')
    values=[s['score'] for s in scores]
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in values) or abs(sum(values)-1)>1e-5:raise ValueError('模型分数无效')
    if result['predicted_class']!=CLASSES[int(values[1]>values[0])]:raise ValueError('分类决策不匹配')
    with Image.open(io.BytesIO(preview)) as im:
        if im.size!=(224,224) or im.mode!='RGB' or sha256(im.tobytes()).hexdigest()!=result['identity']['processed_rgb_sha256']:raise ValueError('分类224输入摘要无效')
    return result

class ClassifierClient(CPUClient):
    worker_resource='classifier_worker'
    package_resource='classifier_root'
    def _start(self,cancel):
        previous=self.process
        super()._start(cancel)
        if self.process is not previous:self.model_load_count=getattr(self,'model_load_count',0)+1
    def predict(self,path,cancel=None):
        with self._lock:
            self._start(cancel)
            self.process.stdin.write(json.dumps(dict(path=str(Path(path).resolve())))+'\n');self.process.stdin.flush()
            return self._receive(120,cancel)

def run(client,raw,name,parent,*,cancel=None):
    if Path(name).name!=name or Path(name).suffix.lower() not in ('.jpg','.jpeg','.png'):raise ValueError('原文件名无效')
    folder=Path(parent)/('classification_'+uuid4().hex);folder.mkdir(parents=True,exist_ok=False)
    write_bytes(folder/'INCOMPLETE.txt',b'classification not complete')
    source=folder/name;write_bytes(source,raw)
    value=client.predict(source,cancel);preview=base64.b64decode(value['preview'],validate=True)
    result=validate(value['result'],raw,preview)
    if source.read_bytes()!=raw:raise ValueError('分类期间原图发生变化')
    write_bytes(folder/'actual_224_input.png',preview);write_json(folder/'result.json',result)
    manifest=dict(schema='alienhome.classification.v1',created_at=datetime.now().astimezone().isoformat(timespec='seconds'),source_filename=name,source_sha256=sha256(raw).hexdigest(),
                  result_sha256=sha256((folder/'result.json').read_bytes()).hexdigest(),preview_sha256=sha256(preview).hexdigest(),
                  scope='whole_original_image',status='teaching_candidate',live_CPU_run=True)
    write_json(folder/'classification.json',manifest);(folder/'INCOMPLETE.txt').unlink()
    return folder

def load(folder,*,source_sha=None):
    folder=Path(folder)
    if (folder/'INCOMPLETE.txt').exists():raise ValueError('分类记录未完整保存')
    meta=json.loads((folder/'classification.json').read_text('utf-8'))
    name=meta['source_filename']
    if Path(name).name!=name or meta.get('scope')!='whole_original_image' or not meta.get('live_CPU_run'):raise ValueError('分类来源无效')
    raw=(folder/name).read_bytes();preview=(folder/'actual_224_input.png').read_bytes();data=(folder/'result.json').read_bytes()
    if (sha256(data).hexdigest()!=meta['result_sha256'] or sha256(preview).hexdigest()!=meta['preview_sha256']
        or sha256(raw).hexdigest()!=meta['source_sha256'] or source_sha is not None and source_sha!=meta['source_sha256']):raise ValueError('分类文件摘要不匹配')
    return validate(json.loads(data),raw,preview)

def attach(record,folder):
    from .records import load_record
    saved=load_record(Path(record))
    if not saved.request.source_bytes:raise ValueError('分割记录不含整张原图')
    digest=sha256(saved.request.source_bytes).hexdigest();load(folder,source_sha=digest)
    # Portable sidecar, self-contained; no absolute path in exported index.
    import shutil
    destination=Path(record)/'classification'/Path(folder).name
    destination.parent.mkdir(exist_ok=True);shutil.copytree(folder,destination)
    from .analysis_identity import result_identity
    write_json(Path(record)/'classification_index.json',dict(source_sha256=digest,folder=destination.relative_to(record).as_posix(),
               record_id=saved.document['record_id'],segmentation_identity=result_identity((saved.request,saved.result))))
    return result_identity((saved.request,saved.result))

def for_record(record):
    index=record.directory/'classification_index.json'
    if not index.exists():return None
    data=json.loads(index.read_text('utf-8'));folder=(record.directory/data['folder']).resolve()
    if not folder.is_relative_to(record.directory.resolve()) or not record.request.source_bytes:raise ValueError('分类关联无效')
    digest=sha256(record.request.source_bytes).hexdigest()
    if data['source_sha256']!=digest:raise ValueError('分类不是对应整图')
    if data.get('record_id') is not None:
        from .analysis_identity import result_identity
        if data['record_id']!=record.document['record_id'] or data.get('segmentation_identity')!=result_identity((record.request,record.result)):
            raise ValueError('分类关联帧身份不符')
    return load(folder,source_sha=digest)

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='独立整图分类教学候选；不连接FPGA')
    p.add_argument('--python',required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv);client=ClassifierClient(a.python)
    try:
        folder=run(client,a.image.read_bytes(),a.image.name,a.output)
        print(json.dumps(dict(folder=str(folder),result=load(folder)),ensure_ascii=False));return 0
    finally:client.close()
