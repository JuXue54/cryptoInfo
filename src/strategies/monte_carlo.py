"""
蒙特卡洛模拟预测策略
"""
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional

from .base import PredictionStrategy, StrategyResult


class MonteCarloStrategy(PredictionStrategy):
    """
    蒙特卡洛模拟预测策略

    基于历史收益率的均值和方差，使用几何布朗运动模拟未来价格路径
    可根据技术指标调整漂移率
    """

    def __init__(self,
                 n_simulations: int = 10000,
                 use_trend_adjustment: bool = True,
                 confidence_level: float = 0.90):
        """
        初始化蒙特卡洛策略

        Args:
            n_simulations: 模拟次数
            use_trend_adjustment: 是否使用趋势调整
            confidence_level: 置信水平（默认90%）
        """
        super().__init__(
            name="MonteCarlo",
            description="基于历史波动率的蒙特卡洛模拟预测"
        )
        self.n_simulations = n_simulations
        self.use_trend_adjustment = use_trend_adjustment
        self.confidence_level = confidence_level
        self.parameters = {
            'n_simulations': n_simulations,
            'use_trend_adjustment': use_trend_adjustment,
            'confidence_level': confidence_level
        }

    def predict(self,
                df: pd.DataFrame,
                forecast_days: int = 7,
                **kwargs) -> StrategyResult:
        """
        执行蒙特卡洛预测

        Args:
            df: 历史价格数据
            forecast_days: 预测天数
            **kwargs: 额外参数

        Returns:
            StrategyResult: 预测结果
        """
        if not self.validate_data(df):
            raise ValueError("Invalid input data")

        # 使用最近的数据计算参数
        lookback_days = kwargs.get('lookback_days', 90)
        recent_df = df.tail(lookback_days) if len(df) > lookback_days else df

        # 计算收益率统计
        returns = self._calculate_returns(recent_df)
        mu = returns['log_return'].mean()
        sigma = returns['log_return'].std()

        # 趋势调整
        trend_adjustment = 0
        if self.use_trend_adjustment:
            trend_adjustment = self._calculate_trend_adjustment(recent_df)
            mu += trend_adjustment

        # 当前价格
        current_price = df['close_price'].iloc[-1]

        # 蒙特卡洛模拟
        simulation_paths = self._monte_carlo_simulation(
            current_price=current_price,
            mu=mu,
            sigma=sigma,
            forecast_days=forecast_days
        )

        # 分析结果
        final_prices = simulation_paths[-1]

        # 计算置信区间
        alpha = (1 - self.confidence_level) / 2
        ci_low = np.percentile(final_prices, alpha * 100)
        ci_high = np.percentile(final_prices, (1 - alpha) * 100)

        # 计算概率
        up_prob = np.mean(final_prices > current_price) * 100
        down_prob = 100 - up_prob

        # 计算预测价格
        pred_mean = np.mean(final_prices)
        pred_median = np.median(final_prices)

        # 技术指标
        indicators = self.calculate_technical_indicators(df)

        return StrategyResult(
            timestamp=datetime.now(),
            current_price=current_price,
            forecast_days=forecast_days,
            predicted_price_mean=pred_mean,
            predicted_price_median=pred_median,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            up_probability=up_prob,
            down_probability=down_prob,
            metadata={
                'mu': mu,
                'sigma': sigma,
                'trend_adjustment': trend_adjustment,
                'indicators': indicators,
                'strategy': 'MonteCarlo'
            },
            simulation_paths=simulation_paths.tolist() if kwargs.get('return_paths', True) else None
        )

    def _calculate_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算收益率"""
        df = df.copy()
        df['daily_return'] = df['close_price'].pct_change()
        df['log_return'] = np.log(df['close_price'] / df['close_price'].shift(1))
        return df.dropna()

    def _calculate_trend_adjustment(self, df: pd.DataFrame) -> float:
        """
        基于技术指标计算趋势调整

        Returns:
            float: 漂移率调整值
        """
        close = df['close_price']

        # 计算多种指标
        score = 0

        # 1. 均线趋势
        ma7 = close.rolling(window=7).mean().iloc[-1] if len(close) >= 7 else None
        ma30 = close.rolling(window=30).mean().iloc[-1] if len(close) >= 30 else None
        if ma7 is not None and ma30 is not None:
            if ma7 > ma30:
                score += 20
            else:
                score -= 20

        # 2. RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_value = rsi.iloc[-1] if not rsi.empty else 50

        if rsi_value < 30:
            score += 25
        elif rsi_value > 70:
            score -= 25
        elif rsi_value < 50:
            score += 10
        else:
            score -= 10

        # 3. 近期涨跌
        if len(close) >= 8:
            change_7d = (close.iloc[-1] - close.iloc[-8]) / close.iloc[-8] * 100
            if change_7d > 0:
                score += 10
            else:
                score -= 10

        # 将评分转换为漂移率调整 (-0.5% 到 +0.5% 每日)
        return score / 100 * 0.005

    def _monte_carlo_simulation(self,
                                current_price: float,
                                mu: float,
                                sigma: float,
                                forecast_days: int) -> np.ndarray:
        """
        执行蒙特卡洛模拟

        Args:
            current_price: 当前价格
            mu: 日收益率均值
            sigma: 日收益率标准差
            forecast_days: 预测天数

        Returns:
            np.ndarray: 模拟路径 (forecast_days x n_simulations)
        """
        dt = 1  # 每日
        random_shocks = np.random.standard_normal((forecast_days, self.n_simulations))

        price_paths = np.zeros((forecast_days + 1, self.n_simulations))
        price_paths[0] = current_price

        for t in range(1, forecast_days + 1):
            price_paths[t] = price_paths[t-1] * np.exp(
                (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * random_shocks[t-1]
            )

        return price_paths[1:]  # 返回未来forecast_days的路径
