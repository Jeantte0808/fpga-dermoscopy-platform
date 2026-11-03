"""Resident external CPU runtime, same-byte comparison and verifiable exports."""
import base64
import csv
from hashlib import sha256
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from uuid import uuid4

import numpy as np

from .resources import resource_path
from .runtime_paths import bundle_root


def default_python():
    candidate = bundle_root()/'.cache/cpu-venv/Scripts/python.exe'
    return str(candidate) if candidate.is_file() else ''


class CPUClient:
    """Call only from a GUI worker. A client serializes requests on one child."""
    worker_resource='cpu_worker'
    package_resource='cpu_root'
    def __init__(self, python):
        self.python = Path(python).resolve()
        if not self.python.is_file():
            raise ValueError('请选择已安装CPU依赖的Python解释器，见CPU_ROI_QUICK_START.md')
        self.process = None
        self.ready = None
        self._lock = threading.Lock()
        self._messages = queue.Queue()

    def _receive(self,timeout,cancel):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            if cancel is not None and cancel.is_set():
                self.close()
                raise InterruptedError('CPU任务已取消；下次将重建CPU进程')
            try:
                value = self._messages.get(timeout=.05)
                if value is None:
                    raise RuntimeError('CPU工作进程已退出，请检查CPU依赖或日志')
                if 'error' in value:
                    raise RuntimeError(value['error'])
                return value
            except queue.Empty:
                continue
        self.close()
        raise TimeoutError('CPU工作进程超时，已结束；FPGA会话未受影响')

    def _start(self,cancel):
        if self.process is not None and self.process.poll() is None:
            return
        self._messages = queue.Queue()
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0
        self.process = subprocess.Popen([str(self.python),'-I','-B','-u',str(resource_path(self.worker_resource)),
                                        '--package',str(resource_path(self.package_resource))],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True,encoding='utf-8',creationflags=flags)
        stream,messages = self.process.stdout,self._messages
        def read():
            try:
                for line in stream:
                    if len(line)>4_000_000:
                        raise ValueError('CPU响应过大')
                    messages.put(json.loads(line))
            except Exception as exc:
                messages.put({'error':str(exc)})
            finally:
                messages.put(None)
        self._reader = threading.Thread(target=read,daemon=True)
        self._reader.start()
        try:
            self.ready = self._receive(90,cancel)
            if self.ready.get('ready') is not True or self.ready['environment']['device']!='cpu':
                raise ValueError('CPU进程初始化失败')
        except Exception:
            self.close()
            raise

    def run(self,rgb,*,benchmark=False,cancel=None):
        if type(rgb) is not bytes or len(rgb)!=196608:
            raise ValueError('CPU仅接受实际196608字节RGB888输入')
        with self._lock:
            self._start(cancel)
            started=time.perf_counter()
            request={'action':'benchmark' if benchmark else 'infer','rgb':base64.b64encode(rgb).decode('ascii')}
            self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
            value=self._receive(180,cancel)
            mask=base64.b64decode(value['mask'],validate=True)
            if (value.get('ok') is not True or value['rgb_sha256']!=sha256(rgb).hexdigest()
                    or len(mask)!=65536 or not set(mask).issubset({0,255})):
                self.close();raise ValueError('CPU返回的RGB身份/Mask无效')
            value.pop('mask')
            value.update(runtime=self.ready,threshold='sigmoid(logits)>0.5',device='cpu',dtype='float32',
                         host_request_ms=(time.perf_counter()-started)*1000,
                         cpu_measured_locally=True, fpga_same_PC_pairing_confirmed=False, five_fps_accepted=False)
            return mask,value

    def close(self):
        process,self.process=self.process,None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()


def agreement(cpu_mask,fpga_mask):
    if len(cpu_mask)!=65536 or len(fpga_mask)!=65536 or not set(cpu_mask+fpga_mask).issubset({0,255}):
        raise ValueError('对照Mask必须是65536字节0/255结果')
    a=np.frombuffer(cpu_mask,np.uint8)!=0;b=np.frombuffer(fpga_mask,np.uint8)!=0
    intersection=int(np.count_nonzero(a&b));denom=int(a.sum()+b.sum());union=int(np.count_nonzero(a|b))
    return {'different_pixels':int(np.count_nonzero(a!=b)),
            'dice_agreement':2*intersection/denom if denom else 1.,
            'iou_agreement':intersection/union if union else 1.,
            'interpretation':'CPU_FP32_vs_FPGA_INT8_agreement_not_ground_truth_accuracy',
            'thresholds':{'cpu':'sigmoid(logits)>0.5','fpga':'integer_logits_q>=1'}}


def save_comparison(parent,rgb,mask,report,*,fpga_mask=None,fpga_metadata=None):
    validate_comparison(rgb,mask,report,fpga_mask,fpga_metadata)
    folder=Path(parent)/('cpu_comparison_'+uuid4().hex[:12]);folder.mkdir(parents=True,exist_ok=False)
    (folder/'INCOMPLETE.txt').write_text('CPU comparison export incomplete\n','utf-8')
    blobs={'input.rgb':rgb,'cpu_mask.raw':mask}
    if fpga_mask is not None:blobs['fpga_mask.raw']=fpga_mask
    for name,data in blobs.items():(folder/name).write_bytes(data)
    from .storage import write_png
    write_png(folder/'input.png',np.frombuffer(rgb,np.uint8).reshape(256,256,3)[:,:,::-1])
    write_png(folder/'cpu_mask.png',np.frombuffer(mask,np.uint8).reshape(256,256))
    if fpga_mask is not None:write_png(folder/'fpga_mask.png',np.frombuffer(fpga_mask,np.uint8).reshape(256,256))
    with (folder/'timings.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['scope','repeat','milliseconds']);writer.writeheader()
        writer.writerow({'scope':'first_model_load','repeat':1,'milliseconds':report['runtime']['load_ms']})
        writer.writerows(report.get('records',[]))
    files={p.name:{'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()}
           for p in folder.iterdir() if p.name!='INCOMPLETE.txt'}
    doc={'schema':'cpu_fp32_comparison_v1','complete':True,'report':report,'files':files,
         'fpga':fpga_metadata,'agreement':agreement(mask,fpga_mask) if fpga_mask is not None else None,
         'education_notice':'仅用于图像分析与算法研究，不作为临床诊断依据。'}
    (folder/'comparison.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    (folder/'INCOMPLETE.txt').unlink()
    return folder


def validate_comparison(rgb,mask,report,fpga_mask,fpga_metadata):
    if (type(rgb) is not bytes or len(rgb)!=196608 or type(mask) is not bytes or len(mask)!=65536
            or not set(mask).issubset({0,255}) or report['rgb_sha256']!=sha256(rgb).hexdigest()
            or report['device']!='cpu' or report['threshold']!='sigmoid(logits)>0.5'
            or report['dtype']!='float32' or report['runtime']['environment']['device']!='cpu'):
        raise ValueError('CPU对照输入/结果身份不一致')
    if fpga_mask is not None:
        agreement(mask,fpga_mask)
        if fpga_metadata is None or fpga_metadata['input_sha256']!=sha256(rgb).hexdigest():
            raise ValueError('CPU与FPGA必须使用同一份RGB888字节')


def load_comparison(folder):
    folder=Path(folder)
    if (folder/'INCOMPLETE.txt').exists():raise ValueError('对照记录保存未完成')
    doc=json.loads((folder/'comparison.json').read_text('utf-8'))
    expected={'input.rgb','cpu_mask.raw','input.png','cpu_mask.png','timings.csv'}
    if doc.get('fpga') is not None:expected|={'fpga_mask.raw','fpga_mask.png'}
    if doc['schema']!='cpu_fp32_comparison_v1' or doc['complete'] is not True or set(doc['files'])!=expected:
        raise ValueError('CPU对照记录结构无效')
    for name,spec in doc['files'].items():
        data=(folder/name).read_bytes()
        if len(data)!=spec['bytes'] or sha256(data).hexdigest()!=spec['sha256']:
            raise ValueError('CPU对照文件校验失败：'+name)
    rgb=(folder/'input.rgb').read_bytes();mask=(folder/'cpu_mask.raw').read_bytes()
    fpga=(folder/'fpga_mask.raw').read_bytes() if doc['fpga'] is not None else None
    validate_comparison(rgb,mask,doc['report'],fpga,doc['fpga'])
    if doc['agreement']!=(agreement(mask,fpga) if fpga is not None else None):
        raise ValueError('CPU对照统计不一致')
    from PIL import Image
    for name,raw,shape in [('input.png',rgb,(256,256,3)),('cpu_mask.png',mask,(256,256)),
                           *([('fpga_mask.png',fpga,(256,256))] if fpga is not None else [])]:
        with Image.open(folder/name) as image:
            if not np.array_equal(np.array(image),np.frombuffer(raw,np.uint8).reshape(shape)):
                raise ValueError('CPU对照PNG和RAW不一致')
    return rgb,mask,doc['report'],fpga,doc['fpga']
