"""
趋势跟踪策略

基于动量和趋势指标的交易策略，不预测价格而是跟随趋势
"""
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional

from .base import PredictionStrategy, StrategyResult


class TrendFollowingStrategy(PredictionStrategy):
    """
    趋势跟踪策略

    结合多时间框架动量、波动率和趋势强度来生成信号
    核心理念：让利润奔跑，及时止损
    """

    def __init__(self,
                 short_window: int = 7,
                 medium_window: int = 30,
                 long_window: int = 90,
                 volatility_lookback: int = 30):
        """
        初始化趋势跟踪策略

        Args:
            short_window: 短期窗口（默认7天）
            medium_window: 中期窗口（默认30天）
            long_window: 长期窗口（默认90天）
            volatility_lookback: 波动率计算回看期
        """
        super().__init__(
            name="TrendFollowing",
            description="多时间框架趋势跟踪策略"
        )
        self.short_window = short_window
        self.medium_window = medium_window
        self.long_window = long_window
        self.volatility_lookback = volatility_lookback
        self.parameters = {
            'short_window': short_window,
            'medium_window': medium_window,
            'long_window': long_window,
            'volatility_lookback': volatility_lookback
        }

    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行趋势分析并生成预测

        Args:
            df: 历史价格数据
            forecast_days: 预测天数
            **kwargs: 额外参数

        Returns:
            StrategyResult: 预测结果
        """
        if not self.validate_data(df):
            raise ValueError("Invalid input data")

        close = df['close_price']
        high = df.get('max_price', df.get('high_price', close))
        low = df.get('min_price', df.get('low_price', close))

        current_price = close.iloc[-1]

        # 计算多时间框架趋势得分 (0-100)
        trend_score = self._calculate_trend_score(close)

        # 计算动量得分
        momentum_score = self._calculate_momentum_score(close)

        # 计算趋势强度 (ADX-like)
        trend_strength = self._calculate_trend_strength(close, high, low)

        # 计算波动率状态
        volatility_regime = self._calculate_volatility_regime(close)

        # 综合得分 -> 上涨概率
        # 趋势跟踪策略：只在强趋势时高置信度
        if trend_score > 60 and momentum_score > 50:
            # 强势上涨
            up_probability = 50 + (trend_score - 50) * (trend_strength / 100)
        elif trend_score < 40 and momentum_score < 50:
            # 强势下跌
            up_probability = 50 - (50 - trend_score) * (trend_strength / 100)
        else:
            # 震荡或趋势不明
            up_probability = 50

        up_probability = max(10, min(90, up_probability))  # 限制在10-90%
        down_probability = 100 - up_probability

        # 基于趋势预测未来价格
        # 使用历史趋势延续性的统计估计
        predicted_return = self._estimate_future_return(
            close, trend_score, momentum_score, forecast_days
        )
        predicted_price = current_price * (1 + predicted_return)

        # 置信区间基于波动率
        volatility = close.pct_change().std() * np.sqrt(365)
        confidence_width = volatility * np.sqrt(forecast_days / 365) * 1.96  # 95% CI

        ci_low = current_price * (1 + predicted_return - confidence_width)
        ci_high = current_price * (1 + predicted_return + confidence_width)

        indicators = {
            'trend_score': trend_score,
            'momentum_score': momentum_score,
            'trend_strength': trend_strength,
            'volatility_regime': volatility_regime,
            'short_ma': close.rolling(self.short_window).mean().iloc[-1],
            'medium_ma': close.rolling(self.medium_window).mean().iloc[-1],
            'long_ma': close.rolling(self.long_window).mean().iloc[-1],
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
                'trend_score': trend_score,
                'strategy': 'TrendFollowing'
            }
        )

    def _calculate_trend_score(self, close: pd.Series) -> float:
        """
        计算趋势得分 (0-100)

        基于多均线排列和价格相对位置
        """
        if len(close) < self.long_window:
            return 50

        ma_short = close.rolling(self.short_window).mean().iloc[-1]
        ma_medium = close.rolling(self.medium_window).mean().iloc[-1]
        ma_long = close.rolling(self.long_window).mean().iloc[-1]

        current = close.iloc[-1]

        # 均线排列得分
        score = 50

        # 价格在长期均线之上/之下
        if current > ma_long:
            score += 20
        else:
            score -= 20

        # 短期均线在中期均线之上/之下
        if ma_short > ma_medium:
            score += 15
        else:
            score -= 15

        # 中期均线在长期均线之上/之下
        if ma_medium > ma_long:
            score += 15
        else:
            score -= 15

        # 价格相对短期均线
        if current > ma_short:
            score += 10
        else:
            score -= 10

        return max(0, min(100, score))

    def _calculate_momentum_score(self, close: pd.Series) -> float:
        """
        计算动量得分 (0-100)

        基于不同时间段的收益率
        """
        scores = []

        # 短期动量
        if len(close) >= 8:
            ret_7d = (close.iloc[-1] / close.iloc[-8] - 1) * 100
            if ret_7d > 10:
                scores.append(80)
            elif ret_7d > 5:
                scores.append(65)
            elif ret_7d > 0:
                scores.append(55)
            elif ret_7d > -5:
                scores.append(45)
            else:
                scores.append(30)

        # 中期动量
        if len(close) >= 31:
            ret_30d = (close.iloc[-1] / close.iloc[-31] - 1) * 100
            if ret_30d > 20:
                scores.append(80)
            elif ret_30d > 10:
                scores.append(65)
            elif ret_30d > 0:
                scores.append(55)
            elif ret_30d > -10:
                scores.append(40)
            else:
                scores.append(25)

        # 长期动量
        if len(close) >= 91:
            ret_90d = (close.iloc[-1] / close.iloc[-91] - 1) * 100
            if ret_90d > 50:
                scores.append(80)
            elif ret_90d > 20:
                scores.append(65)
            elif ret_90d > 0:
                scores.append(55)
            else:
                scores.append(35)

        return np.mean(scores) if scores else 50

    def _calculate_trend_strength(self, close: pd.Series, high: pd.Series, low: pd.Series) -> float:
        """
        计算趋势强度 (0-100)

        类似于ADX指标
        """
        if len(close) < 14:
            return 50

        # 计算DM+和DM-
        high_diff = high.diff()
        low_diff = -low.diff()

        dm_plus = ((high_diff > low_diff) & (high_diff > 0)) * high_diff
        dm_minus = ((low_diff > high_diff) & (low_diff > 0)) * low_diff

        # 计算ATR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # 计算DI+和DI-
        di_plus = 100 * dm_plus.rolling(14).mean() / atr
        di_minus = 100 * dm_minus.rolling(14).mean() / atr

        # DX
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus)

        # 归一化到0-100
        current_dx = dx.iloc[-1] if not dx.empty else 50
        return max(0, min(100, current_dx))

    def _calculate_volatility_regime(self, close: pd.Series) -> str:
        """
        判断波动率状态
        """
        if len(close) < self.volatility_lookback * 2:
            return 'normal'

        returns = close.pct_change().dropna()
        current_vol = returns.tail(self.volatility_lookback).std() * np.sqrt(365)
        historical_vol = returns.std() * np.sqrt(365)

        if current_vol > historical_vol * 1.5:
            return 'high'
        elif current_vol < historical_vol * 0.7:
            return 'low'
        return 'normal'

    def _estimate_future_return(self, close: pd.Series, trend_score: float,
                                momentum_score: float, forecast_days: int) -> float:
        """
        估计未来收益率
        """
        # 基于历史趋势延续性
        # 强趋势时假设趋势延续，震荡市回归均值

        if trend_score > 70 and momentum_score > 60:
            # 强上涨趋势，假设延续
            recent_return = (close.iloc[-1] / close.iloc[-min(30, len(close))] - 1)
            return recent_return * (forecast_days / 30) * 0.5

        elif trend_score < 30 and momentum_score < 40:
            # 强下跌趋势
            recent_return = (close.iloc[-1] / close.iloc[-min(30, len(close))] - 1)
            return recent_return * (forecast_days / 30) * 0.5

        # 震荡市，回归近期均值
        ma = close.rolling(30).mean().iloc[-1]
        mean_reversion = (ma / close.iloc[-1] - 1) * 0.3
        return mean_reversion * (forecast_days / 30)
