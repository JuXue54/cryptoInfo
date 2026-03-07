"""数据聚合模块 - 处理日线数据，生成周线、月线，计算均线"""
from typing import List, Optional
import pandas as pd
import numpy as np

from config import MA_PERIODS


class DataAggregator:
    """数据聚合器"""

    @staticmethod
    def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
        """
        将日线数据聚合为周线数据

        Args:
            df: 日线DataFrame，包含open_price, close_price, max_price, min_price

        Returns:
            周线DataFrame
        """
        if df.empty:
            return df

        # 确保索引是datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 按周重采样
        # 周以周日为结束日（'W'），或周一为开始日（'W-MON'）
        weekly = df.resample('W-MON').agg({
            'open_price': 'first',
            'close_price': 'last',
            'max_price': 'max',
            'min_price': 'min'
        })

        # 删除包含NaN的行
        weekly = weekly.dropna()

        return weekly

    @staticmethod
    def resample_to_monthly(df: pd.DataFrame) -> pd.DataFrame:
        """
        将日线数据聚合为月线数据

        Args:
            df: 日线DataFrame

        Returns:
            月线DataFrame
        """
        if df.empty:
            return df

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 按月重采样
        monthly = df.resample('ME').agg({
            'open_price': 'first',
            'close_price': 'last',
            'max_price': 'max',
            'min_price': 'min'
        })

        monthly = monthly.dropna()

        return monthly

    @staticmethod
    def add_moving_averages(df: pd.DataFrame, periods: Optional[List[int]] = None) -> pd.DataFrame:
        """
        添加移动平均线

        Args:
            df: 价格DataFrame
            periods: 均线周期列表，如[5, 10, 20, 60]，None则使用配置中的默认值

        Returns:
            添加了均线列的DataFrame
        """
        if df.empty:
            return df

        if periods is None:
            periods = list(MA_PERIODS.values())

        result = df.copy()

        for period in periods:
            if period <= 0:
                continue
            # 使用收盘价计算均线
            ma_column = f'MA{period}'
            result[ma_column] = result['close_price'].rolling(window=period, min_periods=1).mean()

        return result

    @staticmethod
    def get_data_by_timeframe(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """
        根据时间周期获取数据

        Args:
            df: 日线DataFrame
            timeframe: 时间周期，'day'日线, 'week'周线, 'month'月线

        Returns:
            对应周期的DataFrame
        """
        if timeframe == 'day':
            return df
        elif timeframe == 'week':
            return DataAggregator.resample_to_weekly(df)
        elif timeframe == 'month':
            return DataAggregator.resample_to_monthly(df)
        else:
            raise ValueError(f"不支持的时间周期: {timeframe}，请使用 'day', 'week' 或 'month'")

    @staticmethod
    def prepare_for_chart(df: pd.DataFrame) -> pd.DataFrame:
        """
        准备数据用于绘图，重命名列以符合mplfinance格式

        Args:
            df: 价格DataFrame

        Returns:
            重命名列后的DataFrame
        """
        if df.empty:
            return df

        result = df.copy()

        # 重命名列为mplfinance标准格式
        column_mapping = {
            'open_price': 'Open',
            'close_price': 'Close',
            'max_price': 'High',
            'min_price': 'Low',
        }

        result = result.rename(columns=column_mapping)

        return result

    @staticmethod
    def filter_by_date(df: pd.DataFrame,
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> pd.DataFrame:
        """
        按日期范围过滤数据

        Args:
            df: 价格DataFrame
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)

        Returns:
            过滤后的DataFrame
        """
        if df.empty:
            return df

        result = df.copy()

        if not isinstance(result.index, pd.DatetimeIndex):
            result.index = pd.to_datetime(result.index)

        if start_date:
            result = result[result.index >= start_date]
        if end_date:
            result = result[result.index <= end_date]

        return result
