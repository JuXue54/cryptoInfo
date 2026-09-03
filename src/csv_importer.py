"""CSV数据导入模块 - 从CSV文件导入历史数据"""
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional
import os


class CSVImporter:
    """CSV数据导入器"""

    @staticmethod
    def import_from_csv(file_path: str, asset_code: str, date_format: str = None) -> List[Dict]:
        """
        从CSV文件导入数据

        期望的CSV格式（列名）:
        - date: 日期 (YYYY-MM-DD)
        - open: 开盘价
        - high: 最高价
        - low: 最低价
        - close: 收盘价

        Args:
            file_path: CSV文件路径
            asset_code: 加密货币代码 (BTC/ETH)
            date_format: 日期格式，默认自动检测

        Returns:
            价格数据列表
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 读取CSV
        df = pd.read_csv(file_path)

        # 标准化列名（转换为小写）
        df.columns = df.columns.str.lower().str.strip()

        # 自动识别列名映射
        column_mapping = {}

        # 日期列
        date_candidates = ['date', 'datetime', 'time', 'timestamp', '日期', '时间']
        for col in df.columns:
            if any(cand in col for cand in date_candidates):
                column_mapping['date'] = col
                break

        # 开盘价
        open_candidates = ['open', '开盘价', 'open_price']
        for col in df.columns:
            if any(cand in col for cand in open_candidates):
                column_mapping['open'] = col
                break

        # 收盘价
        close_candidates = ['close', '收盘价', 'close_price', 'closing']
        for col in df.columns:
            if any(cand in col for cand in close_candidates):
                column_mapping['close'] = col
                break

        # 最高价
        high_candidates = ['high', '最高价', 'high_price', 'max', 'max_price']
        for col in df.columns:
            if any(cand in col for cand in high_candidates):
                column_mapping['high'] = col
                break

        # 最低价
        low_candidates = ['low', '最低价', 'low_price', 'min', 'min_price']
        for col in df.columns:
            if any(cand in col for cand in low_candidates):
                column_mapping['low'] = col
                break

        # 检查必需的列
        required = ['date', 'open', 'high', 'low', 'close']
        missing = [r for r in required if r not in column_mapping]
        if missing:
            raise ValueError(f"CSV文件缺少必需的列: {missing}\n找到的列: {df.columns.tolist()}")

        # 重命名列
        reverse_mapping = {v: k for k, v in column_mapping.items()}
        df = df.rename(columns=reverse_mapping)

        # 解析日期
        if date_format:
            df['date'] = pd.to_datetime(df['date'], format=date_format)
        else:
            df['date'] = pd.to_datetime(df['date'])

        # 向量化转换为标准格式
        result_df = pd.DataFrame({
            'date': df['date'].dt.strftime('%Y-%m-%d'),
            'open_price': df['open'].astype(float),
            'close_price': df['close'].astype(float),
            'max_price': df['high'].astype(float),
            'min_price': df['low'].astype(float),
        })
        result = result_df.to_dict('records')

        print(f"成功从CSV导入 {len(result)} 条 {asset_code} 数据")
        return result

    @staticmethod
    def get_sample_csv_format() -> str:
        """返回示例CSV格式说明"""
        return """
CSV文件格式要求:

必需包含以下列（列名不区分大小写）:
- date: 日期 (支持多种格式，如 2024-01-01, 2024/01/01, 等)
- open: 开盘价
- high: 最高价
- low: 最低价
- close: 收盘价

示例CSV内容:
date,open,high,low,close
2020-01-01,7200.00,7250.00,7150.00,7220.00
2020-01-02,7220.00,7300.00,7200.00,7280.00
...

你可以从以下网站下载历史数据:
1. Yahoo Finance (finance.yahoo.com) - 搜索 BTC-USD 或 ETH-USD
2. CoinMarketCap (coinmarketcap.com)
3. Investing.com
4. Kaggle数据集
        """
