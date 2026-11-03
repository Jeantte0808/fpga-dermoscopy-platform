"""Eval-only Conv/BN folding and paired validation; original files are read-only.

Reload entry: load_folded_model(output_dir, device='cuda').
The output sources/ directory contains this module and the unchanged model definition.
"""
import copy
import csv
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.fx import symbolic_trace
from torch.nn.utils.fusion import fuse_conv_bn_eval

from mobilenetv2_light_unet import MobileNetV2LightUNet
from evaluate_validation import binary_scores

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/mobilenetv2_imagenetv2_lightunet_seed42'
ATOL = RTOL = 1e-4


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def json_save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def csv_save(path, rows, fields=None):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def replace(model, name, module):
    parent_name, _, child = name.rpartition('.')
    parent = model.get_submodule(parent_name) if parent_name else model
    setattr(parent, child, module)


def find_pairs(model):
    graph = symbolic_trace(model)
    modules = dict(model.named_modules())
    calls = {}
    for n in graph.graph.nodes:
        if n.op == 'call_module':
            calls.setdefault(n.target, []).append(n)
    pairs, skipped = [], []
    for node in graph.graph.nodes:
        if node.op != 'call_module' or not isinstance(modules[node.target], nn.BatchNorm2d):
            continue
        bn = modules[node.target]
        prev = node.args[0]
        reason = None
        if prev.op != 'call_module' or not isinstance(modules[prev.target], nn.Conv2d):
            reason = 'BN input is not a directly connected Conv2d node'
        elif len(prev.users) != 1 or next(iter(prev.users)) is not node:
            reason = 'Convolution output has other consumers'
        elif len(calls[prev.target]) != 1 or len(calls[node.target]) != 1:
            reason = 'Shared convolution or BN module invoked multiple times'
        elif bn.training or modules[prev.target].training:
            reason = 'Modules are not in eval mode'
        elif not bn.track_running_stats or bn.running_mean is None or bn.running_var is None:
            reason = 'Fixed BN running statistics unavailable'
        elif modules[prev.target].out_channels != bn.num_features:
            reason = 'Channel dimensions mismatch'
        elif not all(torch.isfinite(v).all().item() for v in (bn.running_mean, bn.running_var, bn.weight, bn.bias) if v is not None):
            reason = 'Nonfinite BN state'
        elif not (bn.running_var + bn.eps > 0).all().item():
            reason = 'Nonpositive variance plus epsilon'
        if reason:
            skipped.append(dict(batchnorm=node.target, reason=reason))
            continue
        conv = modules[prev.target]
        pairs.append(dict(conv=prev.target, batchnorm=node.target, epsilon=bn.eps,
                          affine=bn.affine, track_running_stats=bn.track_running_stats,
                          channels=bn.num_features, groups=conv.groups,
                          original_conv_bias=conv.bias is not None,
                          running_variance_min=float(bn.running_var.min()),
                          direct_single_consumer_edge=True))
    return pairs, skipped, graph


def load_folded_model(output_dir, device='cpu'):
    """Rebuild folded topology, then strictly load independent FP32 state_dict.

    pretrained=False avoids downloading initialization weights; every parameter is
    replaced by the saved folded state_dict. No original checkpoint is needed.
    """
    output_dir = Path(output_dir)
    config = json.loads((output_dir / 'comparison_settings.json').read_text(encoding='utf-8'))
    pairs = json.loads((output_dir / 'folding_pairs.json').read_text(encoding='utf-8'))
    model = MobileNetV2LightUNet(pretrained=False, stage_indices=config['stage_indices']).float().eval()
    for pair in pairs:
        conv = model.get_submodule(pair['conv'])
        assert isinstance(conv, nn.Conv2d)
        assert isinstance(model.get_submodule(pair['batchnorm']), nn.BatchNorm2d)
        if conv.bias is None:
            conv.bias = nn.Parameter(torch.zeros(conv.out_channels, dtype=torch.float32))
        replace(model, pair['batchnorm'], nn.Identity())
    state = torch.load(output_dir / 'folded_model.pth', map_location='cpu', weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


def main():
    out = ROOT / 'results/mobilenetv2_conv_bn_folding'
    base, i = out, 2
    while out.exists():
        out = base.with_name(base.name + f'_{i:02d}')
        i += 1
    assert torch.cuda.is_available(), 'Existing CUDA environment required'
    torch.backends.fp32_precision = 'ieee'
    torch.backends.cuda.matmul.fp32_precision = 'ieee'
    torch.backends.cudnn.conv.fp32_precision = 'ieee'
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    device = torch.device('cuda')
    config = json.loads((EXP / 'config.json').read_text(encoding='utf-8'))
    best = json.loads((EXP / 'best_model.json').read_text(encoding='utf-8'))
    assert best['epoch'] == 27
    weights = EXP / 'best_model.pth'
    original_hash = digest(weights)
    original = MobileNetV2LightUNet(pretrained=False, stage_indices=config['stage_indices']).float().eval()
    original.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True), strict=True)
    pairs, skipped, graph = find_pairs(original)
    historical = json.loads((EXP / 'hardware_report/network_statistics.json').read_text(encoding='utf-8'))['candidate']['batchnorm_foldable_pairs']
    assert {(p['conv'], p['batchnorm'], p['epsilon']) for p in pairs} == {(p['conv'], p['batchnorm'], p['epsilon']) for p in historical}, 'Actual graph differs from hardware-report pair list'
    folded = copy.deepcopy(original)
    for p in pairs:
        replace(folded, p['conv'], fuse_conv_bn_eval(folded.get_submodule(p['conv']), folded.get_submodule(p['batchnorm'])))
        replace(folded, p['batchnorm'], nn.Identity())
    # Module types change only at known pairs; graph edges/operator order remain identical.
    assert str(symbolic_trace(folded).graph) == str(graph.graph)
    remaining_bn = [n for n, m in folded.named_modules() if isinstance(m, nn.BatchNorm2d)]
    assert len(remaining_bn) == len(skipped)
    out.mkdir(parents=True)
    settings = dict(source_weights=str(weights), source_sha256=original_hash, source_epoch=best['epoch'],
        stage_indices=config['stage_indices'], model='MobileNetV2LightUNet unchanged topology except folded Conv biases and Identity BN',
        torch=torch.__version__, cuda=torch.version.cuda, cudnn=torch.backends.cudnn.version(), gpu=torch.cuda.get_device_name(),
        batch_size=8, input_dtype='float32', parameter_dtype='float32', autocast=False, inference_mode=True, eval=True,
        backend=dict(global_fp32_precision=torch.backends.fp32_precision, cuda_matmul_fp32_precision=torch.backends.cuda.matmul.fp32_precision,
                     cudnn_conv_fp32_precision=torch.backends.cudnn.conv.fp32_precision,
                     cudnn_benchmark=torch.backends.cudnn.benchmark, cudnn_deterministic=torch.backends.cudnn.deterministic,
                     deterministic_algorithms=torch.are_deterministic_algorithms_enabled()),
        input='Existing processed/val 256x256 JPEG; cv2 BGR->RGB float32/255 CHW; ImageNet mean/std; no augment/crop/resize',
        mean=[.485,.456,.406], std=[.229,.224,.225], normalization_backend='CPU FP32, matching training ImageNetInput before GPU transfer', threshold='sigmoid(logits)>0.5',
        mask='Existing matching PNG 0/255, foreground>127', atol=ATOL, rtol=RTOL,
        allclose_definition='torch.isclose(folded,original,atol=1e-4,rtol=1e-4); any nonfinite fails; engineering FP32 check, not quantization acceptance',
        metrics='arithmetic mean per-image binary Dice/IoU; both empty=1; exactly one empty=0',
        folding_tool='torch.nn.utils.fusion.fuse_conv_bn_eval',
        folding_formula='s=gamma/sqrt(running_var+eps); W_new=s*W; b_new=beta+s*(b-running_mean); missing conv bias=0',
        fusion_dtype='CPU FP32; uses saved eval running mean/variance, epsilon, affine weight/bias',
        graph_pair_list_matches_existing_hardware_report=True, graph_connections_and_order_unchanged=True,
        original_model_is_baseline=True, folded_copy_is_formal_model=False, network_frozen=False, quantized=False)
    json_save(out / 'comparison_settings.json', settings)
    json_save(out / 'folding_pairs.json', pairs)
    csv_save(out / 'folding_pairs.csv', pairs)
    json_save(out / 'unfolded_batchnorm.json', skipped)
    torch.save(folded.state_dict(), out / 'folded_model.pth')
    # Reload saved model, check exact serialized state; use reloaded instance for all 180 comparisons.
    reloaded = load_folded_model(out)
    assert all(torch.equal(value, reloaded.state_dict()[key]) for key, value in folded.state_dict().items())
    del folded
    original, reloaded = original.to(device), reloaded.to(device)
    rows = list(csv.DictReader((EXP / 'val_manifest.csv').open(encoding='utf-8-sig')))
    assert len(rows) == 180
    actual_ids = {p.name for p in (ROOT / 'dataset/processed/val/images').glob('*.jpg')}
    assert actual_ids == {r['filename'] for r in rows}
    assert {Path(r['filename']).stem for r in rows} == {p.stem for p in (ROOT / 'dataset/processed/val/masks').glob('*.png')}
    # Reuse the frozen validation manifest and verify every original file hash.
    for row in rows:
        assert digest(ROOT / row['image_path']) == row['image_sha256']
        assert digest(ROOT / row['mask_path']) == row['mask_sha256']
    shutil.copy2(EXP / 'val_manifest.csv', out / 'validation_manifest.csv')
    stats = dict(logit_max_abs=0., logit_abs_sum=0., probability_max_abs=0., probability_abs_sum=0.,
                 total_output_pixels=0, nonfinite_original_logits=0, nonfinite_folded_logits=0,
                 nonfinite_original_probability=0, nonfinite_folded_probability=0,
                 allclose_failed_pixels=0, allclose_failed_images=0, mask_mismatch_pixels=0, mask_mismatch_images=0)
    per_image, mismatches = [], []
    mean = torch.tensor(settings['mean'], dtype=torch.float32).view(1,3,1,1)
    std = torch.tensor(settings['std'], dtype=torch.float32).view(1,3,1,1)
    with torch.inference_mode(), torch.autocast(device_type='cuda', enabled=False):
        for start in range(0,len(rows),8):
            group = rows[start:start+8]
            xs, targets = [], []
            for row in group:
                bgr = cv2.imread(str(ROOT / row['image_path']))
                mask = cv2.imread(str(ROOT / row['mask_path']), cv2.IMREAD_GRAYSCALE)
                assert bgr.shape == (256,256,3) and mask.shape == (256,256)
                assert set(np.unique(mask)).issubset({0,255})
                xs.append(torch.from_numpy(np.ascontiguousarray((cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB).astype(np.float32)/255).transpose(2,0,1))))
                targets.append(mask>127)
            x = ((torch.stack(xs)-mean)/std).contiguous().to(device)
            a,b = original(x), reloaded(x)
            assert a.dtype == b.dtype == torch.float32 and a.shape == b.shape == (len(group),1,256,256)
            pa,pb = a.sigmoid(), b.sigmoid()
            err,perr = (a-b).abs(), (pa-pb).abs()
            finite = torch.isfinite(a) & torch.isfinite(b)
            close = torch.isclose(b,a,atol=ATOL,rtol=RTOL) & finite
            ma,mb = pa>.5, pb>.5
            diff = ma!=mb
            stats['logit_max_abs'] = max(stats['logit_max_abs'],float(err.max()))
            stats['probability_max_abs'] = max(stats['probability_max_abs'],float(perr.max()))
            stats['logit_abs_sum'] += float(err.double().sum())
            stats['probability_abs_sum'] += float(perr.double().sum())
            stats['total_output_pixels'] += a.numel()
            for name,tensor in [('original_logits',a),('folded_logits',b),('original_probability',pa),('folded_probability',pb)]:
                stats['nonfinite_'+name] += int((~torch.isfinite(tensor)).sum())
            for i,row in enumerate(group):
                original_dice,original_iou,*_ = binary_scores(ma[i,0].cpu().numpy(),targets[i])
                folded_dice,folded_iou,*_ = binary_scores(mb[i,0].cpu().numpy(),targets[i])
                bad=int((~close[i]).sum()); changed=int(diff[i].sum())
                stats['allclose_failed_pixels'] += bad
                stats['allclose_failed_images'] += int(bad>0)
                stats['mask_mismatch_pixels'] += changed
                stats['mask_mismatch_images'] += int(changed>0)
                per_image.append(dict(filename=row['filename'],logit_max_abs=float(err[i].max()),logit_mean_abs=float(err[i].double().mean()),
                    probability_max_abs=float(perr[i].max()),probability_mean_abs=float(perr[i].double().mean()),allclose_failed_pixels=bad,
                    mask_mismatch_pixels=changed,original_dice=original_dice,folded_dice=folded_dice,original_iou=original_iou,folded_iou=folded_iou))
                for yy,xx in torch.nonzero(diff[i,0],as_tuple=False).cpu().tolist():
                    mismatches.append(dict(filename=row['filename'],y=yy,x=xx,original_logit=float(a[i,0,yy,xx]),folded_logit=float(b[i,0,yy,xx]),
                                           original_probability=float(pa[i,0,yy,xx]),folded_probability=float(pb[i,0,yy,xx])))
            print(f'Paired FP32 comparison {start+len(group)}/180',flush=True)
    stats['logit_mean_abs'] = stats.pop('logit_abs_sum')/stats['total_output_pixels']
    stats['probability_mean_abs'] = stats.pop('probability_abs_sum')/stats['total_output_pixels']
    stats['mask_mismatch_fraction'] = stats['mask_mismatch_pixels']/stats['total_output_pixels']
    stats['allclose_passed'] = stats['allclose_failed_pixels']==0
    stats['all_outputs_finite'] = all(v==0 for k,v in stats.items() if k.startswith('nonfinite_'))
    for prefix in ('original','folded'):
        for metric in ('dice','iou'):
            stats[prefix+'_mean_'+metric] = float(np.mean([r[prefix+'_'+metric] for r in per_image]))
    stats.update(folded_groups=len(pairs),remaining_bn_count=len(remaining_bn),saved_state_exact_reload_passed=True,
                 source_weights_unchanged=digest(weights)==original_hash,folded_weights_sha256=digest(out/'folded_model.pth'),
                 validation_count=len(rows),original_validation_record=dict(dice=best['val_image_dice'],iou=best['val_image_iou']))
    csv_save(out/'per_image_comparison.csv',per_image)
    csv_save(out/'threshold_mismatch_pixels.csv',mismatches,['filename','y','x','original_logit','folded_logit','original_probability','folded_probability'])
    json_save(out/'consistency_summary.json',stats)
    sources=out/'sources';sources.mkdir()
    for name in ('fold_mobilenetv2_eval.py','mobilenetv2_light_unet.py','evaluate_validation.py','unet.py'):
        shutil.copy2(ROOT/'code'/name,sources/name)
    report = [
        '# Conv＋BatchNorm eval折叠一致性验证','',
        f"原权重：{weights}；第27轮；SHA256：{original_hash}",
        f"实际图核对后折叠{len(pairs)}组Conv2d＋BatchNorm2d，全部与既有hardware_report配对一致；剩余BN {len(remaining_bn)}个。",
        '使用PyTorch fuse_conv_bn_eval，固定已保存的running_mean/var、eps和affine参数；BN替换为Identity。激活、Add、Concat、上采样顺序与连接保持原样。',
        f"180张验证图、相同FP32输入（ImageNet归一化）、eval/inference_mode，关闭AMP和TF32。全部输出非有限数统计：{ {k:v for k,v in stats.items() if k.startswith('nonfinite_')} }。",
        f"Logits绝对误差：最大{stats['logit_max_abs']:.12g}，平均{stats['logit_mean_abs']:.12g}；sigmoid概率：最大{stats['probability_max_abs']:.12g}，平均{stats['probability_mean_abs']:.12g}。",
        f"预先设定allclose(atol=1e-4,rtol=1e-4)通过：{stats['allclose_passed']}；超限像素{stats['allclose_failed_pixels']}，涉及图片{stats['allclose_failed_images']}。未调整容差。",
        f"Mask差异{stats['mask_mismatch_pixels']}像素/{stats['total_output_pixels']}，比例{stats['mask_mismatch_fraction']:.12g}，涉及{stats['mask_mismatch_images']}张图片。阈值严格sigmoid>0.5，差异像素的具体logits和概率见threshold_mismatch_pixels.csv。",
        f"平均逐图Dice：{stats['original_mean_dice']:.12f} → {stats['folded_mean_dice']:.12f}；IoU：{stats['original_mean_iou']:.12f} → {stats['folded_mean_iou']:.12f}。",
        'Dice/IoU：双方Mask均空为1，只有一方为空为0；逐图算术平均。',
        f"折叠权重重新构建并strict加载验证通过：{stats['saved_state_exact_reload_passed']}；原权重哈希不变：{stats['source_weights_unchanged']}。",
        '加载入口：将sources加入Python路径，然后 from fold_mobilenetv2_eval import load_folded_model；model=load_folded_model(本目录路径, device="cuda")。默认eval，仍需使用原ImageNet归一化并对logits做sigmoid。不要用原始未折叠模型直接加载折叠state_dict。',
        '该副本是部署准备参考，原模型仍是基准；网络尚未冻结，也未进行INT8量化。此浮点误差容差不是量化验收标准。折叠后卷积带bias，BN为Identity；仍需处理ReLU6、残差、Concat和nearest上采样。',
        '没有合并输入归一化、改变padding或输出sigmoid，没有改队友接口，没有官方测试集推理、ONNX导出或DDR预算工作。',
    ]
    (out/'report_zh.md').write_text('\n\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(out),**stats),indent=2),flush=True)


if __name__=='__main__':
    main()
