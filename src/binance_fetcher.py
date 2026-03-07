"""Binance数据获取模块 - 获取USDT计价的加密货币数据"""
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import time


class BinanceDataFetcher:
    """Binance数据获取器 - 支持USDT计价"""

    # Binance交易对映射
    TICKER_MAP = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
    }

    BASE_URL = "https://api.binance.com/api/v3"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
        })
        self.rate_limit_delay = 0.5

    def _make_request(self, endpoint: str, params: dict) -> dict:
        """发送请求并处理响应"""
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def fetch_historical_data(self, asset_code: str,
                              start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> List[Dict]:
        """
        获取历史K线数据（USDT计价）

        Args:
            asset_code: 加密货币代码，如BTC
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)

        Returns:
            价格数据列表
        """
        asset_code = asset_code.upper()

        if asset_code not in self.TICKER_MAP:
            raise ValueError(f"不支持的加密货币: {asset_code}")

        symbol = self.TICKER_MAP[asset_code]

        # 设置默认日期范围
        if end_date is None:
            end_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        if start_date is None:
            # 默认获取20年数据
            start_date = (datetime.now() - timedelta(days=365 * 20)).strftime('%Y-%m-%d')

        print(f"从Binance获取 {asset_code} 从 {start_date} 到 {end_date} 的USDT数据...")

        try:
            # 转换日期为毫秒时间戳
            start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
            end_ts = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp() * 1000)

            all_data = []
            current_ts = start_ts

            # Binance限制每次请求1000条数据，需要分页获取
            while current_ts < end_ts:
                params = {
                    "symbol": symbol,
                    "interval": "1d",  # 日线
                    "startTime": current_ts,
                    "limit": 1000
                }

                data = self._make_request("klines", params)

                if not data:
                    break

                # Binance K线数据格式:
                # [开盘时间, 开盘价, 最高价, 最低价, 收盘价, ...]
                for item in data:
                    timestamp_ms = item[0]
                    date = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d')

                    all_data.append({
                        'date': date,
                        'open_price': float(item[1]),
                        'high_price': float(item[2]),
                        'low_price': float(item[3]),
                        'close_price': float(item[4]),
                        'max_price': float(item[2]),
                        'min_price': float(item[3]),
                    })

                # 更新当前时间戳为最后一条数据的时间
                current_ts = data[-1][0] + 86400000  # 加一天的毫秒数

                time.sleep(self.rate_limit_delay)

                if len(data) < 1000:
                    break

            # 过滤日期范围
            result = [d for d in all_data if start_date <= d['date'] <= end_date]

            print(f"  成功获取 {len(result)} 条USDT数据")
            return result

        except Exception as e:
            print(f"获取数据失败: {e}")
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
            print(f"数据库中没有{asset_code}数据，获取全部历史数据...")
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

        print(f"获取{asset_code}从{from_date}到{to_date}的USDT数据...")
        return self.fetch_historical_data(asset_code, from_date, to_date)

    def get_symbol_info(self, asset_code: str) -> Dict:
        """获取交易对信息"""
        asset_code = asset_code.upper()
        if asset_code not in self.TICKER_MAP:
            raise ValueError(f"不支持的加密货币: {asset_code}")

        symbol = self.TICKER_MAP[asset_code]
        data = self._make_request("exchangeInfo", {"symbol": symbol})
        return data
