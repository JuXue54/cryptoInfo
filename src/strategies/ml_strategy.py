"""
机器学习策略基类

提供深度学习预测策略的基础框架，包括：
- 特征工程（20+技术指标和统计特征）
- 模型加载和推理
- GPU/CPU自动切换
"""
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from datetime import datetime
from typing import Dict, Any, Optional, List
from abc import abstractmethod

from .base import PredictionStrategy, StrategyResult


class FeatureExtractor:
    """特征提取器 - 从价格数据提取ML特征"""

    # 特征名称列表
    FEATURE_NAMES = [
        # 价格特征
        'log_return', 'log_return_1d', 'log_return_3d', 'log_return_7d',
        'price_deviation_ma7', 'price_deviation_ma30', 'price_deviation_ma90',
        'volatility_7d', 'volatility_30d',

        # 技术指标
        'rsi_6', 'rsi_14', 'rsi_24',
        'macd', 'macd_signal', 'macd_histogram',
        'bb_width', 'bb_position',
        'adx',

        # 趋势特征
        'trend_strength', 'trend_direction',

        # 波动率
        'atr_14', 'atr_ratio',

        # 动量
        'momentum_7', 'momentum_14', 'momentum_30',

        # 时间特征
        'day_of_week', 'is_weekend', 'month',

        # 市场结构
        'high_20d_ratio', 'low_20d_ratio',

        # 交易量特征
        'volume',
        'volume_ma7', 'volume_ma30',
        'volume_change',
        'volume_relative',
    ]

    @classmethod
    def extract_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        从价格数据提取特征

        Args:
            df: 包含close_price, open_price, high_price, low_price的DataFrame

        Returns:
            DataFrame: 特征矩阵 [n_samples, n_features]
        """
        features = pd.DataFrame(index=df.index)

        close = df['close_price']
        high = df.get('max_price', close)
        low = df.get('min_price', close)
        open_p = df.get('open_price', close)

        # ========== 价格特征 ==========
        # 对数收益率
        features['log_return'] = np.log(close / close.shift(1))
        features['log_return_1d'] = features['log_return']
        features['log_return_3d'] = np.log(close / close.shift(3))
        features['log_return_7d'] = np.log(close / close.shift(7))

        # 价格相对于均线的偏离
        ma7 = close.rolling(window=7).mean()
        ma30 = close.rolling(window=30).mean()
        ma90 = close.rolling(window=90).mean()

        features['price_deviation_ma7'] = (close - ma7) / ma7 * 100
        features['price_deviation_ma30'] = (close - ma30) / ma30 * 100
        features['price_deviation_ma90'] = (close - ma90) / ma90 * 100

        # 波动率
        features['volatility_7d'] = features['log_return'].rolling(7).std() * np.sqrt(365)
        features['volatility_30d'] = features['log_return'].rolling(30).std() * np.sqrt(365)

        # ========== 技术指标 ==========
        # RSI (6, 14, 24)
        for period in [6, 14, 24]:
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / (loss + 1e-10)
            features[f'rsi_{period}'] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        features['macd'] = ema12 - ema26
        features['macd_signal'] = features['macd'].ewm(span=9, adjust=False).mean()
        features['macd_histogram'] = features['macd'] - features['macd_signal']

        # 布林带
        bb_ma = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        features['bb_width'] = (bb_std * 2) / bb_ma * 100
        features['bb_position'] = (close - (bb_ma - bb_std * 2)) / (bb_std * 4 + 1e-10)

        # ADX (简化版)
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()

        plus_dm = (high - high.shift(1)).clip(lower=0)
        minus_dm = (low.shift(1) - low).clip(lower=0)

        plus_di = 100 * plus_dm.rolling(14).mean() / atr
        minus_di = 100 * minus_dm.rolling(14).mean() / atr
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
        features['adx'] = dx.rolling(14).mean()

        # ========== 趋势特征 ==========
        features['trend_strength'] = abs(close - ma30) / (close.rolling(30).std() + 1e-10)
        features['trend_direction'] = np.where(close > ma30, 1, -1).astype(float)

        # ========== 波动率特征 ==========
        features['atr_14'] = atr
        features['atr_ratio'] = atr / close * 100

        # ========== 动量特征 ==========
        for period in [7, 14, 30]:
            features[f'momentum_{period}'] = (close / close.shift(period) - 1) * 100

        # ========== 时间特征 ==========
        features['day_of_week'] = df.index.dayofweek.astype(float)
        features['is_weekend'] = (df.index.dayofweek >= 5).astype(float)
        features['month'] = df.index.month.astype(float)

        # ========== 市场结构 ==========
        high_20 = high.rolling(20).max()
        low_20 = low.rolling(20).min()
        features['high_20d_ratio'] = close / high_20
        features['low_20d_ratio'] = close / low_20

        # ========== 交易量特征 ==========
        if 'volume' in df.columns:
            volume = df['volume']
            features['volume'] = volume

            # 交易量移动平均
            features['volume_ma7'] = volume.rolling(window=7).mean()
            features['volume_ma30'] = volume.rolling(window=30).mean()

            # 交易量变化率
            features['volume_change'] = volume.pct_change() * 100

            # 相对交易量（当前交易量/近期平均）
            features['volume_relative'] = volume / (features['volume_ma7'] + 1e-10)
        else:
            # 如果没有交易量数据，填充0
            features['volume'] = 0
            features['volume_ma7'] = 0
            features['volume_ma30'] = 0
            features['volume_change'] = 0
            features['volume_relative'] = 0

        return features

    @classmethod
    def get_feature_names(cls) -> List[str]:
        return cls.FEATURE_NAMES


class LSTMPredictor(nn.Module):
    """LSTM价格预测模型"""

    def __init__(self, input_size: int, hidden_size: int = 128,
                 num_layers: int = 2, dropout: float = 0.2,
                 forecast_horizon: int = 7):
        super().__init__()

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.forecast_horizon = forecast_horizon

        # LSTM层
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0,
            batch_first=True,
            bidirectional=False
        )

        # 注意力机制
        self.attention = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.Tanh(),
            nn.Linear(hidden_size // 2, 1)
        )

        # 输出层
        # 输出: [价格变化率, 上涨概率, 波动率估计]
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, 3)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        前向传播

        Args:
            x: [batch_size, seq_len, input_size]

        Returns:
            [batch_size, 3] - [price_return_pred, up_prob, volatility_pred]
        """
        # LSTM编码
        lstm_out, _ = self.lstm(x)  # [batch, seq_len, hidden]

        # 注意力权重
        attention_weights = torch.softmax(self.attention(lstm_out), dim=1)
        context = torch.sum(attention_weights * lstm_out, dim=1)  # [batch, hidden]

        # 输出
        output = self.fc(context)

        # 应用激活函数
        price_return = output[:, 0]  # 线性输出，表示收益率
        up_prob = torch.sigmoid(output[:, 1])  # 上涨概率
        volatility = torch.sigmoid(output[:, 2]) * 0.5  # 波动率估计，限制在0-50%

        return torch.stack([price_return, up_prob, volatility], dim=1)


class MLStrategyBase(PredictionStrategy):
    """
    机器学习策略基类

    支持PyTorch模型加载、GPU加速、特征工程
    """

    def __init__(self, name: str, description: str,
                 model_path: Optional[str] = None,
                 asset_code: Optional[str] = None,
                 model_id: Optional[str] = None,
                 seq_len: int = 60):
        super().__init__(name, description)

        # 设备选择
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.seq_len = seq_len

        # 特征标准化参数
        self.feature_mean = None
        self.feature_std = None

        # 模型
        self.model = None
        if model_path or asset_code:
            self.load_model(model_path=model_path, asset_code=asset_code, model_id=model_id)
        else:
            # 初始化默认模型（未训练）
            self._init_default_model()

    def _init_default_model(self):
        """初始化默认模型（用于测试或未训练状态）"""
        n_features = len(FeatureExtractor.get_feature_names())
        self.model = LSTMPredictor(
            input_size=n_features,
            hidden_size=128,
            num_layers=2,
            dropout=0.2
        ).to(self.device)
        self.model.eval()

        # 默认标准化参数
        self.feature_mean = np.zeros(n_features)
        self.feature_std = np.ones(n_features)

    @staticmethod
    def find_best_model(asset_code: str, model_id: str = None) -> Optional[str]:
        """
        查找指定资产的最佳模型路径

        Args:
            asset_code: 资产代码 (如 'BTC', 'ETH')
            model_id: 可选的模型标识符，如果为None则查找所有模型中loss最小的

        Returns:
            最佳模型的文件路径，如果未找到则返回None
        """
        import glob
        import os

        asset_lower = asset_code.lower()

        if model_id:
            # 查找指定model_id的best模型
            specific_path = f'models/{asset_lower}_{model_id}_lstm_best.pth'
            if os.path.exists(specific_path):
                return specific_path
            return None
        else:
            # 查找该资产所有模型的best模型
            pattern = f'models/{asset_lower}_*_lstm_best.pth'
            best_models = glob.glob(pattern)

            if not best_models:
                return None

            # 比较所有模型的best_val_loss，返回loss最小的
            best_path = None
            best_loss = float('inf')

            for model_path in best_models:
                try:
                    checkpoint = torch.load(model_path, map_location='cpu')
                    training_info = checkpoint.get('training_info', {})
                    val_loss = training_info.get('best_val_loss', float('inf'))

                    if val_loss < best_loss:
                        best_loss = val_loss
                        best_path = model_path
                except Exception:
                    continue

            return best_path

    def load_model(self, model_path: str = None, asset_code: str = None, model_id: str = None):
        """
        加载预训练模型

        Args:
            model_path: 直接指定模型文件路径（优先级最高）
            asset_code: 资产代码，用于自动查找最佳模型
            model_id: 模型标识符，用于指定特定模型
        """
        try:
            # 如果未指定路径，尝试自动查找
            if model_path is None and asset_code:
                model_path = self.find_best_model(asset_code, model_id)
                if model_path:
                    print(f"✓ 自动加载最佳模型: {model_path}")

            if model_path is None or not os.path.exists(model_path):
                print(f"⚠ 未找到预训练模型，使用默认未训练模型")
                self._init_default_model()
                return

            checkpoint = torch.load(model_path, map_location=self.device)

            # 初始化模型
            model_config = checkpoint.get('config', {})
            n_features = model_config.get('input_size', len(FeatureExtractor.get_feature_names()))

            self.model = LSTMPredictor(
                input_size=n_features,
                hidden_size=model_config.get('hidden_size', 128),
                num_layers=model_config.get('num_layers', 2),
                dropout=model_config.get('dropout', 0.2),
                forecast_horizon=model_config.get('forecast_horizon', 7)
            ).to(self.device)

            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.model.eval()

            # 加载标准化参数
            self.feature_mean = checkpoint.get('feature_mean', np.zeros(n_features))
            self.feature_std = checkpoint.get('feature_std', np.ones(n_features))

            # 更新策略参数
            training_info = checkpoint.get('training_info', {})
            self.parameters.update({
                'model_loaded': True,
                'model_path': model_path,
                'device': str(self.device),
                'best_val_loss': training_info.get('best_val_loss'),
                'best_epoch': training_info.get('best_epoch'),
                'model_config': model_config
            })

        except Exception as e:
            print(f"⚠ 加载模型失败: {e}，使用默认未训练模型")
            self._init_default_model()

    def save_model(self, model_path: str, config: Dict = None):
        """保存模型"""
        checkpoint = {
            'model_state_dict': self.model.state_dict(),
            'feature_mean': self.feature_mean,
            'feature_std': self.feature_std,
            'config': config or {}
        }
        torch.save(checkpoint, model_path)

    def _normalize_features(self, features: np.ndarray) -> np.ndarray:
        """标准化特征"""
        return (features - self.feature_mean) / (self.feature_std + 1e-8)

    def predict(self, df: pd.DataFrame, forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行预测

        Args:
            df: 历史价格数据
            forecast_days: 预测天数

        Returns:
            StrategyResult: 预测结果
        """
        if not self.validate_data(df):
            raise ValueError("Invalid input data")

        # 提取特征
        features_df = FeatureExtractor.extract_features(df)
        features_df = features_df.fillna(0)  # 填充缺失值

        # 获取最近seq_len个时间步
        if len(features_df) < self.seq_len:
            # 填充不足的数据
            pad_len = self.seq_len - len(features_df)
            padding = pd.DataFrame(0, index=range(pad_len), columns=features_df.columns)
            features_df = pd.concat([padding, features_df], ignore_index=True)

        features = features_df[FeatureExtractor.FEATURE_NAMES].tail(self.seq_len).values

        # 标准化
        features_norm = self._normalize_features(features)

        # 转为tensor
        x = torch.FloatTensor(features_norm).unsqueeze(0).to(self.device)

        # 推理
        with torch.no_grad():
            with torch.amp.autocast(device_type=self.device.type, enabled=self.device.type == 'cuda'):
                output = self.model(x)

        # 解析输出
        pred_return = output[0, 0].cpu().item()  # 预测收益率
        up_prob = output[0, 1].cpu().item()  # 上涨概率
        volatility = output[0, 2].cpu().item()  # 预测波动率

        # 计算预测价格
        current_price = df['close_price'].iloc[-1]
        pred_price = current_price * (1 + pred_return)

        # 计算置信区间（基于预测波动率）
        confidence_mult = 1.96  # 95%置信区间
        price_std = current_price * volatility * np.sqrt(forecast_days / 365)
        ci_low = pred_price - confidence_mult * price_std
        ci_high = pred_price + confidence_mult * price_std

        # 确保价格在合理范围内
        ci_low = max(ci_low, pred_price * 0.5)
        ci_high = max(ci_high, pred_price * 1.5)

        # 技术指标
        indicators = self.calculate_technical_indicators(df)

        return StrategyResult(
            timestamp=datetime.now(),
            current_price=current_price,
            forecast_days=forecast_days,
            predicted_price_mean=pred_price,
            predicted_price_median=pred_price,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            up_probability=up_prob * 100,
            down_probability=(1 - up_prob) * 100,
            metadata={
                'strategy': self.name,
                'device': str(self.device),
                'predicted_return': pred_return * 100,  # 百分比
                'predicted_volatility': volatility * 100,
                'indicators': indicators,
                'model_type': 'LSTM'
            }
        )

    def get_feature_importance(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        计算特征重要性（基于注意力权重）

        Returns:
            Dict: 各特征的重要性分数
        """
        if not self.model:
            return {}

        features_df = FeatureExtractor.extract_features(df)
        features_df = features_df.fillna(0)
        features = features_df[FeatureExtractor.FEATURE_NAMES].tail(self.seq_len).values
        features_norm = self._normalize_features(features)
        x = torch.FloatTensor(features_norm).unsqueeze(0).to(self.device)

        with torch.no_grad():
            # 获取LSTM输出
            lstm_out, _ = self.model.lstm(x)
            # 获取注意力权重
            attention_weights = torch.softmax(self.model.attention(lstm_out), dim=1)

        weights = attention_weights.squeeze().cpu().numpy()

        # 按特征维度汇总
        importance = np.mean(weights, axis=0)

        return {
            name: float(imp)
            for name, imp in zip(FeatureExtractor.FEATURE_NAMES, importance)
        }


class LSTMStrategy(MLStrategyBase):
    """LSTM预测策略 - 可直接使用"""

    def __init__(self, model_path: Optional[str] = None,
                 seq_len: int = 60,
                 hidden_size: int = 128,
                 num_layers: int = 2):
        super().__init__(
            name="LSTM",
            description="基于LSTM深度学习的BTC价格预测策略，使用20+技术指标特征",
            model_path=model_path,
            seq_len=seq_len
        )

        self.parameters.update({
            'seq_len': seq_len,
            'hidden_size': hidden_size,
            'num_layers': num_layers,
            'features': len(FeatureExtractor.get_feature_names())
        })
