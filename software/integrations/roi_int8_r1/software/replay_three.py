"""Optional CUDA replay of the three ROI masks using the unchanged formal integer core."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
import torch
from roi_to_rgb888 import load_roi_image,convert_roi
from official_reference_loader import load_official


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--delivery',required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    identity=json.loads((root/'model_identity.json').read_text('utf-8'))
    reference=load_official(Path(args.delivery),identity)
    rows=json.loads((root/'samples.json').read_text('utf-8'))
    with torch.inference_mode():
        for row in rows:
            folder=root/row['directory']
            image=load_roi_image(folder/row['input_file'])
            rgb,meta=convert_roi(image,row['roi'])
            data=(folder/'input.rgb888.bin').read_bytes()
            assert data==rgb.tobytes() and len(data)==196608
            assert np.array_equal(cv2.cvtColor(cv2.imread(str(folder/'roi_preview.png')),cv2.COLOR_BGR2RGB),image.crop_rgb(row['roi']))
            assert np.array_equal(cv2.cvtColor(cv2.imread(str(folder/'input_rgb_preview.png')),cv2.COLOR_BGR2RGB),rgb)
            q=reference.run(np.frombuffer(data,np.uint8).reshape(256,256,3))
            assert reference.spec['mask_foreground_min_integer']==1
            mask=(q[0,0].cpu().numpy()>=1).astype(np.uint8)*255
            assert mask.tobytes()==(folder/'mask.bin').read_bytes()
            assert np.array_equal(mask,cv2.imread(str(folder/'mask.png'),cv2.IMREAD_GRAYSCALE))
            assert hashlib.sha256(q.cpu().numpy().tobytes()).hexdigest()==row['logits_sha256']
    assert reference.overflow==0
    print(json.dumps(dict(samples=len(rows),rgb_preview_and_masks_equal=True,accumulator_overflows=int(reference.overflow),
                         real_board_used=False,new_ROI_accuracy_assessment=False)))


if __name__=='__main__':main()
