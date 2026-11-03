import os
import cv2
import torch
from torch.utils.data import Dataset


class SkinLesionDataset(Dataset):
    def __init__(self, image_dir, mask_dir):
        self.image_dir = image_dir
        self.mask_dir = mask_dir

        self.image_names = sorted([
            f for f in os.listdir(image_dir)
            if f.lower().endswith(".jpg")
        ])

    def __len__(self):
        return len(self.image_names)

    def __getitem__(self, idx):
        image_name = self.image_names[idx]

        image_id = os.path.splitext(image_name)[0]
        mask_name = image_id + ".png"

        image_path = os.path.join(self.image_dir, image_name)
        mask_path = os.path.join(self.mask_dir, mask_name)

        # 读取原图
        image = cv2.imread(image_path)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # 读取mask
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

        # 归一化到0~1
        image = image.astype("float32") / 255.0
        mask = mask.astype("float32") / 255.0

        # HWC -> CHW
        image = image.transpose(2, 0, 1)

        # mask增加一个通道维度
        mask = mask[None, :, :]

        # 转成PyTorch Tensor
        image = torch.tensor(image, dtype=torch.float32)
        mask = torch.tensor(mask, dtype=torch.float32)

        return image, mask