"""
策略基类模块

定义预测策略的接口和基础数据结构
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from datetime import datetime


@dataclass
class StrategyResult:
    """策略预测结果数据类"""
    # 基本信息
    timestamp: datetime
    current_price: float
    forecast_days: int

    # 预测结果
    predicted_price_mean: float
    predicted_price_median: float
    confidence_interval_low: float  # 例如5%分位数
    confidence_interval_high: float  # 例如95%分位数

    # 概率
    up_probability: float  # 0-100
    down_probability: float  # 0-100

    # 额外数据（用于可视化等）
    metadata: Dict[str, Any] = None
    simulation_paths: Optional[List[List[float]]] = None  # 模拟路径，用于可视化

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @property
    def expected_return(self) -> float:
        """预期收益率"""
        return (self.predicted_price_mean - self.current_price) / self.current_price * 100

    @property
    def risk_reward_ratio(self) -> float:
        """风险收益比"""
        upside = self.predicted_price_mean - self.current_price
        downside = self.current_price - self.confidence_interval_low
        if downside <= 0:
            return float('inf') if upside > 0 else 0
        return upside / downside


@dataclass
class BacktestResult:
    """回测结果数据类"""
    # 回测配置
    strategy_name: str
    start_date: str
    end_date: str
    forecast_days: int
    total_predictions: int

    # 准确性指标
    direction_accuracy: float  # 方向预测准确率（涨跌）
    direction_accuracy_up: float  # 上涨预测的准确率
    direction_accuracy_down: float  # 下跌预测的准确率

    # 误差指标
    mae: float  # 平均绝对误差
    rmse: float  # 均方根误差
    mape: float  # 平均绝对百分比误差

    # 概率校准度
    probability_calibration: float  # 概率预测与实际发生的比率

    # 交易模拟结果（策略）
    trading_return: float  # 模拟交易总收益率 (%)
    trading_annual_return: float  # 策略年化收益率 (%)
    trading_sharpe: float  # 夏普比率
    max_drawdown: float  # 最大回撤 (%)
    annual_volatility: float  # 年化波动率 (%)
    var_95: float  # 风险价值VaR 95% (%)
    profit_loss_ratio: float  # 盈亏比
    win_rate: float  # 胜率 (%)
    total_trades: int  # 总交易次数

    # 买入持有策略对比（基准）
    buy_hold_return: float  # 买入持有总收益率 (%)
    buy_hold_annual_return: float  # 买入持有年化收益率 (%)
    buy_hold_max_drawdown: float  # 买入持有最大回撤 (%)
    buy_hold_volatility: float  # 买入持有年化波动率 (%)
    buy_hold_sharpe: float  # 买入持有夏普比率
    period_days: int  # 回测期间天数

    # 详细数据
    predictions: List[Dict] = None  # 每个预测点的详细结果
    position_history: List[Dict] = None  # 仓位历史记录
    trades: List[Any] = None  # 交易记录

    def __post_init__(self):
        if self.predictions is None:
            self.predictions = []
        if self.position_history is None:
            self.position_history = []
        if self.trades is None:
            self.trades = []


class PredictionStrategy(ABC):
    """
    预测策略基类

    所有预测策略必须继承此类并实现predict方法
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.parameters = {}

    @abstractmethod
    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行预测

        Args:
            df: 历史价格数据DataFrame，包含OHLC列
            forecast_days: 预测天数
            **kwargs: 策略特定参数

        Returns:
            StrategyResult: 预测结果
        """
        pass

    def get_parameters(self) -> Dict[str, Any]:
        """获取策略参数"""
        return self.parameters.copy()

    def set_parameters(self, **kwargs):
        """设置策略参数"""
        self.parameters.update(kwargs)

    def validate_data(self, df: pd.DataFrame) -> bool:
        """
        验证输入数据是否有效

        Args:
            df: 输入数据

        Returns:
            bool: 数据是否有效
        """
        if df is None or df.empty:
            return False

        # 至少需要有 close_price 列
        if 'close_price' not in df.columns:
            return False

        # 检查 close_price 是否有有效数据
        if df['close_price'].isna().all():
            return False

        return True

    def calculate_technical_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        计算通用技术指标

        Args:
            df: 价格数据

        Returns:
            Dict: 技术指标字典
        """
        close = df['close_price']

        signals = {}

        # 移动平均线
        signals['MA7'] = close.rolling(window=7).mean().iloc[-1] if len(close) >= 7 else None
        signals['MA30'] = close.rolling(window=30).mean().iloc[-1] if len(close) >= 30 else None

        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        # 避免除以零
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        signals['RSI'] = rsi.iloc[-1] if not rsi.empty and not pd.isna(rsi.iloc[-1]) else 50

        # 波动率
        returns = close.pct_change().dropna()
        signals['volatility'] = returns.std() * (365 ** 0.5) if len(returns) > 0 else 0

        # 趋势
        if signals['MA7'] is not None and signals['MA30'] is not None:
            signals['trend'] = 'bullish' if signals['MA7'] > signals['MA30'] else 'bearish'
        else:
            signals['trend'] = 'neutral'

        return signals
