"""
模型训练脚本

用于训练LSTM价格预测模型，支持断点续训
"""
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler
import os
import json
import glob
from datetime import datetime
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.strategies.ml_strategy import FeatureExtractor, LSTMPredictor
from src.database import Database
from config import DB_PATH, DEFAULT_CURRENCY


class PriceDataset(Dataset):
    """价格数据集"""

    def __init__(self, features, targets, seq_len=60):
        self.features = features
        self.targets = targets
        self.seq_len = seq_len

    def __len__(self):
        return max(0, len(self.features) - self.seq_len)

    def __getitem__(self, idx):
        x = self.features[idx:idx + self.seq_len]
        y = self.targets[idx + self.seq_len - 1]
        return torch.FloatTensor(x), torch.FloatTensor(y)


def get_checkpoint_dir(asset_code):
    """获取checkpoint目录"""
    checkpoint_dir = f'models/checkpoints/{asset_code.lower()}'
    os.makedirs(checkpoint_dir, exist_ok=True)
    return checkpoint_dir


def get_latest_checkpoint(asset_code):
    """查找最新的checkpoint"""
    checkpoint_dir = get_checkpoint_dir(asset_code)
    checkpoints = glob.glob(os.path.join(checkpoint_dir, 'checkpoint_epoch_*.pth'))

    if not checkpoints:
        return None

    # 按修改时间排序，取最新的
    checkpoints.sort(key=os.path.getmtime, reverse=True)
    return checkpoints[0]


def save_checkpoint(checkpoint_dir, epoch, model, optimizer, scheduler, scaler,
                    best_val_loss, history, config):
    """保存训练checkpoint"""
    checkpoint_path = os.path.join(checkpoint_dir, f'checkpoint_epoch_{epoch}.pth')

    checkpoint = {
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'scheduler_state_dict': scheduler.state_dict() if scheduler else None,
        'feature_mean': scaler.mean_,
        'feature_std': scaler.scale_,
        'best_val_loss': best_val_loss,
        'history': history,
        'config': config,
        'saved_at': datetime.now().isoformat()
    }

    torch.save(checkpoint, checkpoint_path)
    print(f"  ✓ Checkpoint saved: {checkpoint_path}")

    # 只保留最近5个checkpoint，删除旧的
    all_checkpoints = glob.glob(os.path.join(checkpoint_dir, 'checkpoint_epoch_*.pth'))
    all_checkpoints.sort(key=os.path.getmtime, reverse=True)
    for old_ckpt in all_checkpoints[5:]:
        os.remove(old_ckpt)
        print(f"  ✓ Removed old checkpoint: {old_ckpt}")

    return checkpoint_path


def load_checkpoint(checkpoint_path, model, optimizer, scheduler, device):
    """加载checkpoint"""
    checkpoint = torch.load(checkpoint_path, map_location=device)

    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    if scheduler and checkpoint.get('scheduler_state_dict'):
        scheduler.load_state_dict(checkpoint['scheduler_state_dict'])

    return checkpoint


def prepare_data(asset_code='BTC', start_date=None, forecast_horizon=7, train_ratio=0.7):
    """
    准备训练数据

    Returns:
        train_loader, val_loader, scaler, raw_df
    """
    # 获取历史数据
    db = Database(DB_PATH)
    df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date)

    print(f"加载数据: {len(df)} 条记录")
    print(f"时间范围: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")

    if len(df) < 200:
        raise ValueError(f"数据不足: 只有 {len(df)} 条记录，需要至少 200 条")

    # 提取特征
    features_df = FeatureExtractor.extract_features(df)
    features_df = features_df.fillna(0)

    # 准备目标变量
    # 未来N日收益率
    future_returns = df['close_price'].pct_change(forecast_horizon).shift(-forecast_horizon)
    # 上涨/下跌标签
    up_label = (future_returns > 0).astype(float)
    # 波动率（使用未来N日的实际波动率）
    future_volatility = df['close_price'].pct_change().rolling(forecast_horizon).std().shift(-forecast_horizon)

    # 组合目标 [return, up_prob, volatility]
    targets = np.column_stack([
        future_returns.fillna(0).values,
        up_label.fillna(0).values,
        future_volatility.fillna(0).values * np.sqrt(365)  # 年化波动率
    ])

    # 标准化特征
    feature_values = features_df[FeatureExtractor.FEATURE_NAMES].values
    scaler = StandardScaler()
    feature_values_scaled = scaler.fit_transform(feature_values)

    # 划分训练/验证集（时间序列划分）
    n_samples = len(feature_values_scaled) - 60  # 减去序列长度
    train_size = int(n_samples * train_ratio)

    train_features = feature_values_scaled[:train_size + 60]
    train_targets = targets[:train_size + 60]

    val_features = feature_values_scaled[train_size:]
    val_targets = targets[train_size:]

    print(f"训练集样本: {len(train_features) - 60}")
    print(f"验证集样本: {len(val_features) - 60}")

    # 创建数据集
    train_dataset = PriceDataset(train_features, train_targets, seq_len=60)
    val_dataset = PriceDataset(val_features, val_targets, seq_len=60)

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)

    return train_loader, val_loader, scaler, df


def train_model(asset_code='BTC', epochs=50, lr=0.001, device=None,
                resume=False, checkpoint_interval=10,
                save_best=True, patience=10):
    """
    训练LSTM模型，支持断点续训

    Args:
        asset_code: 资产代码
        epochs: 训练轮数
        lr: 学习率
        device: 计算设备
        resume: 是否从checkpoint恢复
        checkpoint_interval: 每N轮保存一次checkpoint
        save_best: 是否保存最佳模型
        patience: 早停耐心值（轮数）
    """
    if device is None:
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    print("=" * 60)
    print(f"训练设备: {device}")
    print(f"训练资产: {asset_code}")
    print(f"总轮数: {epochs}")
    print(f"学习率: {lr}")
    print(f"Checkpoint间隔: 每 {checkpoint_interval} 轮")
    print("=" * 60)

    # 准备数据
    print("\n[1/3] 准备训练数据...")
    train_loader, val_loader, scaler, raw_df = prepare_data(asset_code)

    # 初始化模型
    print("\n[2/3] 初始化模型...")
    input_size = len(FeatureExtractor.FEATURE_NAMES)
    model = LSTMPredictor(
        input_size=input_size,
        hidden_size=128,
        num_layers=2,
        dropout=0.2
    ).to(device)

    # 优化器和学习率调度
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    # 损失函数
    mse_loss = nn.MSELoss()
    bce_loss = nn.BCELoss()

    # 训练状态
    start_epoch = 1
    best_val_loss = float('inf')
    history = {
        'train_loss': [],
        'val_loss': [],
        'train_acc': [],
        'val_acc': [],
        'learning_rate': []
    }
    epochs_no_improve = 0

    checkpoint_dir = get_checkpoint_dir(asset_code)
    best_model_path = f'models/{asset_code.lower()}_lstm_best.pth'
    final_model_path = f'models/{asset_code.lower()}_lstm.pth'

    os.makedirs('models', exist_ok=True)

    # 恢复训练
    if resume:
        latest_checkpoint = get_latest_checkpoint(asset_code)
        if latest_checkpoint:
            print(f"\n[恢复] 加载 checkpoint: {latest_checkpoint}")
            checkpoint = load_checkpoint(latest_checkpoint, model, optimizer, scheduler, device)
            start_epoch = checkpoint['epoch'] + 1
            best_val_loss = checkpoint.get('best_val_loss', float('inf'))
            history = checkpoint.get('history', history)
            print(f"  从第 {start_epoch} 轮继续训练")
            print(f"  当前最佳验证损失: {best_val_loss:.4f}")
        else:
            print("\n[警告] 未找到checkpoint，从头开始训练")

    print(f"\n[3/3] 开始训练 (第 {start_epoch} 轮 / 共 {epochs} 轮)...")
    print("-" * 60)

    for epoch in range(start_epoch, epochs + 1):
        epoch_start_time = datetime.now()

        # 训练
        model.train()
        train_loss = 0.0
        train_direction_acc = 0
        train_samples = 0

        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)

            optimizer.zero_grad()

            # 前向传播
            outputs = model(batch_x)

            # 计算损失
            return_loss = mse_loss(outputs[:, 0], batch_y[:, 0])
            direction_loss = bce_loss(outputs[:, 1], batch_y[:, 1])
            vol_loss = mse_loss(outputs[:, 2], batch_y[:, 2])
            loss = return_loss + direction_loss + 0.1 * vol_loss

            loss.backward()
            optimizer.step()

            train_loss += loss.item()

            # 计算方向准确率
            pred_direction = (outputs[:, 1] > 0.5).float()
            true_direction = batch_y[:, 1]
            train_direction_acc += (pred_direction == true_direction).sum().item()
            train_samples += len(batch_y)

        # 验证
        model.eval()
        val_loss = 0.0
        val_direction_acc = 0
        val_samples = 0

        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x = batch_x.to(device)
                batch_y = batch_y.to(device)

                outputs = model(batch_x)

                return_loss = mse_loss(outputs[:, 0], batch_y[:, 0])
                direction_loss = bce_loss(outputs[:, 1], batch_y[:, 1])
                vol_loss = mse_loss(outputs[:, 2], batch_y[:, 2])
                loss = return_loss + direction_loss + 0.1 * vol_loss

                val_loss += loss.item()

                pred_direction = (outputs[:, 1] > 0.5).float()
                true_direction = batch_y[:, 1]
                val_direction_acc += (pred_direction == true_direction).sum().item()
                val_samples += len(batch_y)

        scheduler.step()

        # 计算指标
        train_loss /= len(train_loader)
        val_loss /= len(val_loader)
        train_acc = train_direction_acc / train_samples * 100
        val_acc = val_direction_acc / val_samples * 100
        current_lr = optimizer.param_groups[0]['lr']

        # 记录历史
        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_loss)
        history['train_acc'].append(train_acc)
        history['val_acc'].append(val_acc)
        history['learning_rate'].append(current_lr)

        epoch_time = (datetime.now() - epoch_start_time).total_seconds()

        # 打印进度
        print(f"轮次 {epoch:3d}/{epochs} | "
              f"训练损失: {train_loss:.4f} | "
              f"验证损失: {val_loss:.4f} | "
              f"训练准确率: {train_acc:5.1f}% | "
              f"验证准确率: {val_acc:5.1f}% | "
              f"学习率: {current_lr:.6f} | "
              f"耗时: {epoch_time:.1f}s")

        # 保存最佳模型
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            epochs_no_improve = 0

            if save_best:
                best_checkpoint = {
                    'epoch': epoch,
                    'model_state_dict': model.state_dict(),
                    'config': {
                        'input_size': input_size,
                        'hidden_size': 128,
                        'num_layers': 2,
                        'dropout': 0.2
                    },
                    'feature_mean': scaler.mean_,
                    'feature_std': scaler.scale_,
                    'training_info': {
                        'asset': asset_code,
                        'best_epoch': epoch,
                        'best_val_loss': best_val_loss,
                        'val_accuracy': val_acc,
                        'trained_at': datetime.now().isoformat()
                    }
                }
                torch.save(best_checkpoint, best_model_path)
                print(f"  ✓ 最佳模型已保存 (验证损失: {best_val_loss:.4f})")
        else:
            epochs_no_improve += 1

        # 定期保存checkpoint
        if epoch % checkpoint_interval == 0:
            config = {
                'asset_code': asset_code,
                'epochs': epochs,
                'lr': lr,
                'input_size': input_size
            }
            save_checkpoint(
                checkpoint_dir, epoch, model, optimizer, scheduler, scaler,
                best_val_loss, history, config
            )

        # 早停检查
        if epochs_no_improve >= patience:
            print(f"\n[早停] {patience} 轮未改善，停止训练")
            break

        print()

    # 保存最终模型
    final_checkpoint = {
        'model_state_dict': model.state_dict(),
        'config': {
            'input_size': input_size,
            'hidden_size': 128,
            'num_layers': 2,
            'dropout': 0.2
        },
        'feature_mean': scaler.mean_,
        'feature_std': scaler.scale_,
        'training_info': {
            'asset': asset_code,
            'epochs': epoch,
            'final_val_loss': val_loss,
            'best_val_loss': best_val_loss,
            'trained_at': datetime.now().isoformat()
        },
        'history': history
    }

    torch.save(final_checkpoint, final_model_path)

    # 保存训练历史
    history_path = f'models/{asset_code.lower()}_lstm_history.json'
    with open(history_path, 'w') as f:
        json.dump({
            'history': history,
            'best_val_loss': best_val_loss,
            'total_epochs': epoch,
            'asset': asset_code
        }, f, indent=2)

    print("=" * 60)
    print("训练完成!")
    print(f"  最佳验证损失: {best_val_loss:.4f}")
    print(f"  最终验证损失: {val_loss:.4f}")
    print(f"  最佳模型: {best_model_path}")
    print(f"  最终模型: {final_model_path}")
    print(f"  训练历史: {history_path}")
    print(f"  Checkpoints: {checkpoint_dir}/")
    print("=" * 60)

    return final_model_path, best_model_path, history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='训练LSTM价格预测模型')
    parser.add_argument('--asset', type=str, default='BTC', help='资产代码 (默认: BTC)')
    parser.add_argument('--epochs', type=int, default=50, help='训练轮数 (默认: 50)')
    parser.add_argument('--lr', type=float, default=0.001, help='学习率 (默认: 0.001)')
    parser.add_argument('--device', type=str, default=None, help='设备 (cuda/cpu)')
    parser.add_argument('--resume', action='store_true', help='从checkpoint恢复训练')
    parser.add_argument('--checkpoint-interval', type=int, default=10,
                        help='每N轮保存一次checkpoint (默认: 10)')
    parser.add_argument('--no-best', action='store_true', help='不保存最佳模型')
    parser.add_argument('--patience', type=int, default=10,
                        help='早停耐心值，N轮未改善则停止 (默认: 10)')

    args = parser.parse_args()

    device = args.device
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    train_model(
        asset_code=args.asset,
        epochs=args.epochs,
        lr=args.lr,
        device=torch.device(device),
        resume=args.resume,
        checkpoint_interval=args.checkpoint_interval,
        save_best=not args.no_best,
        patience=args.patience
    )
