# 来源、许可与已有评估

数据为HAM10000 / ISIC2018 Task3官方训练池，按seed42固定选900个不同lesion_id、每病灶1张；没有拼接ISIC2016或精选展示图库。原七类标签、诊断确认方式、病灶及划分保留在data/*.csv；900张图像未随包提供，仅附4张原有验证样例。

- 官方任务：https://challenge.isic-archive.com/landing/2018/47/
- 数据论文：https://arxiv.org/abs/1803.10417
- 官方分组：https://isic-archive.s3.amazonaws.com/challenges/2018/ISIC2018_Task3_Training_LesionGroupings.csv
- 官方标签：https://isic-archive.s3.amazonaws.com/challenges/2018/ISIC2018_Task3_Training_GroundTruth.zip

HAM10000数据版权归ViDIR Group, Department of Dermatology, Medical University of Vienna；CC Attribution-NonCommercial 4.0。原ATTRIBUTION.txt及完整LICENSE.txt位于data/，保留字节未改。用于非商业教学/研究时仍须保留归属及许可，不因整理包而改变许可。不同标签诊断依据可能为组织病理、随访或专家共识等，未把全部样本称为病理确诊。

本包完整收录既有reports/REPORT_zh.md、逐图CSV、混淆矩阵和错误案例。测试136张人为50/50平衡，每个非MEL亚型仅11—12张；按lesion_id隔离但缺患者ID。不代表真实患病比例，不是官方ISIC2018测试，也不是与V1同集比较。本次没有重新评估测试集。原始训练config和源码哈希仅作来源记录，推理不需要原数据、原预训练缓存或原工程目录。
