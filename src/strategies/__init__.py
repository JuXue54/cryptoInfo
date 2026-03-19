"""
策略模块

支持多种预测策略，包括：
- MonteCarloStrategy: 蒙特卡洛模拟策略
- TrendFollowingStrategy: 趋势跟踪策略
- MeanReversionStrategy: 均值回归策略
- EnsembleStrategy: 策略组合器
- RegimeAwareStrategy: 状态感知策略
"""
from .base import PredictionStrategy, StrategyResult
from .monte_carlo import MonteCarloStrategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy
from .ensemble import EnsembleStrategy, RegimeAwareStrategy

__all__ = [
    'PredictionStrategy', 'StrategyResult',
    'MonteCarloStrategy',
    'TrendFollowingStrategy',
    'MeanReversionStrategy',
    'EnsembleStrategy',
    'RegimeAwareStrategy'
]
