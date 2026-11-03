import os
import cv2
import random
import shutil

# =========================
# 路径设置
# =========================
ROOT = r"E:\FPGA_Skin_Project\dataset"

TRAIN_IMG_DIR = os.path.join(ROOT, "ISBI2016_ISIC_Part1_Training_Data")
TRAIN_MASK_DIR = os.path.join(ROOT, "ISBI2016_ISIC_Part1_Training_GroundTruth")

TEST_IMG_DIR = os.path.join(ROOT, "ISBI2016_ISIC_Part1_Test_Data")
TEST_MASK_DIR = os.path.join(ROOT, "ISBI2016_ISIC_Part1_Test_GroundTruth")

OUT_DIR = os.path.join(ROOT, "processed")

IMAGE_SIZE = 256
VAL_RATIO = 0.2
SEED = 42

# =========================
# 创建输出目录
# =========================
folders = [
    "train/images",
    "train/masks",
    "val/images",
    "val/masks",
    "test/images",
    "test/masks"
]

for folder in folders:
    os.makedirs(os.path.join(OUT_DIR, folder), exist_ok=True)


def process_pair(img_path, mask_path, out_img_path, out_mask_path):
    # 读取原图
    image = cv2.imread(img_path)

    # 灰度方式读取Mask
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

    if image is None:
        raise ValueError(f"无法读取图片：{img_path}")

    if mask is None:
        raise ValueError(f"无法读取Mask：{mask_path}")

    # 原图使用普通插值
    image = cv2.resize(
        image,
        (IMAGE_SIZE, IMAGE_SIZE),
        interpolation=cv2.INTER_AREA
    )

    # Mask必须使用最近邻插值
    mask = cv2.resize(
        mask,
        (IMAGE_SIZE, IMAGE_SIZE),
        interpolation=cv2.INTER_NEAREST
    )

    # 强制变成二值Mask
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    cv2.imwrite(out_img_path, image)
    cv2.imwrite(out_mask_path, mask)


# =========================
# 处理训练 / 验证集
# =========================
train_images = sorted([
    f for f in os.listdir(TRAIN_IMG_DIR)
    if f.lower().endswith(".jpg")
])

print("训练原图数量：", len(train_images))

random.seed(SEED)
random.shuffle(train_images)

val_count = int(len(train_images) * VAL_RATIO)

val_images = train_images[:val_count]
real_train_images = train_images[val_count:]

print("划分后训练集：", len(real_train_images))
print("划分后验证集：", len(val_images))


def process_train_or_val(file_list, split):
    for i, img_name in enumerate(file_list):

        # ISIC_0000000.jpg
        image_id = os.path.splitext(img_name)[0]

        # 对应Mask名称
        mask_name = image_id + "_Segmentation.png"

        img_path = os.path.join(TRAIN_IMG_DIR, img_name)
        mask_path = os.path.join(TRAIN_MASK_DIR, mask_name)

        if not os.path.exists(mask_path):
            print("缺少Mask：", mask_name)
            continue

        out_img = os.path.join(
            OUT_DIR, split, "images",
            image_id + ".jpg"
        )

        out_mask = os.path.join(
            OUT_DIR, split, "masks",
            image_id + ".png"
        )

        process_pair(
            img_path,
            mask_path,
            out_img,
            out_mask
        )

        if (i + 1) % 100 == 0:
            print(f"{split} 已处理 {i + 1}/{len(file_list)}")


process_train_or_val(real_train_images, "train")
process_train_or_val(val_images, "val")


# =========================
# 处理官方测试集
# =========================
test_images = sorted([
    f for f in os.listdir(TEST_IMG_DIR)
    if f.lower().endswith(".jpg")
])

print("测试集数量：", len(test_images))

for i, img_name in enumerate(test_images):

    image_id = os.path.splitext(img_name)[0]
    mask_name = image_id + "_Segmentation.png"

    img_path = os.path.join(TEST_IMG_DIR, img_name)
    mask_path = os.path.join(TEST_MASK_DIR, mask_name)

    if not os.path.exists(mask_path):
        print("测试集缺少Mask：", mask_name)
        continue

    out_img = os.path.join(
        OUT_DIR, "test", "images",
        image_id + ".jpg"
    )

    out_mask = os.path.join(
        OUT_DIR, "test", "masks",
        image_id + ".png"
    )

    process_pair(
        img_path,
        mask_path,
        out_img,
        out_mask
    )

    if (i + 1) % 100 == 0:
        print(f"test 已处理 {i + 1}/{len(test_images)}")


print("\n==========================")
print("数据预处理完成！")
print("输出目录：", OUT_DIR)
print("==========================")