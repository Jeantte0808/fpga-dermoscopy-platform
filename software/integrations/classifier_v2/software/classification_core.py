"""Independent PC dermoscopy classifier: no segmentation, downloads, GPU requirement or GUI."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageOps
import torch
from torch import nn
from torchvision import transforms
from torchvision.models import mobilenet_v2

VERSION = 'HAM10000_SMALL900_MOBILENETV2_CLASSIFIER_FP32_V2'
CLASS_NAMES = ['non_melanoma_group', 'melanoma']
CLASS_ZH = ['非黑色素瘤组（包含其他皮肤癌）', '黑色素瘤']
MEAN, STD = [0.485,0.456,0.406], [0.229,0.224,0.225]
TO_TENSOR = transforms.Compose([transforms.ToTensor(), transforms.Normalize(MEAN,STD)])
RESIZE_CROP = transforms.Compose([transforms.Resize(232, interpolation=transforms.InterpolationMode.BILINEAR, antialias=True),
                                  transforms.CenterCrop(224)])


def decode_classification_image(path):
    raw = Path(path).read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in ('JPEG','PNG') or im.mode != 'RGB' or getattr(im,'n_frames',1)!=1:
            raise ValueError('Candidate supports single-frame 8-bit RGB JPEG/PNG only; no implicit grayscale/alpha/16-bit conversion.')
        if im.format=='PNG' and (len(raw)<26 or raw[24]!=8 or raw[25]!=2):
            raise ValueError('PNG must be 8-bit truecolor RGB; 16-bit/palette/alpha inputs are unsupported.')
        if im.format=='JPEG' and getattr(im,'bits',8)!=8:
            raise ValueError('JPEG must use 8-bit precision.')
        orientation=im.getexif().get(274,1)
        if orientation not in range(1,9):raise ValueError('Invalid EXIF orientation')
        image=ImageOps.exif_transpose(im).convert('RGB')
        image.load()
        size=image.size
        result=RESIZE_CROP(image)
    return result,dict(source_filename=Path(path).name,source_sha256=hashlib.sha256(raw).hexdigest(),
                       exif_orientation=int(orientation),oriented_size_wh=list(size),
                       preprocess_version='CLASS_RGB_EXIF_SHORT232_CENTERCROP224_IMAGENET_V1',
                       processed_rgb_sha256=hashlib.sha256(result.tobytes()).hexdigest(),
                       image_input_scope='whole source image; standard classification center crop; no segmentation ROI',
                       jpeg_reencoding=False)


def create_model():
    model=mobilenet_v2(weights=None,width_mult=1.0)
    model.classifier[1]=nn.Linear(model.last_channel,2)
    return model


def classification_metrics(labels, scores):
    y=np.asarray(labels,dtype=np.int64);p=np.asarray(scores,dtype=np.float64)
    if len(y)==0 or p.shape!=(len(y),2) or not np.isfinite(p).all():raise ValueError('Invalid scores')
    predicted=np.argmax(p,axis=1)  # exact tie selects index0; no threshold tuning
    cm=np.zeros((2,2),dtype=np.int64)
    for truth,guess in zip(y,predicted):cm[truth,guess]+=1
    per_class=[]
    for i,name in enumerate(CLASS_NAMES):
        tp=int(cm[i,i]);fp=int(cm[:,i].sum()-tp);fn=int(cm[i,:].sum()-tp)
        precision=tp/(tp+fp) if tp+fp else 0.
        recall=tp/(tp+fn) if tp+fn else 0.
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.
        per_class.append(dict(index=i,name=name,label_zh=CLASS_ZH[i],support=int(cm[i].sum()),
                              precision=precision,recall=recall,f1=f1,
                              precision_defined=bool(tp+fp),recall_defined=bool(tp+fn)))
    positive=p[y==1,1];negative=p[y==0,1]
    auc=float(((positive[:,None]>negative[None,:]).astype(float)+.5*(positive[:,None]==negative[None,:])).mean()) if len(positive) and len(negative) else None
    return dict(count=len(y),class_order=CLASS_NAMES,per_class=per_class,
        macro_f1=float(np.mean([r['f1'] for r in per_class])),balanced_accuracy=float(np.mean([r['recall'] for r in per_class])),
        accuracy=float(np.trace(cm)/len(y)),confusion_matrix=cm.tolist(),confusion_axes='rows=true,columns=predicted',
        roc_auc_positive_melanoma=auc,positive_recall=per_class[1]['recall'],specificity=per_class[0]['recall'],
        zero_denominator='precision/recall/F1=0 with defined flags; ROC-AUC=null if a class is absent',
        decision='argmax in fixed class order; exact ties select index0; no tuned/reject threshold',
        scores_are='uncalibrated model scores, not disease probabilities')


class CPUClassifier:
    def __init__(self, weight_path, *, threads=4, interop_threads=1):
        torch.set_num_threads(threads)
        if torch.get_num_interop_threads()!=interop_threads:
            torch.set_num_interop_threads(interop_threads)
        self.weight_path=Path(weight_path)
        self.weight_sha256=hashlib.sha256(self.weight_path.read_bytes()).hexdigest()
        clock=time.perf_counter_ns()
        self.model=create_model().to(device='cpu',dtype=torch.float32)
        state=torch.load(self.weight_path,map_location='cpu',weights_only=True)
        if state['class_names']!=CLASS_NAMES or state['model_version']!=VERSION:raise ValueError('Class order/version mismatch')
        self.model.load_state_dict(state['state_dict'],strict=True)
        self.model.eval()
        self.best_epoch=state['epoch']
        self.load_ms=(time.perf_counter_ns()-clock)/1e6
        self.threads=threads;self.interop_threads=interop_threads

    def predict(self, path):
        prepared,identity=decode_classification_image(path)
        tensor=TO_TENSOR(prepared).unsqueeze(0).to(device='cpu',dtype=torch.float32)
        clock=time.perf_counter_ns()
        with torch.inference_mode(), torch.autocast(device_type='cpu',enabled=False):
            logits=self.model(tensor)
        forward_ms=(time.perf_counter_ns()-clock)/1e6
        scores=torch.softmax(logits,dim=1)[0].tolist()
        index=int(np.argmax(scores))
        return dict(schema='skin.classification.candidate.v1',model_version=VERSION,weight_sha256=self.weight_sha256,
            best_epoch=self.best_epoch,device='cpu',dtype='float32',identity=identity,
            predicted_class=CLASS_NAMES[index],predicted_class_zh=CLASS_ZH[index],
            class_scores=[dict(index=i,category=name,label_zh=CLASS_ZH[i],score=float(scores[i])) for i,name in enumerate(CLASS_NAMES)],
            score_semantics='uncalibrated model scores; not disease probabilities or a diagnosis',
            timing=dict(model_load_ms=self.load_ms,forward_ms=forward_ms,
                        load_scope='network creation, local weight read/load, eval; excludes imports/hash/thread setup; file cache uncontrolled',
                        forward_scope='CPU resident forward only, prepared batch1 tensor; excludes decode/preprocess/softmax/JSON/save',
                        threads=self.threads,interop_threads=self.interop_threads),
            limitations=['Non-melanoma includes BCC and AKIEC; it is not a benign group.',
                         'No unknown-disease threshold, ROI inference or segmentation dependency.',
                         'Candidate for engineering demonstration, not clinical diagnosis.'])


def load_model(weight_path=None, *, threads=4, interop_threads=1):
    if weight_path is None:weight_path=Path(__file__).resolve().parents[1]/'best_model.pth'
    return CPUClassifier(weight_path,threads=threads,interop_threads=interop_threads)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--weights');p.add_argument('--threads',type=int,default=4);p.add_argument('--interop-threads',type=int,default=1)
    a=p.parse_args();runner=load_model(a.weights,threads=a.threads,interop_threads=a.interop_threads);result=runner.predict(a.input)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(predicted_class=result['predicted_class'],forward_ms=result['timing']['forward_ms']),ensure_ascii=False))


if __name__=='__main__':main()
