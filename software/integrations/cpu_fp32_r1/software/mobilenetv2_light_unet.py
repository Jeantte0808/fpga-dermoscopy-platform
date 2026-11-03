"""Fixed MobileNetV2 IMAGENET1K_V2 encoder and specified light U-Net decoder."""
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from torchvision.models import mobilenet_v2, MobileNet_V2_Weights

IMAGENET_MEAN = [.485, .456, .406]
IMAGENET_STD = [.229, .224, .225]
STAGE_REQUEST = [(2,16), (4,24), (8,32), (16,96), (32,320)]


def discover_features(backbone):
    """Choose stage ends from actual runtime shapes, not assumed indices."""
    mode = backbone.training
    backbone.eval()
    device = next(backbone.parameters()).device
    x = torch.zeros(1,3,256,256,device=device)
    rows=[]
    with torch.no_grad():
        for index, module in enumerate(backbone.features):
            before=list(x.shape); x=module(x)
            rows.append(dict(index=index,module=type(module).__name__,input_shape=before,
                output_shape=list(x.shape),output_stride=256//x.shape[-1],channels=x.shape[1],
                residual=bool(getattr(module,'use_res_connect',False))))
    selected=[]
    for stride,channels in STAGE_REQUEST:
        matches=[r for r in rows if r['output_stride']==stride and r['channels']==channels
                 and r['output_shape'][-2:]==[256//stride,256//stride]]
        assert matches, f'MobileNetV2 has no requested feature stride{stride},channels{channels}: {rows}'
        selected.append(matches[-1]['index'])
    assert rows[-1]['channels']==1280 and selected[-1] < rows[-1]['index']
    assert selected[-1] == len(rows)-2, 'Unexpected layers between 320 stage and final expansion'
    backbone.train(mode)
    return rows, tuple(selected)


class DecoderBlock(nn.Module):
    def __init__(self,input_channels,output_channels):
        super().__init__()
        self.conv1=nn.Conv2d(input_channels,output_channels,3,padding=1,bias=True)
        self.relu1=nn.ReLU(inplace=True)
        self.conv2=nn.Conv2d(output_channels,output_channels,3,padding=1,bias=True)
        self.relu2=nn.ReLU(inplace=True)

    def forward(self,x,skip):
        x=F.interpolate(x,scale_factor=2.,mode='nearest')
        x=torch.cat([x,skip],dim=1)
        return self.relu2(self.conv2(self.relu1(self.conv1(x))))


class MobileNetV2LightUNet(nn.Module):
    def __init__(self,pretrained=True,stage_indices=None):
        super().__init__()
        weights=MobileNet_V2_Weights.IMAGENET1K_V2 if pretrained else None
        backbone=mobilenet_v2(weights=weights,width_mult=1.0,progress=True)
        if stage_indices is None:
            # Probe on GPU where available; evaluation does not update BatchNorm buffers.
            if torch.cuda.is_available(): backbone=backbone.cuda()
            feature_rows,stage_indices=discover_features(backbone)
            self.feature_discovery=feature_rows
        else:
            self.feature_discovery=None
        self.stage_indices=tuple(stage_indices)
        self.encoder=nn.Sequential(*list(backbone.features.children())[:self.stage_indices[-1]+1])
        self.decoder16=DecoderBlock(320+96,128)
        self.decoder8=DecoderBlock(128+32,64)
        self.decoder4=DecoderBlock(64+24,32)
        self.decoder2=DecoderBlock(32+16,16)
        self.final_conv=nn.Conv2d(16,16,3,padding=1,bias=True)
        self.final_relu=nn.ReLU(inplace=True)
        self.head=nn.Conv2d(16,1,1,bias=True)

    def forward(self,x):
        selected=[]
        for index,module in enumerate(self.encoder):
            x=module(x)
            if index in self.stage_indices: selected.append(x)
        f2,f4,f8,f16,f32=selected
        x=self.decoder16(f32,f16)
        x=self.decoder8(x,f8)
        x=self.decoder4(x,f4)
        x=self.decoder2(x,f2)
        x=F.interpolate(x,scale_factor=2.,mode='nearest')
        return self.head(self.final_relu(self.final_conv(x)))

    def decoder_parameters(self):
        for name,parameter in self.named_parameters():
            if not name.startswith('encoder.'): yield parameter


def verify_pretrained_encoder(model,checkpoint_path):
    official=torch.load(Path(checkpoint_path),map_location='cpu',weights_only=True)
    matched=[]
    for name,value in model.encoder.state_dict().items():
        source='features.'+name
        assert source in official and torch.equal(value.detach().cpu(),official[source]), f'Not pretrained: {source}'
        matched.append(source)
    return dict(all_encoder_tensors_exactly_equal_official=True,matched_tensors=len(matched),
        encoder_parameter_count=sum(p.numel() for p in model.encoder.parameters()),
        classifier_removed=not any(isinstance(m,nn.Linear) for m in model.modules()),
        final1280_expansion_removed=len(model.encoder)==model.stage_indices[-1]+1)
