# LSTM 模型训练指南

本文档介绍如何训练 BTC 价格预测 LSTM 模型，支持断点续训和早停功能。

## 数据说明

当前数据库中的 BTC 历史数据：
- **数据条数**: 3142 条（日线）
- **时间范围**: 2017-08-17 ~ 2026-03-24
- **约 8.6 年** 历史数据
- **价格范围**: $2,817 ~ $126,200

训练时自动按时间顺序划分：
- **训练集**: 前 70% 数据
- **验证集**: 后 30% 数据

## 快速开始

### 1. 首次训练

```bash
venv/Scripts/python scripts/train_model.py --asset BTC --epochs 50
```

### 2. 推荐参数训练

```bash
venv/Scripts/python scripts/train_model.py \
    --asset BTC \
    --epochs 100 \
    --lr 0.001 \
    --checkpoint-interval 10 \
    --patience 10
```

## 完整参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--asset` | 资产代码 (BTC/ETH) | BTC |
| `--epochs` | 总训练轮数 | 50 |
| `--lr` | 学习率 | 0.001 |
| `--device` | 训练设备 (cuda/cpu) | 自动检测 |
| `--checkpoint-interval` | 每N轮保存checkpoint | 10 |
| `--resume` | 从checkpoint恢复 | False |
| `--patience` | 早停耐心值（轮数） | 10 |
| `--no-best` | 不保存最佳模型 | False |

## 断点续训

### 场景1：训练中断后恢复

如果训练过程中按 `Ctrl+C` 中断，或电脑关机/重启：

```bash
# 自动找到最新的 checkpoint 继续训练
venv/Scripts/python scripts/train_model.py --asset BTC --epochs 100 --resume
```

### 场景2：增加训练轮数

已完成50轮，想继续训练到100轮：

```bash
venv/Scripts/python scripts/train_model.py --asset BTC --epochs 100 --resume
```

### 场景3：查看checkpoint状态

```bash
# 查看保存的 checkpoints
ls -la models/checkpoints/btc/

# 输出示例：
# checkpoint_epoch_10.pth
# checkpoint_epoch_20.pth
# checkpoint_epoch_30.pth
```

## 输出文件

训练完成后，以下文件将保存在 `models/` 目录：

```
models/
├── btc_lstm.pth                  # 最终模型（最后一轮）
├── btc_lstm_best.pth             # 最佳模型（验证损失最低）
├── btc_lstm_history.json         # 训练历史（损失、准确率等）
└── checkpoints/
    └── btc/
        ├── checkpoint_epoch_10.pth   # 第10轮 checkpoint
        ├── checkpoint_epoch_20.pth   # 第20轮 checkpoint
        └── checkpoint_epoch_30.pth   # 第30轮 checkpoint
```

## 训练监控

### 控制台输出

训练过程中会显示：

```
============================================================
训练设备: cuda
训练资产: BTC
总轮数: 100
Checkpoint间隔: 每 10 轮
============================================================

[1/3] 准备训练数据...
加载数据: 3142 条记录
时间范围: 2017-08-17 ~ 2026-03-24
训练集样本: 2180
验证集样本: 902

[2/3] 初始化模型...

[3/3] 开始训练 (第 1 轮 / 共 100 轮)...
------------------------------------------------------------
轮次   1/100 | 训练损失: 0.8234 | 验证损失: 0.7654 | 训练准确率:  52.3% | 验证准确率:  54.1% | 学习率: 0.001000 | 耗时: 12.5s

轮次   2/100 | 训练损失: 0.7123 | 验证损失: 0.6987 | 训练准确率:  58.1% | 验证准确率:  57.3% | 学习率: 0.000999 | 耗时: 11.8s
...
轮次  10/100 | 训练损失: 0.3456 | 验证损失: 0.3210 | 训练准确率:  72.5% | 验证准确率:  70.8% | 学习率: 0.000951 | 耗时: 11.2s
  ✓ Checkpoint saved: models/checkpoints/btc/checkpoint_epoch_10.pth
  ✓ 最佳模型已保存 (验证损失: 0.3210)
```

### 早停机制

如果 `--patience 10`，连续10轮验证损失没有改善，训练会自动停止：

```
[早停] 10 轮未改善，停止训练
```

## 使用训练好的模型

### 1. 策略自动加载

`LSTMStrategy` 会自动加载最佳模型：

```python
from src.strategies import LSTMStrategy

strategy = LSTMStrategy()  # 自动加载 models/btc_lstm_best.pth
result = strategy.predict(df, forecast_days=7)
```

### 2. 手动加载模型

```python
from src.strategies.ml_strategy import MLStrategyBase

strategy = MLStrategyBase(
    name="LSTM",
    description="手动加载模型",
    model_path="models/btc_lstm_best.pth"
)
```

## 常见问题

### Q: 训练需要多长时间？

A: 取决于硬件：
- **GPU (CUDA)**: 每轮约 5-15 秒，100轮约 10-25 分钟
- **CPU**: 每轮约 30-60 秒，100轮约 50-100 分钟

### Q: 如何知道训练是否收敛？

A: 观察以下几点：
1. 验证损失不再明显下降
2. 验证准确率达到平台期（约 65-75%）
3. 训练损失和验证损失差距不大（避免过拟合）

### Q: checkpoint 占用多少空间？

A: 每个 checkpoint 约 2-3 MB，默认保留最近 5 个，共约 10-15 MB。

### Q: 可以同时训练多个资产吗？

A: 可以，checkpoints 按资产代码分目录存储：

```bash
# 终端1：训练 BTC
venv/Scripts/python scripts/train_model.py --asset BTC --epochs 100

# 终端2：训练 ETH
venv/Scripts/python scripts/train_model.py --asset ETH --epochs 100
```

### Q: 如何清理旧的 checkpoints？

A: 脚本会自动保留最近 5 个 checkpoint，手动清理：

```bash
# 删除所有 checkpoints（不影响最佳模型）
rm -rf models/checkpoints/

# 或只删除特定资产的 checkpoints
rm -rf models/checkpoints/btc/
```

## 高级用法

### 自定义模型参数

如需调整模型结构（隐藏层大小、层数等），需修改 `src/strategies/ml_strategy.py` 中的 `LSTMPredictor` 类定义。

### 特征工程扩展

如需添加新特征，修改 `FeatureExtractor.FEATURE_NAMES` 和 `extract_features()` 方法。

### 训练历史可视化

```python
import json
import matplotlib.pyplot as plt

with open('models/btc_lstm_history.json', 'r') as f:
    data = json.load(f)

history = data['history']

plt.figure(figsize=(12, 4))

plt.subplot(1, 2, 1)
plt.plot(history['train_loss'], label='Train Loss')
plt.plot(history['val_loss'], label='Val Loss')
plt.xlabel('Epoch')
plt.ylabel('Loss')
plt.legend()
plt.title('Training Loss')

plt.subplot(1, 2, 2)
plt.plot(history['train_acc'], label='Train Acc')
plt.plot(history['val_acc'], label='Val Acc')
plt.xlabel('Epoch')
plt.ylabel('Accuracy (%)')
plt.legend()
plt.title('Training Accuracy')

plt.tight_layout()
plt.show()
```

## 故障排除

### 错误：CUDA out of memory

```bash
# 使用 CPU 训练
venv/Scripts/python scripts/train_model.py --asset BTC --device cpu
```

### 错误：数据不足

```bash
# 先更新数据
venv/Scripts/python -c "from app import auto_update_data; auto_update_data()"
```

### 错误：找不到 checkpoint

```bash
# 确认 checkpoint 目录存在
ls models/checkpoints/btc/

# 如果没有，检查是否已完成至少一轮训练
```
