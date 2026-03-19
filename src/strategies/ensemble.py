"""
策略组合器

组合多个策略的预测结果，提高稳定性和准确性
"""
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import PredictionStrategy, StrategyResult


class EnsembleStrategy(PredictionStrategy):
    """
    策略组合器

    组合多个策略的预测，通过加权投票生成最终信号
    """

    def __init__(self,
                 strategies: List[PredictionStrategy],
                 weights: Optional[List[float]] = None,
                 voting_method: str = 'weighted_average'):
        """
        初始化策略组合器

        Args:
            strategies: 策略列表
            weights: 各策略权重，None则等权重
            voting_method: 投票方法 ('weighted_average', 'majority', 'confidence')
        """
        super().__init__(
            name="Ensemble",
            description=f"组合策略: {', '.join([s.name for s in strategies])}"
        )
        self.strategies = strategies
        self.voting_method = voting_method

        if weights is None:
            self.weights = [1.0 / len(strategies)] * len(strategies)
        else:
            # 归一化权重
            total = sum(weights)
            self.weights = [w / total for w in weights]

        self.parameters = {
            'strategies': [s.name for s in strategies],
            'weights': self.weights,
            'voting_method': voting_method
        }

    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行组合预测
        """
        results = []
        individual_predictions = []

        # 收集各策略预测
        for strategy, weight in zip(self.strategies, self.weights):
            try:
                result = strategy.predict(df, forecast_days, **kwargs)
                results.append((result, weight))
                individual_predictions.append({
                    'strategy': strategy.name,
                    'up_probability': result.up_probability,
                    'predicted_price': result.predicted_price_mean,
                    'weight': weight
                })
            except Exception as e:
                print(f"策略 {strategy.name} 预测失败: {e}")

        if not results:
            raise ValueError("所有策略预测失败")

        current_price = results[0][0].current_price

        # 根据投票方法计算最终结果
        if self.voting_method == 'weighted_average':
            # 加权平均
            up_prob = sum(r.up_probability * w for r, w in results)
            predicted_price = sum(r.predicted_price_mean * w for r, w in results)
            ci_low = sum(r.confidence_interval_low * w for r, w in results)
            ci_high = sum(r.confidence_interval_high * w for r, w in results)

        elif self.voting_method == 'majority':
            # 多数投票
            up_votes = sum(w for r, w in results if r.up_probability > 50)
            down_votes = sum(w for r, w in results if r.up_probability < 50)

            up_prob = up_votes / (up_votes + down_votes) * 100 if (up_votes + down_votes) > 0 else 50
            predicted_price = sum(r.predicted_price_mean * w for r, w in results)
            ci_low = min(r.confidence_interval_low for r, _ in results)
            ci_high = max(r.confidence_interval_high for r, _ in results)

        elif self.voting_method == 'confidence':
            # 置信度加权：置信度高的策略权重更大
            confidences = []
            for r, w in results:
                # 用概率偏离50的程度作为置信度
                confidence = abs(r.up_probability - 50) / 50
                confidences.append(confidence)

            total_conf = sum(confidences)
            if total_conf > 0:
                adaptive_weights = [c / total_conf for c in confidences]
            else:
                adaptive_weights = [1.0 / len(results)] * len(results)

            up_prob = sum(r.up_probability * aw for r, aw in zip([r for r, _ in results], adaptive_weights))
            predicted_price = sum(r.predicted_price_mean * aw for r, aw in zip([r for r, _ in results], adaptive_weights))
            ci_low = sum(r.confidence_interval_low * aw for r, aw in zip([r for r, _ in results], adaptive_weights))
            ci_high = sum(r.confidence_interval_high * aw for r, aw in zip([r for r, _ in results], adaptive_weights))

        else:
            raise ValueError(f"未知投票方法: {self.voting_method}")

        down_prob = 100 - up_prob

        return StrategyResult(
            timestamp=datetime.now(),
            current_price=current_price,
            forecast_days=forecast_days,
            predicted_price_mean=predicted_price,
            predicted_price_median=predicted_price,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            up_probability=up_prob,
            down_probability=down_prob,
            metadata={
                'individual_predictions': individual_predictions,
                'voting_method': self.voting_method,
                'strategy': 'Ensemble'
            }
        )


class RegimeAwareStrategy(PredictionStrategy):
    """
    状态感知策略

    根据市场状态（趋势/震荡）自动选择合适的子策略
    """

    def __init__(self,
                 trend_strategy: PredictionStrategy,
                 range_strategy: PredictionStrategy,
                 adx_period: int = 14,
                 adx_threshold: float = 25):
        """
        初始化状态感知策略

        Args:
            trend_strategy: 趋势策略（用于趋势市场）
            range_strategy: 震荡策略（用于震荡市场）
            adx_period: ADX计算周期
            adx_threshold: 趋势/震荡阈值
        """
        super().__init__(
            name="RegimeAware",
            description="根据市场状态自动切换策略"
        )
        self.trend_strategy = trend_strategy
        self.range_strategy = range_strategy
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.parameters = {
            'trend_strategy': trend_strategy.name,
            'range_strategy': range_strategy.name,
            'adx_threshold': adx_threshold
        }

    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行状态感知预测
        """
        close = df['close_price']
        high = df.get('max_price', df.get('high_price', close))
        low = df.get('min_price', df.get('low_price', close))

        # 计算ADX判断趋势强度
        adx = self._calculate_adx(close, high, low)

        # 判断市场状态
        if adx > self.adx_threshold:
            regime = 'trend'
            active_strategy = self.trend_strategy
        else:
            regime = 'range'
            active_strategy = self.range_strategy

        # 使用活跃策略预测
        result = active_strategy.predict(df, forecast_days, **kwargs)

        # 添加状态信息
        result.metadata['market_regime'] = regime
        result.metadata['adx'] = adx
        result.metadata['active_strategy'] = active_strategy.name

        return result

    def _calculate_adx(self, close: pd.Series, high: pd.Series, low: pd.Series) -> float:
        """计算ADX指标"""
        if len(close) < self.adx_period * 2:
            return 50  # 默认中等趋势强度

        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        dm_plus = high.diff()
        dm_minus = -low.diff()

        dm_plus = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0)
        dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0)

        # Smoothed averages
        atr = tr.rolling(self.adx_period).mean()
        di_plus = 100 * dm_plus.rolling(self.adx_period).mean() / atr
        di_minus = 100 * dm_minus.rolling(self.adx_period).mean() / atr

        # DX and ADX
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus)
        adx = dx.rolling(self.adx_period).mean()

        return adx.iloc[-1] if not adx.empty else 25
