"""
均值回归策略

基于RSI、布林带和Z-Score的均值回归交易策略
适合震荡市场
"""
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional

from .base import PredictionStrategy, StrategyResult


class MeanReversionStrategy(PredictionStrategy):
    """
    均值回归策略

    核心理念：价格会回归均值，在超买时做空，超卖时做多
    """

    def __init__(self,
                 rsi_period: int = 14,
                 rsi_overbought: float = 70,
                 rsi_oversold: float = 30,
                 bb_period: int = 20,
                 bb_std: float = 2.0,
                 lookback: int = 30):
        """
        初始化均值回归策略

        Args:
            rsi_period: RSI计算周期
            rsi_overbought: RSI超买阈值
            rsi_oversold: RSI超卖阈值
            bb_period: 布林带周期
            bb_std: 布林带标准差倍数
            lookback: 回看期
        """
        super().__init__(
            name="MeanReversion",
            description="基于RSI和布林带的均值回归策略"
        )
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.lookback = lookback
        self.parameters = {
            'rsi_period': rsi_period,
            'rsi_overbought': rsi_overbought,
            'rsi_oversold': rsi_oversold,
            'bb_period': bb_period,
            'bb_std': bb_std,
            'lookback': lookback
        }

    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行均值回归分析
        """
        if not self.validate_data(df):
            raise ValueError("Invalid input data")

        close = df['close_price']
        current_price = close.iloc[-1]

        # 计算RSI
        rsi = self._calculate_rsi(close)

        # 计算布林带
        bb_upper, bb_middle, bb_lower, bb_width = self._calculate_bollinger_bands(close)

        # 计算Z-Score
        z_score = self._calculate_z_score(close)

        # 计算价格位置 (%B)
        percent_b = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5

        # 综合判断超买超卖
        oversold_score = 0
        overbought_score = 0

        # RSI信号
        if rsi < self.rsi_oversold:
            oversold_score += (self.rsi_oversold - rsi) / self.rsi_oversold * 40
        elif rsi > self.rsi_overbought:
            overbought_score += (rsi - self.rsi_overbought) / (100 - self.rsi_overbought) * 40

        # 布林带信号
        if percent_b < 0.1:
            oversold_score += 30
        elif percent_b > 0.9:
            overbought_score += 30

        # Z-Score信号
        if z_score < -2:
            oversold_score += 30
        elif z_score > 2:
            overbought_score += 30

        # 判断趋势状态（均值回归只在震荡市有效）
        trend_strength = self._calculate_trend_strength(close)

        # 如果是强趋势市，降低信号强度
        if trend_strength > 70:
            oversold_score *= 0.3
            overbought_score *= 0.3

        # 转换为概率
        if oversold_score > overbought_score:
            up_probability = 50 + oversold_score
        else:
            up_probability = 50 - overbought_score

        up_probability = max(10, min(90, up_probability))
        down_probability = 100 - up_probability

        # 预测价格（向均线回归）
        if oversold_score > 20:
            target = bb_middle
            predicted_price = current_price + (target - current_price) * min(1, forecast_days / 7)
        elif overbought_score > 20:
            target = bb_middle
            predicted_price = current_price + (target - current_price) * min(1, forecast_days / 7)
        else:
            predicted_price = current_price * 1.001  # 小幅上涨

        # 置信区间
        volatility = close.pct_change().std() * np.sqrt(forecast_days)
        ci_low = predicted_price * (1 - volatility * 1.5)
        ci_high = predicted_price * (1 + volatility * 1.5)

        indicators = {
            'RSI': rsi,
            'BB_Upper': bb_upper,
            'BB_Middle': bb_middle,
            'BB_Lower': bb_lower,
            'BB_Width': bb_width,
            'Z_Score': z_score,
            'Percent_B': percent_b,
            'Oversold_Score': oversold_score,
            'Overbought_Score': overbought_score,
            'Trend_Strength': trend_strength
        }

        return StrategyResult(
            timestamp=datetime.now(),
            current_price=current_price,
            forecast_days=forecast_days,
            predicted_price_mean=predicted_price,
            predicted_price_median=predicted_price,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            up_probability=up_probability,
            down_probability=down_probability,
            metadata={
                'indicators': indicators,
                'oversold_score': oversold_score,
                'overbought_score': overbought_score,
                'strategy': 'MeanReversion'
            }
        )

    def _calculate_rsi(self, close: pd.Series) -> float:
        """计算RSI"""
        if len(close) < self.rsi_period + 1:
            return 50

        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not rsi.empty else 50

    def _calculate_bollinger_bands(self, close: pd.Series) -> tuple:
        """计算布林带"""
        if len(close) < self.bb_period:
            middle = close.mean()
            std = close.std()
            return middle + 2*std, middle, middle - 2*std, 0

        middle = close.rolling(self.bb_period).mean().iloc[-1]
        std = close.rolling(self.bb_period).std().iloc[-1]

        upper = middle + self.bb_std * std
        lower = middle - self.bb_std * std
        width = (upper - lower) / middle if middle > 0 else 0

        return upper, middle, lower, width

    def _calculate_z_score(self, close: pd.Series) -> float:
        """计算Z-Score"""
        if len(close) < self.lookback:
            return 0

        recent = close.tail(self.lookback)
        mean = recent.mean()
        std = recent.std()

        if std == 0:
            return 0

        return (close.iloc[-1] - mean) / std

    def _calculate_trend_strength(self, close: pd.Series) -> float:
        """计算趋势强度 (0-100)"""
        if len(close) < 30:
            return 50

        # 使用线性回归斜率
        x = np.arange(min(30, len(close)))
        y = close.tail(30).values

        slope = np.polyfit(x, y, 1)[0]
        normalized_slope = abs(slope) / close.iloc[-1] * 100

        # 归一化到0-100
        return min(100, normalized_slope * 10)
