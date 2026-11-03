"""Unchanged replace/load_folded_model functions extracted from the verified loader.
Only imports are trimmed; no new BN folding is performed at load time.
"""
import json
from pathlib import Path
import torch
from torch import nn
from mobilenetv2_light_unet import MobileNetV2LightUNet

def replace(model, name, module):
    parent_name, _, child = name.rpartition('.')
    parent = model.get_submodule(parent_name) if parent_name else model
    setattr(parent, child, module)

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
