"""
回测引擎模块

用于回测预测策略的历史表现
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Type, Any

from ..strategies.base import PredictionStrategy, BacktestResult
from .metrics import calculate_metrics


class BacktestEngine:
    """
    回测引擎

    支持多种预测策略的回测，评估策略的历史表现
    """

    def __init__(self, strategy: PredictionStrategy):
        """
        初始化回测引擎

        Args:
            strategy: 预测策略实例
        """
        self.strategy = strategy
        self.results = []

    def run_backtest(self,
                     df: pd.DataFrame,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     forecast_days: int = 7,
                     step_days: int = 7,
                     min_history_days: int = 90,
                     progress_callback=None,
                     strategy_params: Optional[Dict[str, Any]] = None) -> BacktestResult:
        """
        运行回测

        Args:
            df: 完整的历史数据
            start_date: 回测开始日期，None则从最早数据开始
            end_date: 回测结束日期，None则到最后数据
            forecast_days: 每次预测的天数
            step_days: 步进天数（每隔多少天进行一次预测）
            min_history_days: 最小历史数据天数
            progress_callback: 进度回调函数
            strategy_params: 策略参数字典
                - long_threshold: 做多阈值
                - short_threshold: 做空阈值
                - use_position_sizing: 是否使用仓位管理
                - trend_filter: 趋势过滤

        Returns:
            BacktestResult: 回测结果
        """
        # 数据准备
        df = df.copy()
        df.index = pd.to_datetime(df.index)

        # 确定回测时间范围
        if start_date:
            start_dt = pd.to_datetime(start_date)
        else:
            start_dt = df.index[min_history_days]

        if end_date:
            end_dt = pd.to_datetime(end_date)
        else:
            end_dt = df.index[-forecast_days - 1]

        # 生成回测时间点
        backtest_dates = []
        current_date = start_dt
        while current_date <= end_dt:
            # 找到最近的交易日
            valid_dates = df.index[df.index >= current_date]
            if len(valid_dates) == 0:
                break
            backtest_dates.append(valid_dates[0])
            current_date = valid_dates[0] + timedelta(days=step_days)

        if len(backtest_dates) == 0:
            raise ValueError("没有足够的回测时间点")

        # 执行回测
        predictions = []
        total = len(backtest_dates)

        # 若策略支持全量特征预计算（ML策略），一次算好整段历史的特征，
        # 之后每步只做切片——避免逐步对增长窗口重算全部特征（O(N²) → O(N)）
        if hasattr(self.strategy, 'set_reference_frame'):
            self.strategy.set_reference_frame(df)

        for i, backtest_date in enumerate(backtest_dates):
            try:
                # 该时间点之前的历史数据：位置切片即可
                # （无需布尔掩码全扫 + 整表copy，策略只读不写输入）
                pos = df.index.searchsorted(backtest_date, side='right')
                history_df = df.iloc[:pos]
                if len(history_df) < min_history_days:
                    continue

                # 当前价格
                current_price = history_df['close_price'].iloc[-1]
                current_date = history_df.index[-1]

                # 获取未来实际价格
                future_idx_actual = pos - 1 + forecast_days

                if future_idx_actual >= len(df):
                    continue

                actual_future_price = df.iloc[future_idx_actual]['close_price']
                actual_future_date = df.index[future_idx_actual]

                # 执行预测
                result = self.strategy.predict(
                    history_df,
                    forecast_days=forecast_days,
                    return_paths=False  # 回测不需要路径数据
                )

                # 记录结果
                prediction_record = {
                    'date': current_date.strftime('%Y-%m-%d'),
                    'current_price': current_price,
                    'predicted_price': result.predicted_price_mean,
                    'predicted_up_probability': result.up_probability,
                    'predicted_down_probability': result.down_probability,
                    'actual_future_price': actual_future_price,
                    'actual_future_date': actual_future_date.strftime('%Y-%m-%d'),
                    'actual_return': (actual_future_price - current_price) / current_price * 100,
                    'expected_return': result.expected_return,
                    'direction_correct': bool((result.predicted_price_mean > current_price) == (actual_future_price > current_price)),
                    'confidence_low': result.confidence_interval_low,
                    'confidence_high': result.confidence_interval_high,
                    'in_confidence_range': bool(result.confidence_interval_low <= actual_future_price <= result.confidence_interval_high)
                }

                predictions.append(prediction_record)

                # 回调进度
                if progress_callback:
                    progress_callback((i + 1) / total * 100)

            except Exception as e:
                print(f"回测失败在 {backtest_date}: {e}")
                continue

        if len(predictions) == 0:
            raise ValueError("没有成功生成任何预测结果")

        # 计算回测指标（传入策略参数）
        metrics = calculate_metrics(predictions, strategy_params)

        return BacktestResult(
            strategy_name=self.strategy.name,
            start_date=start_date or df.index[0].strftime('%Y-%m-%d'),
            end_date=end_date or df.index[-1].strftime('%Y-%m-%d'),
            forecast_days=forecast_days,
            total_predictions=len(predictions),
            **metrics,
            predictions=predictions
        )

    def compare_strategies(self,
                          df: pd.DataFrame,
                          strategies: List[PredictionStrategy],
                          **backtest_kwargs) -> Dict[str, BacktestResult]:
        """
        比较多个策略的表现

        Args:
            df: 历史数据
            strategies: 策略列表
            **backtest_kwargs: 回测参数

        Returns:
            Dict[str, BacktestResult]: 策略名称到回测结果的映射
        """
        results = {}
        for strategy in strategies:
            print(f"\n回测策略: {strategy.name}")
            self.strategy = strategy
            result = self.run_backtest(df, **backtest_kwargs)
            results[strategy.name] = result

        return results
