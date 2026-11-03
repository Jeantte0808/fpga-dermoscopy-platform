"""Prepare fixed first20 validation samples; use the formal delivery core unchanged."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import cv2
import numpy as np
import torch

from validation20_replay import load_official,sha

ROOT=Path(__file__).resolve().parents[1]
DELIVERY=ROOT/'deliveries/FPGA_Skin_MobileNetV2_INT8_MASK_V1_03_HW_R1'


def js(path,obj):Path(path).write_text(json.dumps(obj,indent=2,ensure_ascii=False),encoding='utf-8')


def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)


def csv_save(path,rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def inventory(package):
    files=[]
    for path in sorted(package.rglob('*')):
        if not path.is_file() or path.name in ['manifest.json','SHA256SUMS.txt'] or '__pycache__' in path.parts:continue
        rel=path.relative_to(package).as_posix()
        row=dict(path=rel,bytes=path.stat().st_size,sha256=sha(path))
        if rel.startswith('rgb888/'):
            row.update(dtype='uint8',shape=[256,256,3],layout='row-major HWC RGB interleaved',expected_bytes=196608)
        elif rel.startswith('reference_masks/bin/'):
            row.update(dtype='uint8',shape=[256,256],layout='row-major HW tight0/255',expected_bytes=65536)
        if 'expected_bytes' in row:assert row['bytes']==row['expected_bytes']
        files.append(row)
    js(package/'manifest.json',dict(files=files,exclusions=['manifest.json self','SHA256SUMS.txt self'],
        samples=20,dataset='existing validation split',selection='sort180 frozen filenames ascending; first20',
        new_independent_test_score=False,no_intermediate_layer_tensors=True))
    (package/'SHA256SUMS.txt').write_text('\n'.join([r['sha256']+'  '+r['path'] for r in files]+[sha(package/'manifest.json')+'  manifest.json'])+'\n',encoding='ascii')
    return files


def main():
    sys.dont_write_bytecode=True
    protected={str(path):sha(path) for path in DELIVERY.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
    status=json.loads((DELIVERY/'DELIVERY_STATUS.json').read_text(encoding='utf-8'))
    spec=json.loads((DELIVERY/'model/quantization.json').read_text(encoding='utf-8'))
    assert status['software_precision_accepted'] and status['version']==DELIVERY.name
    assert spec['version']=='INT8_V1_OUTPUT_ACCUMULATOR_UNIT_03' and spec['output_scale']==0.009964352582085952
    with (DELIVERY/'calibration/val_manifest.csv').open(encoding='utf-8-sig',newline='') as f:
        all_rows=list(csv.DictReader(f))
    assert len(all_rows)==180 and len({r['filename'] for r in all_rows})==180
    all_rows.sort(key=lambda r:r['filename']);chosen=all_rows[:20]
    actual_ids={p.name for p in (ROOT/'dataset/processed/val/images').glob('*.jpg')}
    assert actual_ids=={r['filename'] for r in all_rows}
    with (DELIVERY/'calibration/train_manifest.csv').open(encoding='utf-8-sig') as f:train_names={r['filename'] for r in csv.DictReader(f)}
    assert not train_names & {r['filename'] for r in chosen}
    version='FPGA_Skin_Validation20_INT8_V1_03_HW_R1_samples_r1';package=ROOT/'deliveries'/version;i=2
    while package.exists() or package.with_suffix('.zip').exists():
        version=f'FPGA_Skin_Validation20_INT8_V1_03_HW_R1_samples_r{i}';package=ROOT/'deliveries'/version;i+=1
    package.mkdir()
    for name in ['images','ground_truth','rgb888','reference_masks/png','reference_masks/bin','software','preprocessing/source_snapshots','model_identity','verification']:
        (package/name).mkdir(parents=True,exist_ok=True)
    identity=dict(formal_delivery_version=status['version'],numerical_candidate_version=spec['version'],
        formal_delivery_source=str(DELIVERY),formal_delivery_manifest_sha256=sha(DELIVERY/'manifest.json'),
        parameter_manifest_sha256=sha(DELIVERY/'model/parameter_files.json'),quantization_sha256=sha(DELIVERY/'model/quantization.json'),
        integer_reference_sha256=sha(DELIVERY/'software/integer_reference.py'),input_lut_sha256=sha(DELIVERY/'model/parameters/input_lut.bin'),
        output_domain=spec['output_domain'],output_scale=spec['output_scale'],output_zero_point=spec['output_zero_point'],
        mask_foreground_min_integer=spec['mask_foreground_min_integer'],
        augmentation='bypassed; RGB bytes go directly to the existing LUT',model_parameters_modified=False,
        full_model_weights_included=False,input_lut_identity_copy_included=True)
    formal_zip=DELIVERY.with_suffix('.zip')
    if formal_zip.exists():identity.update(formal_delivery_zip_sha256=sha(formal_zip),formal_delivery_zip_filename=formal_zip.name)
    js(package/'model_identity.json',identity)
    for name in ['quantization.json','parameter_files.json']:
        copy(DELIVERY/'model'/name,package/'model_identity'/name)
    copy(DELIVERY/'model/parameters/input_lut.bin',package/'model_identity/input_lut.bin')
    copy(DELIVERY/'calibration/val_manifest.csv',package/'model_identity/original_validation_manifest.csv')
    copy(DELIVERY/'ENVIRONMENT.json',package/'ENVIRONMENT.json')
    copy(DELIVERY/'spec/DATA_LAYOUT_V1.md',package/'model_identity/DATA_LAYOUT_V1.md')
    copy(ROOT/'code/validation20_replay.py',package/'software/replay_samples.py')
    # Exact code snapshots are documentation only; none of these preprocessing/training scripts is executed.
    snapshots=['prepare_dataset.py','dataset_loader.py','train_mobilenetv2_candidate.py','mobilenetv2_light_unet.py','build_int8_hardware_delivery.py']
    sources=[]
    for name in snapshots:
        src=ROOT/'code'/name;copy(src,package/'preprocessing/source_snapshots'/name)
        sources.append(dict(original_file=str(src),snapshot='source_snapshots/'+name,sha256=sha(src),executed=False))
    js(package/'preprocessing/snapshot_sources.json',sources)
    copy(ROOT/'experiments/mobilenetv2_imagenetv2_lightunet_seed42/hardware_report/input_output_spec.json',package/'preprocessing/input_output_spec.json')
    copy(ROOT/'experiments/mobilenetv2_imagenetv2_lightunet_seed42/config.json',package/'preprocessing/candidate_training_config.json')
    ref=load_official(DELIVERY,identity)
    selected=[];hash_protected_inputs={}
    with torch.inference_mode():
        for index,source in enumerate(chosen,1):
            filename=source['filename'];stem=Path(filename).stem
            image_path=ROOT/source['image_path'];gt_path=ROOT/source['mask_path']
            assert image_path.parent==ROOT/'dataset/processed/val/images'
            assert gt_path.parent==ROOT/'dataset/processed/val/masks'
            assert image_path.name==filename and gt_path.name==stem+'.png'
            assert sha(image_path)==source['image_sha256'] and sha(gt_path)==source['mask_sha256']
            hash_protected_inputs[str(image_path)]=source['image_sha256'];hash_protected_inputs[str(gt_path)]=source['mask_sha256']
            image_file='images/'+filename;gt_file='ground_truth/'+stem+'.png'
            copy(image_path,package/image_file);copy(gt_path,package/gt_file)
            # Exact existing export flow: decode existing processed JPEG; BGR->RGB; no resize/save pass.
            bgr=cv2.imread(str(package/image_file),cv2.IMREAD_COLOR)
            gt=cv2.imread(str(package/gt_file),cv2.IMREAD_GRAYSCALE)
            assert bgr is not None and bgr.shape==(256,256,3)
            assert gt is not None and gt.shape==(256,256) and set(np.unique(gt)).issubset({0,255})
            rgb=np.ascontiguousarray(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB),dtype=np.uint8)
            rgb_file='rgb888/'+stem+'.rgb888.bin';rgb.tofile(package/rgb_file)
            assert (package/rgb_file).stat().st_size==196608
            actual_input=np.fromfile(package/rgb_file,np.uint8).reshape(256,256,3)
            assert np.array_equal(rgb,actual_input)
            before=int(ref.overflow)
            logits=ref.run(actual_input)
            mask=(logits[0,0].cpu().numpy()>=ref.spec['mask_foreground_min_integer']).astype(np.uint8)*255
            mask_bin='reference_masks/bin/'+stem+'.mask.bin';mask_png='reference_masks/png/'+stem+'.png'
            mask.tofile(package/mask_bin);assert cv2.imwrite(str(package/mask_png),mask)
            assert (package/mask_bin).stat().st_size==65536
            read_mask=np.fromfile(package/mask_bin,np.uint8).reshape(256,256)
            png_mask=cv2.imread(str(package/mask_png),cv2.IMREAD_GRAYSCALE)
            assert np.array_equal(mask,read_mask) and np.array_equal(mask,png_mask)
            assert set(np.unique(mask)).issubset({0,255})
            row=dict(index=index,filename=filename,split='validation',image_file=image_file,ground_truth_file=gt_file,
                rgb888_file=rgb_file,reference_mask_png=mask_png,reference_mask_bin=mask_bin,
                image_sha256=sha(package/image_file),ground_truth_sha256=sha(package/gt_file),rgb888_sha256=sha(package/rgb_file),
                reference_png_sha256=sha(package/mask_png),reference_bin_sha256=sha(package/mask_bin),
                final_int8_logits_sha256=hashlib.sha256(logits.cpu().numpy().tobytes()).hexdigest(),
                rgb888_bytes=196608,reference_mask_bytes=65536,image_shape='256x256x3',mask_shape='256x256',
                ground_truth_values=','.join(str(int(v)) for v in np.unique(gt)),reference_mask_values=','.join(str(int(v)) for v in np.unique(mask)),
                accumulator_overflow=int(ref.overflow)-before)
            selected.append(row)
            print(f'Validation sample {index}/20: {filename}, overflow={row["accumulator_overflow"]}',flush=True)
    csv_save(package/'samples.csv',selected)
    js(package/'selection.json',dict(method='From the original180 validation manifest, sort filename ascending and take the first20',
        original_manifest_sha256=sha(DELIVERY/'calibration/val_manifest.csv'),total_validation_count=180,selected_count=20,
        filenames=[r['filename'] for r in selected],image_dataset='dataset/processed/val/images',
        ground_truth_dataset='dataset/processed/val/masks',original_images_copied_without_resaving=True,
        ground_truth_copied_without_modification=True,official_test_used=False,annotations_not_used_by_inference=True,
        accumulator_overflows=int(ref.overflow),not_a_new_independent_test_score=True))
    js(package/'preprocessing/config.json',dict(original_to_processed=dict(size=[256,256],image_interpolation='cv2.INTER_AREA',
        mask_interpolation='cv2.INTER_NEAREST',preserve_aspect_ratio=False,crop=False,pad_to_square=False,
        mask_threshold='cv2.threshold(mask,127,255,cv2.THRESH_BINARY)',
        jpeg_save='cv2.imwrite(out_img_path,image), .jpg extension, no explicit JPEG encode parameters'),
        inference_decode='cv2.imread(processed JPEG, IMREAD_COLOR) BGR; cv2.cvtColor(...,COLOR_BGR2RGB)',
        rgb888=dict(dtype='uint8',shape=[256,256,3],layout='tight row-major RGB interleaved',offset='((y*256+x)*3+c)',bytes=196608),
        LUT=dict(file='model_identity/input_lut.bin',dtype='int8',shape=[3,256],layout='R256,G256,B256',
                 formula='clip(RTA(((v/255-mean[c])/std[c])/Sin),-127,127)',
                 mean=[.485,.456,.406],std=[.229,.224,.225],Sin=spec['input_scale'],zero_point=0),
        augmentation=False,no_reprocessing_performed=True,opencv_used_for_this_decode=cv2.__version__))
    file_list=', '.join(r['filename'] for r in selected)
    readme=f'''# 验证集前20张联调样本：{version}

用途：人工标注用于评估分割质量；INT8参考Mask用于与RTL输出逐像素做**0 LSB比较**。
这是现有版本的联调样本，来自已使用的验证集，**不是新的独立测试成绩**。本次不计算新的集合精度、不调参、不跑官方测试集。

## 固定选择及文件对应

读取正式交付包calibration/val_manifest.csv中的180张，按filename升序排序，取前20张；不依据分数挑选。
文件名：{file_list}。
samples.csv逐行列出图片、同stem人工Mask、RGB888、INT8 Mask PNG/bin及SHA。选择规则与源清单SHA见selection.json。

每张内容：images/保留原名的处理版JPEG；ground_truth/同名stem的原PNG人工标注；rgb888/外部输入字节；reference_masks/png和bin/对应INT8参考Mask。
图像与标注均256×256；人工和参考Mask均0/255。图片/标注从现有processed/val按源清单哈希原样复制，没有重新保存JPEG或重建预处理数据。
RGB888为uint8，按行、按像素R/G/B交错，偏移((y*256+x)*3+c)，每图196608B。Mask bin为紧密HW、uint8、无C16补齐，每图65536B。

## 模型版本及重放

只使用正式交付 `{identity['formal_delivery_version']}` 的 `{identity['numerical_candidate_version']}`。
最终输出scale={identity['output_scale']:.17g}、zp=0，严格logits域阈值按正式元数据为q≥{identity['mask_foreground_min_integer']}。
本次没有改变参数或整数规则。版本、正式ZIP/manifest及参数清单SHA见model_identity.json；包内另附完全相同的参数索引、量化元数据和输入LUT，供追溯，不附第二份整网权重。

解压本样本包与正式模型包后，从本样本目录运行（正式模型路径显式传入）：

```powershell
python -B software/replay_samples.py --delivery "..\\{DELIVERY.name}" --verify-files
```

本机使用E:\\conda_envs\\fpga_skin\\python.exe；依赖版本见ENVIRONMENT.json。需要已有PyTorch CUDA、NumPy、OpenCV环境。
此命令直接导入指定正式包内software/integer_reference.py，以正式包参数清单SHA锁定版本，不实现第二套量化算法；对20张包内RGB字节重放并比较PNG/bin及最终整数logits指纹。不会导出逐层张量。
样本包需要配套正式模型包；这是一项显式依赖，不能把其他候选目录当正式模型替换。

## 现有预处理的实际流程

1. 原始图像由cv2.imread读取为BGR，直接用INTER_AREA缩放到256×256；不保持原比例，不裁剪、不补边。
2. 人工Mask灰度读取、INTER_NEAREST缩放到256×256，再按127阈值输出0/255。
3. 图像通过cv2.imwrite(out_img_path,image)保存为.jpg，Mask保存为PNG。代码未显式指定JPEG质量/编码参数，不能把某个质量数字当作已记录的约定。本次原样复制既有JPEG。
4. 推理/本次导出重新解码该处理版JPEG为BGR，再在cv2.cvtColor(...,COLOR_BGR2RGB)转成RGB；这里不再次缩放或保存JPEG。只有此时的uint8 RGB值写入RGB888。
5. 浮点训练输入为RGB float32/255、CHW，随后减ImageNet mean=[0.485,0.456,0.406]、除std=[0.229,0.224,0.225]。整数推理直接由正式3×256输入LUT承接这一归一化及输入量化，外部仍发送未归一化的RGB888；不重复归一化。LUT按R/G/B三个256项bank、signedINT8[-127,127]/zp0，Sin={spec['input_scale']:.17g}。
6. 几何/光度增强均旁路。最终Mask使用正式参考的LUT、整数算术、修正head参数和严格阈值；末端logits幅值及其sigmoid不作可靠置信度。

preprocessing/source_snapshots/附当前prepare_dataset.py、dataset_loader.py、ImageNetInput所在训练脚本、常量模型定义，以及实际RGB导出代码来源；preprocessing/config.json和input_output_spec.json给出必要配置。这些是追溯快照，**本次未运行**，重放入口只用上方replay_samples.py；不要为联调重新运行全量prepare_dataset。

## 本次核对结果

20/20验证来源与原文件哈希匹配，图片/标注同stem、尺寸和取值检查通过；RGB文件重新读回后作为实际送入整数参考的输入；PNG/bin与参考Mask逐像素一致。
20张累计累加溢出={int(ref.overflow)}。独立打包入口重放结果见verification/replay_check.json；原正式交付包保持不变，见verification/source_preservation.json。
manifest.json记录每个文件的字节数/SHA和原始字节格式，SHA256SUMS.txt另包含manifest哈希。两者不自哈希，外部ZIP.sha256锚定整个归档。
人工标注与参考Mask作用不同：RTL应对参考Mask做0 LSB比较；不能要求RTL逐像素等于人工标注。
'''
    (package/'README.md').write_text(readme,encoding='utf-8')
    message=f'''本次补充的是正式版本{DELIVERY.name}的20张验证集联调样本，按原180张清单文件名排序固定取前20张。每张附原处理版JPEG、人工Mask、196608B RGB888及正式INT8参考Mask（PNG和65536B紧密二进制）；对应关系和SHA见samples.csv。人工Mask用于看分割质量，RTL请与INT8参考Mask做0 LSB比较，两者不要混用。20张推理成功，累加溢出0，输入增强旁路，参数/LUT/输出阈值沿用正式版本。请先核对RGB字节、文件名和长度，再比较Mask；需要重放时按README显式指定正式模型包路径。这是已有验证集的联调数据，不是新的独立测试成绩。'''
    (package/'HANDOFF_MESSAGE.txt').write_text(message,encoding='utf-8')
    inventory(package)
    env=os.environ.copy();env['PYTHONPATH']='';env['PYTHONDONTWRITEBYTECODE']='1'
    result=subprocess.run([sys.executable,'-B',str(package/'software/replay_samples.py'),'--delivery',str(DELIVERY),
        '--verify-files','--report-out',str(package/'verification/replay_check.json')],cwd=package,
        env=env,capture_output=True,text=True,encoding='utf-8',check=True)
    print(result.stdout,flush=True)
    assert all(sha(path)==digest for path,digest in protected.items())
    assert all(sha(path)==digest for path,digest in hash_protected_inputs.items())
    js(package/'verification/source_preservation.json',dict(formal_delivery_unchanged=True,
        formal_files_checked=len(protected),source20_images_and20_annotations_unchanged=True,
        no_training_calibration_or_quantization_search=True,no_official_test_used=True,
        only20_unique_validation_images_inferred=True,layer_tensors_exported=False))
    js(package/'verification/build_summary.json',dict(version=version,samples=20,inference_successes=20,
        accumulator_overflows=int(ref.overflow),png_binary_pixel_match=True,rgb_file_is_actual_model_input=True,
        formal_version=status['version'],parameter_manifest_sha256=identity['parameter_manifest_sha256'],
        new_independent_test_score=False))
    files=inventory(package)
    archive=package.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,version+'/'+p.relative_to(package).as_posix())
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            p=package/info.filename.removeprefix(version+'/')
            assert hashlib.sha256(z.read(info)).hexdigest()==sha(p)
    archive_digest=sha(archive)
    Path(str(archive)+'.sha256').write_text(archive_digest+'  '+archive.name+'\n',encoding='ascii')
    summary=dict(directory=str(package),archive=str(archive),zip_bytes=archive.stat().st_size,zip_sha256=archive_digest,
        sample_count=20,filenames=[r['filename'] for r in selected],integer_inference_successes=20,
        accumulator_overflows=int(ref.overflow),formal_delivery_unmodified=True,files=len(files)+2)
    js(package.parent/(version+'_summary.json'),summary)
    print(json.dumps(summary,indent=2,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
