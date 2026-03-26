"""
蒙特卡洛策略单元测试
用于排查 'Cannot read properties of undefined (reading toLocaleString)' 错误
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.strategies.monte_carlo import MonteCarloStrategy
from src.strategies.base import StrategyResult


class TestMonteCarloStrategy(unittest.TestCase):
    """测试蒙特卡洛策略"""

    def setUp(self):
        """设置测试数据"""
        # 创建模拟价格数据
        np.random.seed(42)
        dates = pd.date_range(start='2024-01-01', end='2024-03-01', freq='D')
        n = len(dates)

        # 生成随机游走价格数据
        returns = np.random.normal(0.001, 0.02, n)
        prices = 100 * np.exp(np.cumsum(returns))

        self.df = pd.DataFrame({
            'close_price': prices,
            'volume': np.random.randint(1000000, 10000000, n)
        }, index=dates)

        self.strategy = MonteCarloStrategy(
            n_simulations=1000,
            use_trend_adjustment=True
        )

    def test_validate_data(self):
        """测试数据验证"""
        self.assertTrue(self.strategy.validate_data(self.df))

    def test_predict_basic(self):
        """测试基本预测功能"""
        result = self.strategy.predict(self.df, forecast_days=7)

        self.assertIsInstance(result, StrategyResult)
        self.assertIsNotNone(result.timestamp)
        self.assertIsNotNone(result.current_price)
        self.assertIsNotNone(result.predicted_price_mean)
        self.assertIsNotNone(result.predicted_price_median)
        self.assertIsNotNone(result.confidence_interval_low)
        self.assertIsNotNone(result.confidence_interval_high)
        self.assertIsNotNone(result.up_probability)
        self.assertIsNotNone(result.down_probability)

    def test_predict_with_return_paths(self):
        """测试返回模拟路径"""
        result = self.strategy.predict(self.df, forecast_days=7, return_paths=True)

        self.assertIsNotNone(result.simulation_paths)
        # 验证路径数据格式
        self.assertIsInstance(result.simulation_paths, list)

    def test_predict_without_return_paths(self):
        """测试不返回模拟路径"""
        result = self.strategy.predict(self.df, forecast_days=7, return_paths=False)

        # 当 return_paths=False 时，simulation_paths 应该为 None
        self.assertIsNone(result.simulation_paths)

    def test_calculate_returns(self):
        """测试收益率计算"""
        returns_df = self.strategy._calculate_returns(self.df)

        self.assertIn('daily_return', returns_df.columns)
        self.assertIn('log_return', returns_df.columns)
        # 检查没有NaN值
        self.assertFalse(returns_df['daily_return'].isna().any())
        self.assertFalse(returns_df['log_return'].isna().any())

    def test_trend_adjustment(self):
        """测试趋势调整计算"""
        adjustment = self.strategy._calculate_trend_adjustment(self.df)

        self.assertIsInstance(adjustment, float)
        # 调整值应该在合理范围内
        self.assertGreaterEqual(adjustment, -0.01)
        self.assertLessEqual(adjustment, 0.01)

    def test_monte_carlo_simulation(self):
        """测试蒙特卡洛模拟"""
        current_price = 100.0
        mu = 0.001
        sigma = 0.02
        forecast_days = 7

        paths = self.strategy._monte_carlo_simulation(
            current_price, mu, sigma, forecast_days
        )

        self.assertEqual(paths.shape, (forecast_days, self.strategy.n_simulations))
        # 所有价格应该为正
        self.assertTrue((paths > 0).all())

    def test_short_data(self):
        """测试短数据情况"""
        # 只有30天的数据
        short_df = self.df.tail(30)

        result = self.strategy.predict(short_df, forecast_days=7)
        self.assertIsInstance(result, StrategyResult)

    def test_probability_sum(self):
        """测试上涨和下跌概率之和约为100"""
        result = self.strategy.predict(self.df, forecast_days=7)

        total_prob = result.up_probability + result.down_probability
        self.assertAlmostEqual(total_prob, 100, delta=0.1)

    def test_metadata_contains_required_fields(self):
        """测试metadata包含必要字段"""
        result = self.strategy.predict(self.df, forecast_days=7)

        required_fields = ['mu', 'sigma', 'trend_adjustment', 'indicators', 'strategy']
        for field in required_fields:
            self.assertIn(field, result.metadata)

    def test_indicators_not_none(self):
        """测试技术指标不为None"""
        result = self.strategy.predict(self.df, forecast_days=7)

        indicators = result.metadata.get('indicators')
        self.assertIsNotNone(indicators)


class TestMonteCarloEdgeCases(unittest.TestCase):
    """测试边界情况"""

    def test_extremely_short_data(self):
        """测试极短数据"""
        np.random.seed(42)
        dates = pd.date_range(start='2024-01-01', periods=10, freq='D')
        df = pd.DataFrame({
            'close_price': np.random.uniform(90, 110, 10),
            'volume': np.random.randint(1000000, 10000000, 10)
        }, index=dates)

        strategy = MonteCarloStrategy(n_simulations=100)
        result = strategy.predict(df, forecast_days=7)

        self.assertIsInstance(result, StrategyResult)

    def test_constant_price(self):
        """测试价格不变的情况"""
        dates = pd.date_range(start='2024-01-01', periods=60, freq='D')
        df = pd.DataFrame({
            'close_price': [100.0] * 60,
            'volume': [1000000] * 60
        }, index=dates)

        strategy = MonteCarloStrategy(n_simulations=100)
        result = strategy.predict(df, forecast_days=7)

        self.assertIsInstance(result, StrategyResult)


if __name__ == '__main__':
    unittest.main(verbosity=2)
