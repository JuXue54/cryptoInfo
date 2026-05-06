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


def get_checkpoint_dir(asset_code, model_id='default'):
    """获取checkpoint目录"""
    checkpoint_dir = f'models/checkpoints/{asset_code.lower()}_{model_id}'
    os.makedirs(checkpoint_dir, exist_ok=True)
    return checkpoint_dir


def get_latest_checkpoint(asset_code, model_id='default'):
    """查找最新的checkpoint"""
    checkpoint_dir = get_checkpoint_dir(asset_code, model_id)
    checkpoints = glob.glob(os.path.join(checkpoint_dir, 'checkpoint_epoch_*.pth'))

    if not checkpoints:
        return None

    # 按epoch编号排序，取最新的
    def extract_epoch(path):
        match = os.path.basename(path).replace('checkpoint_epoch_', '').replace('.pth', '')
        try:
            return int(match)
        except:
            return 0

    checkpoints.sort(key=extract_epoch, reverse=True)
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

    # 只保留最近10个checkpoint，删除旧的
    all_checkpoints = glob.glob(os.path.join(checkpoint_dir, 'checkpoint_epoch_*.pth'))

    def extract_epoch(path):
        match = os.path.basename(path).replace('checkpoint_epoch_', '').replace('.pth', '')
        try:
            return int(match)
        except:
            return 0

    all_checkpoints.sort(key=extract_epoch, reverse=True)
    for old_ckpt in all_checkpoints[10:]:
        os.remove(old_ckpt)

    return checkpoint_path


def load_checkpoint(checkpoint_path, model, optimizer, scheduler, device):
    """加载checkpoint"""
    checkpoint = torch.load(checkpoint_path, map_location=device)

    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    if scheduler and checkpoint.get('scheduler_state_dict'):
        scheduler.load_state_dict(checkpoint['scheduler_state_dict'])

    return checkpoint


def prepare_data(asset_code='BTC', start_date=None, forecast_horizon=7,
                 train_ratio=0.7, batch_size=32, seq_len=60):
    """
    准备训练数据

    Args:
        asset_code: 资产代码
        start_date: 开始日期
        forecast_horizon: 预测未来天数
        train_ratio: 训练集比例
        batch_size: 批次大小
        seq_len: 输入序列长度

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
    n_samples = len(feature_values_scaled) - seq_len  # 减去序列长度
    train_size = int(n_samples * train_ratio)

    train_features = feature_values_scaled[:train_size + seq_len]
    train_targets = targets[:train_size + seq_len]

    val_features = feature_values_scaled[train_size:]
    val_targets = targets[train_size:]

    print(f"训练集样本: {len(train_features) - seq_len}")
    print(f"验证集样本: {len(val_features) - seq_len}")

    # 创建数据集
    train_dataset = PriceDataset(train_features, train_targets, seq_len=seq_len)
    val_dataset = PriceDataset(val_features, val_targets, seq_len=seq_len)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    return train_loader, val_loader, scaler, df


def count_model_parameters(model):
    """计算模型参数数量"""
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return total_params, trainable_params


def format_time(seconds):
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds//60:.0f}m {seconds%60:.0f}s"
    else:
        return f"{seconds//3600:.0f}h {(seconds%3600)//60:.0f}m"


def train_model(asset_code='BTC', epochs=50, lr=0.001, device=None,
                resume=False, checkpoint_interval=1,
                save_best=True, patience=10,
                progress_callback=None,
                model_id='default',
                hidden_size=64, num_layers=1, dropout=0.4,
                batch_size=128, seq_len=60, forecast_horizon=7,
                weight_decay=1e-3, grad_clip=1.0):
    """
    训练LSTM模型，支持断点续训

    Args:
        asset_code: 资产代码
        epochs: 训练轮数
        lr: 学习率
        device: 计算设备
        resume: 是否从checkpoint恢复
        checkpoint_interval: 每N轮保存一次checkpoint (默认每轮都保存)
        save_best: 是否保存最佳模型
        patience: 早停耐心值（轮数）
        progress_callback: 进度回调函数
        model_id: 模型标识符，支持多模型
        hidden_size: LSTM隐藏层大小
        num_layers: LSTM层数
        dropout: Dropout率
        batch_size: 批次大小
        seq_len: 输入序列长度
        forecast_horizon: 预测未来天数
    """
    if device is None:
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    print("=" * 70)
    print(f"🚀 LSTM 模型训练启动")
    print("=" * 70)
    print(f"📊 资产: {asset_code}")
    print(f"🏷️  模型ID: {model_id}")
    print(f"⚙️  训练设备: {device}")
    print(f"📈 总轮数: {epochs} | 学习率: {lr}")
    print(f"🧠 网络结构: {num_layers}层LSTM, 隐藏层{hidden_size}, dropout={dropout}")
    print(f"📦 Batch大小: {batch_size} | 序列长度: {seq_len} | 预测 horizon: {forecast_horizon}天")
    print(f"💾 Checkpoint: 每{checkpoint_interval}轮保存, 保留最近10轮")
    print("=" * 70)

    # 准备数据
    print("\n[1/3] 准备训练数据...")
    train_loader, val_loader, scaler, raw_df = prepare_data(
        asset_code=asset_code,
        forecast_horizon=forecast_horizon,
        train_ratio=0.7,
        batch_size=batch_size,
        seq_len=seq_len
    )

    # 初始化模型
    print("\n[2/3] 初始化模型...")
    input_size = len(FeatureExtractor.FEATURE_NAMES)
    model = LSTMPredictor(
        input_size=input_size,
        hidden_size=hidden_size,
        num_layers=num_layers,
        dropout=dropout,
        forecast_horizon=forecast_horizon
    ).to(device)

    # 打印模型结构摘要
    total_params, trainable_params = count_model_parameters(model)
    print(f"  ✓ 模型已创建")
    print(f"     输入特征数: {input_size}")
    print(f"     总参数量: {total_params:,} ({total_params/1e6:.2f}M)")
    print(f"     可训练参数: {trainable_params:,} ({trainable_params/1e6:.2f}M)")

    # 优化器和学习率调度
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=5, factor=0.5)

    # 损失函数
    mse_loss = nn.MSELoss()
    bce_loss = nn.BCELoss()

    # 训练状态
    start_epoch = 1
    best_val_loss = float('inf')
    best_checkpoint = None
    history = {
        'train_loss': [],
        'val_loss': [],
        'train_acc': [],
        'val_acc': [],
        'learning_rate': []
    }
    epochs_no_improve = 0

    checkpoint_dir = get_checkpoint_dir(asset_code, model_id)
    best_model_path = f'models/{asset_code.lower()}_{model_id}_lstm_best.pth'
    final_model_path = f'models/{asset_code.lower()}_{model_id}_lstm.pth'

    os.makedirs('models', exist_ok=True)

    # 恢复训练
    if resume:
        latest_checkpoint = get_latest_checkpoint(asset_code, model_id)
        if latest_checkpoint:
            print(f"\n[恢复] 加载 checkpoint: {latest_checkpoint}")
            checkpoint = load_checkpoint(latest_checkpoint, model, optimizer, scheduler, device)
            start_epoch = checkpoint['epoch'] + 1
            best_val_loss = checkpoint.get('best_val_loss', float('inf'))
            history = checkpoint.get('history', history)
            # 恢复模型配置
            config = checkpoint.get('config', {})
            print(f"  从第 {start_epoch} 轮继续训练")
            print(f"  当前最佳验证损失: {best_val_loss:.4f}")
        else:
            print("\n[警告] 未找到checkpoint，从头开始训练")

    print(f"\n[3/3] 开始训练 (第 {start_epoch} 轮 / 共 {epochs} 轮)...")
    print("=" * 70)

    # 计算预计总时间
    start_time = datetime.now()

    for epoch in range(start_epoch, epochs + 1):
        epoch_start_time = datetime.now()

        # 训练
        model.train()
        train_loss = 0.0
        train_return_loss = 0.0
        train_direction_loss = 0.0
        train_vol_loss = 0.0
        train_direction_acc = 0
        train_samples = 0

        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)

            optimizer.zero_grad()

            # 前向传播
            outputs = model(batch_x)

            # 计算损失（分解各部分）
            return_loss = mse_loss(outputs[:, 0], batch_y[:, 0])
            direction_loss = bce_loss(outputs[:, 1], batch_y[:, 1])
            vol_loss = mse_loss(outputs[:, 2], batch_y[:, 2])
            loss = return_loss + direction_loss + 0.1 * vol_loss

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            optimizer.step()

            train_loss += loss.item()
            train_return_loss += return_loss.item()
            train_direction_loss += direction_loss.item()
            train_vol_loss += vol_loss.item()

            # 计算方向准确率
            pred_direction = (outputs[:, 1] > 0.5).float()
            true_direction = batch_y[:, 1]
            train_direction_acc += (pred_direction == true_direction).sum().item()
            train_samples += len(batch_y)

        # 验证
        model.eval()
        val_loss = 0.0
        val_return_loss = 0.0
        val_direction_loss = 0.0
        val_vol_loss = 0.0
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
                val_return_loss += return_loss.item()
                val_direction_loss += direction_loss.item()
                val_vol_loss += vol_loss.item()

                pred_direction = (outputs[:, 1] > 0.5).float()
                true_direction = batch_y[:, 1]
                val_direction_acc += (pred_direction == true_direction).sum().item()
                val_samples += len(batch_y)

        # 计算指标
        train_loss /= len(train_loader)
        val_loss /= len(val_loader)
        train_return_loss /= len(train_loader)
        train_direction_loss /= len(train_loader)
        train_vol_loss /= len(train_loader)
        val_return_loss /= len(val_loader)
        val_direction_loss /= len(val_loader)
        val_vol_loss /= len(val_loader)
        train_acc = train_direction_acc / train_samples * 100
        val_acc = val_direction_acc / val_samples * 100

        # 更新学习率
        scheduler.step(val_loss)
        current_lr = optimizer.param_groups[0]['lr']

        # 记录历史
        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_loss)
        history['train_acc'].append(train_acc)
        history['val_acc'].append(val_acc)
        history['learning_rate'].append(current_lr)

        epoch_time = (datetime.now() - epoch_start_time).total_seconds()

        # 计算预计剩余时间
        elapsed_time = (datetime.now() - start_time).total_seconds()
        avg_time_per_epoch = elapsed_time / (epoch - start_epoch + 1)
        remaining_epochs = epochs - epoch
        eta_seconds = avg_time_per_epoch * remaining_epochs

        # 打印详细进度（带时间戳和损失分解）
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"\n[{timestamp}] Epoch {epoch}/{epochs}")
        print(f"  📊 Loss: {train_loss:.4f} (return={train_return_loss:.4f}, direction={train_direction_loss:.4f}, vol={train_vol_loss:.4f})")
        print(f"  📈 Val Loss: {val_loss:.4f} (return={val_return_loss:.4f}, direction={val_direction_loss:.4f}, vol={val_vol_loss:.4f})")
        print(f"  🎯 Accuracy: Train={train_acc:.1f}% | Val={val_acc:.1f}%")
        print(f"  ⚙️  LR: {current_lr:.6f} | ⏱️  Time: {format_time(epoch_time)} | ETA: {format_time(eta_seconds)}")

        # 回调进度
        if progress_callback:
            progress_callback({
                'epoch': epoch,
                'total_epochs': epochs,
                'train_loss': train_loss,
                'val_loss': val_loss,
                'train_acc': train_acc,
                'val_acc': val_acc,
                'learning_rate': current_lr,
                'epoch_time': epoch_time,
                'best_val_loss': best_val_loss,
                'is_best': val_loss < best_val_loss,
                'message': f"Epoch {epoch}/{epochs} - val_loss: {val_loss:.4f}, val_acc: {val_acc:.1f}%"
            })

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
                        'hidden_size': hidden_size,
                        'num_layers': num_layers,
                        'dropout': dropout,
                        'forecast_horizon': forecast_horizon,
                        'seq_len': seq_len
                    },
                    'feature_mean': scaler.mean_,
                    'feature_std': scaler.scale_,
                    'training_info': {
                        'asset': asset_code,
                        'model_id': model_id,
                        'best_epoch': epoch,
                        'best_val_loss': best_val_loss,
                        'val_accuracy': val_acc,
                        'trained_at': datetime.now().isoformat()
                    }
                }
                torch.save(best_checkpoint, best_model_path)
                print(f"  ✨ New Best! Val Loss: {best_val_loss:.4f} (saved)")
        else:
            epochs_no_improve += 1

        # 定期保存checkpoint
        if epoch % checkpoint_interval == 0:
            config = {
                'asset_code': asset_code,
                'model_id': model_id,
                'epochs': epochs,
                'lr': lr,
                'input_size': input_size,
                'hidden_size': hidden_size,
                'num_layers': num_layers,
                'dropout': dropout,
                'batch_size': batch_size,
                'seq_len': seq_len,
                'forecast_horizon': forecast_horizon
            }
            ckpt_path = save_checkpoint(
                checkpoint_dir, epoch, model, optimizer, scheduler, scaler,
                best_val_loss, history, config
            )
            print(f"  💾 Checkpoint: {ckpt_path}")

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
            'hidden_size': hidden_size,
            'num_layers': num_layers,
            'dropout': dropout,
            'forecast_horizon': forecast_horizon,
            'seq_len': seq_len
        },
        'feature_mean': scaler.mean_,
        'feature_std': scaler.scale_,
        'training_info': {
            'asset': asset_code,
            'model_id': model_id,
            'epochs': epoch,
            'final_val_loss': val_loss,
            'best_val_loss': best_val_loss,
            'trained_at': datetime.now().isoformat()
        },
        'history': history
    }

    torch.save(final_checkpoint, final_model_path)

    # 保存训练历史
    history_path = f'models/{asset_code.lower()}_{model_id}_lstm_history.json'
    with open(history_path, 'w') as f:
        json.dump({
            'history': history,
            'best_val_loss': best_val_loss,
            'total_epochs': epoch,
            'asset': asset_code,
            'model_id': model_id,
            'config': {
                'hidden_size': hidden_size,
                'num_layers': num_layers,
                'dropout': dropout,
                'batch_size': batch_size,
                'seq_len': seq_len,
                'forecast_horizon': forecast_horizon
            }
        }, f, indent=2)

    total_time = datetime.now() - start_time
    print("\n" + "=" * 70)
    print("🎉 训练完成!")
    print("=" * 70)
    print(f"  📊 最佳验证损失: {best_val_loss:.4f}")
    print(f"  📈 最终验证损失: {val_loss:.4f}")
    best_epoch_display = best_checkpoint['training_info']['best_epoch'] if best_checkpoint else '-'
    print(f"  🏆 最佳轮数: {best_epoch_display}")
    print(f"  ⏱️  总耗时: {format_time(total_time.total_seconds())}")
    print(f"  💾 最佳模型: {best_model_path}")
    print(f"  💾 最终模型: {final_model_path}")
    print(f"  📜 训练历史: {history_path}")
    print(f"  📁 Checkpoints: {checkpoint_dir}/")
    print("=" * 70)

    return final_model_path, best_model_path, history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='训练LSTM价格预测模型')
    parser.add_argument('--asset', type=str, default='BTC', help='资产代码 (默认: BTC)')
    parser.add_argument('--model-id', type=str, default='default', help='模型标识符 (默认: default)')
    parser.add_argument('--epochs', type=int, default=50, help='训练轮数 (默认: 50)')
    parser.add_argument('--lr', type=float, default=0.001, help='学习率 (默认: 0.001)')
    parser.add_argument('--hidden-size', type=int, default=128, help='LSTM隐藏层大小 (默认: 128)')
    parser.add_argument('--num-layers', type=int, default=2, help='LSTM层数 (默认: 2)')
    parser.add_argument('--dropout', type=float, default=0.2, help='Dropout率 (默认: 0.2)')
    parser.add_argument('--batch-size', type=int, default=32, help='批次大小 (默认: 32)')
    parser.add_argument('--seq-len', type=int, default=60, help='输入序列长度 (默认: 60)')
    parser.add_argument('--forecast-horizon', type=int, default=7, help='预测未来天数 (默认: 7)')
    parser.add_argument('--device', type=str, default=None, help='设备 (cuda/cpu)')
    parser.add_argument('--resume', action='store_true', help='从checkpoint恢复训练')
    parser.add_argument('--checkpoint-interval', type=int, default=1,
                        help='每N轮保存一次checkpoint (默认: 1，即每轮都保存)')
    parser.add_argument('--no-best', action='store_true', help='不保存最佳模型')
    parser.add_argument('--patience', type=int, default=10,
                        help='早停耐心值，N轮未改善则停止 (默认: 10)')

    args = parser.parse_args()

    device = args.device
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    train_model(
        asset_code=args.asset,
        model_id=args.model_id,
        epochs=args.epochs,
        lr=args.lr,
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        dropout=args.dropout,
        batch_size=args.batch_size,
        seq_len=args.seq_len,
        forecast_horizon=args.forecast_horizon,
        device=torch.device(device),
        resume=args.resume,
        checkpoint_interval=args.checkpoint_interval,
        save_best=not args.no_best,
        patience=args.patience
    )
