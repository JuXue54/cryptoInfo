"""Yahoo Finance数据获取模块 - 获取更多历史数据（10年以上）"""
import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import time

from config import SUPPORTED_ASSETS


class YahooDataFetcher:
    """Yahoo Finance数据获取器 - 支持更长的历史数据"""

    # Yahoo Finance的ticker映射
    TICKER_MAP = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }

    def __init__(self):
        self.rate_limit_delay = 0.5  # 请求间隔

    def fetch_historical_data(self, asset_code: str,
                              start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> List[Dict]:
        """
        获取历史价格数据（支持10年以上）

        Args:
            asset_code: 加密货币代码，如BTC
            start_date: 开始日期 (YYYY-MM-DD)，默认10年前
            end_date: 结束日期 (YYYY-MM-DD)，默认昨天

        Returns:
            价格数据列表
        """
        asset_code = asset_code.upper()

        if asset_code not in self.TICKER_MAP:
            raise ValueError(f"不支持的加密货币: {asset_code}")

        ticker = self.TICKER_MAP[asset_code]

        # 设置默认日期范围
        if end_date is None:
            end_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        if start_date is None:
            # 默认获取10年数据
            start_date = (datetime.now() - timedelta(days=365 * 10)).strftime('%Y-%m-%d')

        print(f"从Yahoo Finance获取 {asset_code} 从 {start_date} 到 {end_date} 的数据...")

        try:
            # 使用yfinance获取数据
            df = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                progress=False,
                auto_adjust=False  # 我们需要原始的OHLC数据
            )

            if df.empty:
                print(f"未获取到 {asset_code} 的数据")
                return []

            # 处理多级列名（yfinance返回的多级列）
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # 重命名列以匹配我们的格式
            result = []
            for date, row in df.iterrows():
                result.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'open_price': float(row['Open']),
                    'close_price': float(row['Close']),
                    'max_price': float(row['High']),
                    'min_price': float(row['Low']),
                })

            time.sleep(self.rate_limit_delay)
            return result

        except Exception as e:
            print(f"从Yahoo Finance获取数据失败: {e}")
            raise

    def fetch_incremental_data(self, asset_code: str,
                                last_date: Optional[str]) -> List[Dict]:
        """
        获取增量数据

        Args:
            asset_code: 加密货币代码
            last_date: 数据库中最新的日期，None表示获取全部历史数据

        Returns:
            新增的价格数据列表
        """
        if last_date is None:
            print(f"数据库中没有{asset_code}数据，获取全部历史数据（约10年）...")
            return self.fetch_historical_data(asset_code)

        # 计算起始日期（最新日期的下一天）
        last_dt = datetime.strptime(last_date, '%Y-%m-%d')
        from_dt = last_dt + timedelta(days=1)
        to_dt = datetime.now() - timedelta(days=1)  # 昨天

        if from_dt > to_dt:
            print(f"{asset_code}数据已是最新，无需更新")
            return []

        from_date = from_dt.strftime('%Y-%m-%d')
        to_date = to_dt.strftime('%Y-%m-%d')

        return self.fetch_historical_data(asset_code, from_date, to_date)

    def get_ticker_info(self, asset_code: str) -> Dict:
        """获取币种信息"""
        asset_code = asset_code.upper()
        if asset_code not in self.TICKER_MAP:
            raise ValueError(f"不支持的加密货币: {asset_code}")

        ticker = yf.Ticker(self.TICKER_MAP[asset_code])
        return ticker.info
