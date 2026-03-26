"""
测试仓位管理字段修复
验证 database.calculate_portfolio_stats 返回正确的字段名
"""
import unittest
import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.database import Database


class TestPortfolioStats(unittest.TestCase):
    """测试仓位统计字段"""

    @classmethod
    def setUpClass(cls):
        """设置测试数据库"""
        cls.test_db_path = 'test_portfolio.db'
        cls.db = Database(cls.test_db_path)

        # 创建测试仓位
        cls.portfolio_id = cls.db.create_portfolio(
            name='测试仓位',
            asset_code='BTC',
            mode='manual',
            initial_capital=10000
        )

        # 添加测试交易
        cls.db.add_trade(cls.portfolio_id, '2024-01-01', 'buy', 50000, 0.1, 10)
        cls.db.add_trade(cls.portfolio_id, '2024-01-15', 'sell', 55000, 0.05, 10)

    @classmethod
    def tearDownClass(cls):
        """清理测试数据库"""
        if os.path.exists(cls.test_db_path):
            os.remove(cls.test_db_path)

    def test_stats_has_correct_field_names(self):
        """测试统计结果包含前端期望的字段名"""
        stats = self.db.calculate_portfolio_stats(self.portfolio_id, 60000)

        # 验证前端期望的字段存在
        required_fields = [
            'current_quantity',  # 不是 current_qty
            'avg_cost',
            'current_price',
            'unrealized_pnl',
            'realized_pnl',
            'total_return',  # 不是 total_return_pct
        ]

        for field in required_fields:
            self.assertIn(field, stats, f"缺少字段: {field}")

    def test_stats_values_are_numbers(self):
        """测试统计值是数字类型（不是None）"""
        stats = self.db.calculate_portfolio_stats(self.portfolio_id, 60000)

        # 验证前端会调用 toFixed 的字段是数字
        numeric_fields = [
            'current_quantity',
            'avg_cost',
            'current_price',
            'unrealized_pnl',
            'realized_pnl',
            'total_return',
        ]

        for field in numeric_fields:
            value = stats.get(field)
            self.assertIsNotNone(value, f"{field} 不能为 None")
            self.assertIsInstance(value, (int, float), f"{field} 必须是数字")


if __name__ == '__main__':
    unittest.main(verbosity=2)
