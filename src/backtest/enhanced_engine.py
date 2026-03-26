"""
增强版回测引擎

支持：
- 连续持仓和复利计算
- 动态仓位管理
- 止损止盈
- 多策略组合
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Type, Any, Callable
from dataclasses import dataclass
from enum import Enum

from ..strategies.base import PredictionStrategy, BacktestResult
from .metrics import calculate_metrics


class Position(Enum):
    """持仓状态"""
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Trade:
    """交易记录"""
    entry_date: datetime
    exit_date: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    position: Position
    size: float
    pnl: Optional[float]
    pnl_pct: Optional[float]
    exit_reason: str


class EnhancedBacktestEngine:
    """
    增强版回测引擎

    支持连续持仓、复利计算、止损止盈
    """

    def __init__(self, strategy: PredictionStrategy):
        self.strategy = strategy
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        self.dates: List[datetime] = []
        self.position_history: List[Dict] = []  # 新增：仓位历史记录
    def run_backtest(self,
                     df: pd.DataFrame,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     initial_capital: float = 10000,
                     position_size: float = 1.0,
                     use_compound: bool = True,
                     stop_loss_pct: Optional[float] = None,
                     take_profit_pct: Optional[float] = None,
                     trailing_stop_pct: Optional[float] = None,
                     max_position_hold_days: int = 30,
                     rebalance_freq: str = 'daily',
                     min_history_days: int = 90,
                     progress_callback=None,
                     strategy_params: Optional[Dict[str, Any]] = None) -> BacktestResult:
        """
        运行增强版回测

        Args:
            df: 历史数据
            start_date: 回测开始日期
            end_date: 回测结束日期
            initial_capital: 初始资金
            position_size: 仓位大小 (0-1)
            use_compound: 是否使用复利
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            trailing_stop_pct: 移动止损百分比
            max_position_hold_days: 最大持仓天数
            rebalance_freq: 再平衡频率 ('daily', 'weekly', 'signal')
            min_history_days: 最小历史数据天数
            progress_callback: 进度回调
            strategy_params: 策略参数

        Returns:
            BacktestResult: 回测结果
        """
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
            end_dt = df.index[-1]

        df = df[(df.index >= start_dt) & (df.index <= end_dt)]

        if len(df) == 0:
            raise ValueError("回测时间范围内没有数据")

        # 初始化
        capital = initial_capital
        current_position = Position.FLAT
        position_entry_price = 0
        position_entry_date = None
        highest_price_since_entry = 0
        lowest_price_since_entry = float('inf')
        realized_pnl = 0  # 累计已实现盈亏

        self.equity_curve = [capital]
        self.dates = [df.index[0]]
        self.trades = []
        self.position_history = []  # 重置仓位历史

        predictions = []
        current_trade = None

        # 生成再平衡时间点
        rebalance_dates = self._generate_rebalance_dates(df, rebalance_freq)

        total_steps = len(df)
        current_signal_strength = 0  # 当前信号强度

        # 记录初始状态
        self._record_position_state(
            date=df.index[0],
            current_position=Position.FLAT,
            current_price=df['close_price'].iloc[0],
            entry_price=0,
            capital=capital,
            unrealized_pnl=0,
            realized_pnl=realized_pnl,
            position_size=0,
            signal_strength=0,
            reason="Initial State"
        )

        for i, (date, row) in enumerate(df.iterrows()):
            current_price = row['close_price']

            # 检查是否需要再平衡
            should_rebalance = date in rebalance_dates

            # 如果有持仓，检查止损止盈
            if current_position != Position.FLAT and current_trade is not None:
                # 更新最高/最低价
                highest_price_since_entry = max(highest_price_since_entry, current_price)
                lowest_price_since_entry = min(lowest_price_since_entry, current_price)

                exit_signal = False
                exit_reason = ""

                # 检查止损
                if stop_loss_pct and current_position == Position.LONG:
                    loss_pct = (position_entry_price - current_price) / position_entry_price * 100
                    if loss_pct >= stop_loss_pct:
                        exit_signal = True
                        exit_reason = f"Stop Loss (-{loss_pct:.1f}%)"

                elif stop_loss_pct and current_position == Position.SHORT:
                    loss_pct = (current_price - position_entry_price) / position_entry_price * 100
                    if loss_pct >= stop_loss_pct:
                        exit_signal = True
                        exit_reason = f"Stop Loss (-{loss_pct:.1f}%)"

                # 检查止盈
                if take_profit_pct and current_position == Position.LONG:
                    profit_pct = (current_price - position_entry_price) / position_entry_price * 100
                    if profit_pct >= take_profit_pct:
                        exit_signal = True
                        exit_reason = f"Take Profit (+{profit_pct:.1f}%)"

                elif take_profit_pct and current_position == Position.SHORT:
                    profit_pct = (position_entry_price - current_price) / position_entry_price * 100
                    if profit_pct >= take_profit_pct:
                        exit_signal = True
                        exit_reason = f"Take Profit (+{profit_pct:.1f}%)"

                # 检查移动止损
                if trailing_stop_pct and current_position == Position.LONG:
                    max_profit_pct = (highest_price_since_entry - position_entry_price) / position_entry_price * 100
                    pullback_pct = (highest_price_since_entry - current_price) / highest_price_since_entry * 100
                    if pullback_pct >= trailing_stop_pct and max_profit_pct > trailing_stop_pct:
                        exit_signal = True
                        exit_reason = f"Trailing Stop ({pullback_pct:.1f}% pullback)"

                elif trailing_stop_pct and current_position == Position.SHORT:
                    max_profit_pct = (position_entry_price - lowest_price_since_entry) / position_entry_price * 100
                    pullback_pct = (current_price - lowest_price_since_entry) / lowest_price_since_entry * 100
                    if pullback_pct >= trailing_stop_pct and max_profit_pct > trailing_stop_pct:
                        exit_signal = True
                        exit_reason = f"Trailing Stop ({pullback_pct:.1f}% pullback)"

                # 检查最大持仓时间
                if position_entry_date:
                    hold_days = (date - position_entry_date).days
                    if hold_days >= max_position_hold_days:
                        exit_signal = True
                        exit_reason = f"Max Hold Time ({hold_days} days)"

                # 执行平仓
                if exit_signal:
                    pnl = self._calculate_pnl(
                        current_position, position_entry_price,
                        current_price, current_trade.size if current_trade else 0
                    )
                    pnl_pct = (current_price - position_entry_price) / position_entry_price * 100
                    if current_position == Position.SHORT:
                        pnl_pct = -pnl_pct

                    capital += pnl
                    realized_pnl += pnl  # 累计已实现盈亏

                    current_trade.exit_date = date
                    current_trade.exit_price = current_price
                    current_trade.pnl = pnl
                    current_trade.pnl_pct = pnl_pct
                    current_trade.exit_reason = exit_reason
                    self.trades.append(current_trade)

                    # 记录仓位变化
                    self._record_position_state(
                        date=date,
                        current_position=current_position,
                        current_price=current_price,
                        entry_price=position_entry_price,
                        capital=capital,
                        unrealized_pnl=0,
                        realized_pnl=realized_pnl,
                        position_size=0,
                        signal_strength=current_signal_strength,
                        reason=f"Exit: {exit_reason}"
                    )

                    current_position = Position.FLAT
                    current_trade = None
                    position_entry_price = 0

            # 再平衡时获取新信号
            if should_rebalance and i >= min_history_days:
                history_df = df.iloc[:i+1]

                try:
                    result = self.strategy.predict(
                        history_df,
                        forecast_days=7,
                        return_paths=False
                    )

                    up_prob = result.up_probability
                    current_signal_strength = abs(up_prob - 50) / 50  # 0-1

                    # 确定目标仓位
                    if up_prob > 60:
                        target_position = Position.LONG
                    elif up_prob < 40:
                        target_position = Position.SHORT
                    else:
                        target_position = Position.FLAT

                    # 执行调仓
                    if target_position != current_position:
                        # 先平旧仓位
                        if current_position != Position.FLAT and current_trade:
                            exit_price = current_price
                            pnl = self._calculate_pnl(
                                current_position, position_entry_price,
                                exit_price, current_trade.size
                            )
                            pnl_pct = (exit_price - position_entry_price) / position_entry_price * 100
                            if current_position == Position.SHORT:
                                pnl_pct = -pnl_pct

                            capital += pnl
                            realized_pnl += pnl

                            current_trade.exit_date = date
                            current_trade.exit_price = exit_price
                            current_trade.pnl = pnl
                            current_trade.pnl_pct = pnl_pct
                            current_trade.exit_reason = "Signal Change"
                            self.trades.append(current_trade)

                            # 记录平仓状态
                            self._record_position_state(
                                date=date,
                                current_position=current_position,
                                current_price=current_price,
                                entry_price=position_entry_price,
                                capital=capital,
                                unrealized_pnl=0,
                                realized_pnl=realized_pnl,
                                position_size=0,
                                signal_strength=current_signal_strength,
                                reason="Exit: Signal Change"
                            )

                        # 开新仓位
                        if target_position != Position.FLAT:
                            actual_position_size = position_size
                            if strategy_params and strategy_params.get('use_position_sizing'):
                                # 根据信号强度调整仓位
                                actual_position_size = position_size * (0.3 + current_signal_strength * 0.7)

                            trade_size = capital * actual_position_size

                            current_trade = Trade(
                                entry_date=date,
                                exit_date=None,
                                entry_price=current_price,
                                exit_price=None,
                                position=target_position,
                                size=trade_size,
                                pnl=None,
                                pnl_pct=None,
                                exit_reason=""
                            )

                            position_entry_price = current_price
                            position_entry_date = date
                            highest_price_since_entry = current_price
                            lowest_price_since_entry = current_price

                            # 记录开仓状态
                            self._record_position_state(
                                date=date,
                                current_position=target_position,
                                current_price=current_price,
                                entry_price=current_price,
                                capital=capital - trade_size,
                                unrealized_pnl=0,
                                realized_pnl=realized_pnl,
                                position_size=actual_position_size,
                                signal_strength=current_signal_strength,
                                reason=f"Entry: Signal ({up_prob:.1f}% up)"
                            )

                        current_position = target_position

                    # 记录预测
                    predictions.append({
                        'date': date.strftime('%Y-%m-%d'),
                        'current_price': current_price,
                        'predicted_price': result.predicted_price_mean,
                        'predicted_up_probability': up_prob,
                        'predicted_down_probability': result.down_probability,
                        'actual_future_price': current_price,  # 会在后面更新
                        'position': current_position.value,
                        'capital': capital
                    })

                except Exception as e:
                    print(f"预测失败在 {date}: {e}")

            # 更新权益曲线
            unrealized_pnl = 0
            if current_position != Position.FLAT and current_trade:
                unrealized_pnl = self._calculate_pnl(
                    current_position, position_entry_price,
                    current_price, current_trade.size
                )

            total_equity = capital + unrealized_pnl
            self.equity_curve.append(total_equity)
            self.dates.append(date)

            # 记录每日仓位状态（只在再平衡日或有持仓时记录，避免数据过大）
            if should_rebalance or current_position != Position.FLAT:
                # 计算当前仓位比例
                current_position_size = 0
                if current_position != Position.FLAT and current_trade:
                    current_position_size = current_trade.size / total_equity if total_equity > 0 else 0

                self._record_position_state(
                    date=date,
                    current_position=current_position,
                    current_price=current_price,
                    entry_price=position_entry_price,
                    capital=capital,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
                    position_size=current_position_size,
                    signal_strength=current_signal_strength,
                    reason="Daily Update" if should_rebalance else "Position Holding"
                )

            if progress_callback:
                progress_callback((i + 1) / total_steps * 100)

        # 平掉最后的仓位
        if current_position != Position.FLAT and current_trade:
            final_price = df['close_price'].iloc[-1]
            pnl = self._calculate_pnl(
                current_position, position_entry_price,
                final_price, current_trade.size
            )
            pnl_pct = (final_price - position_entry_price) / position_entry_price * 100
            if current_position == Position.SHORT:
                pnl_pct = -pnl_pct

            current_trade.exit_date = df.index[-1]
            current_trade.exit_price = final_price
            current_trade.pnl = pnl
            current_trade.pnl_pct = pnl_pct
            current_trade.exit_reason = "End of Backtest"
            self.trades.append(current_trade)

            # 记录最终平仓状态
            realized_pnl += pnl
            self._record_position_state(
                date=df.index[-1],
                current_position=current_position,
                current_price=final_price,
                entry_price=position_entry_price,
                capital=capital + pnl,
                unrealized_pnl=0,
                realized_pnl=realized_pnl,
                position_size=0,
                signal_strength=current_signal_strength,
                reason="Exit: End of Backtest"
            )

        # 计算最终收益
        final_capital = self.equity_curve[-1]
        total_return = (final_capital - initial_capital) / initial_capital * 100

        # 计算回测指标
        metrics = self._calculate_enhanced_metrics(
            initial_capital, final_capital, df
        )

        # 构造结果
        result_data = {
            'strategy_name': self.strategy.name,
            'start_date': start_date or df.index[0].strftime('%Y-%m-%d'),
            'end_date': end_date or df.index[-1].strftime('%Y-%m-%d'),
            'forecast_days': 1,
            'total_predictions': len(predictions),
            **metrics,
            'predictions': predictions,
            'position_history': self.position_history,
            'trades': self.trades
        }

        return BacktestResult(**result_data)

    def _generate_rebalance_dates(self, df: pd.DataFrame, freq: str) -> set:
        """生成再平衡日期"""
        dates = set()

        if freq == 'daily':
            dates = set(df.index)
        elif freq == 'weekly':
            current_week = None
            for date in df.index:
                week = date.isocalendar()[1]
                if week != current_week:
                    dates.add(date)
                    current_week = week
        elif freq == 'signal':
            # 只在策略信号变化时再平衡
            dates = set(df.index)

        return dates

    def _calculate_pnl(self, position: Position, entry_price: float,
                       exit_price: float, size: float) -> float:
        """计算盈亏"""
        if position == Position.LONG:
            return size * (exit_price - entry_price) / entry_price
        elif position == Position.SHORT:
            return size * (entry_price - exit_price) / entry_price
        return 0

    def _record_position_state(self,
                               date: datetime,
                               current_position: Position,
                               current_price: float,
                               entry_price: float,
                               capital: float,
                               unrealized_pnl: float,
                               realized_pnl: float,
                               position_size: float,
                               signal_strength: float,
                               reason: str):
        """
        记录仓位历史状态

        Args:
            date: 日期
            current_position: 当前持仓方向
            current_price: 当前价格
            entry_price: 入场价格
            capital: 现金
            unrealized_pnl: 浮动盈亏
            realized_pnl: 已实现盈亏
            position_size: 仓位比例 (0-1)
            signal_strength: 信号强度 (0-1)
            reason: 记录原因
        """
        total_equity = capital + unrealized_pnl

        self.position_history.append({
            'date': date,
            'position': current_position.name if current_position != Position.FLAT else 'FLAT',
            'position_size': position_size if current_position != Position.FLAT else 0,
            'entry_price': entry_price if current_position != Position.FLAT else 0,
            'current_price': current_price,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'total_equity': total_equity,
            'cash': capital,
            'signal_strength': signal_strength,
            'reason': reason
        })

    def _calculate_enhanced_metrics(self, initial_capital: float,
                                    final_capital: float,
                                    df: pd.DataFrame) -> Dict[str, float]:
        """计算增强版指标"""
        returns = []
        for i in range(1, len(self.equity_curve)):
            daily_return = (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1] * 100
            returns.append(daily_return)

        returns_array = np.array(returns)

        # 基础指标
        total_return = (final_capital - initial_capital) / initial_capital * 100

        # 年化收益率
        total_days = (df.index[-1] - df.index[0]).days
        if total_days > 0:
            annual_return = ((final_capital / initial_capital) ** (365 / total_days) - 1) * 100
        else:
            annual_return = 0

        # 夏普比率 (简化版)
        if len(returns_array) > 1 and np.std(returns_array) > 0:
            sharpe = np.mean(returns_array) / np.std(returns_array) * np.sqrt(365)
        else:
            sharpe = 0

        # 最大回撤
        max_dd = 0
        peak = initial_capital
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # 胜率
        winning_trades = [t for t in self.trades if t.pnl and t.pnl > 0]
        win_rate = len(winning_trades) / len(self.trades) * 100 if self.trades else 0

        # 盈亏比
        avg_profit = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        losing_trades = [t for t in self.trades if t.pnl and t.pnl < 0]
        avg_loss = abs(np.mean([t.pnl for t in losing_trades])) if losing_trades else 1
        profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

        # 买入持有基准
        first_price = df['close_price'].iloc[0]
        last_price = df['close_price'].iloc[-1]
        buy_hold_return = (last_price - first_price) / first_price * 100

        return {
            'direction_accuracy': 50,  # 趋势策略方向准确率不直接适用
            'direction_accuracy_up': 50,
            'direction_accuracy_down': 50,
            'mae': 0,
            'rmse': 0,
            'mape': 0,
            'probability_calibration': 0,
            'trading_return': total_return,
            'trading_annual_return': annual_return,
            'trading_sharpe': sharpe,
            'max_drawdown': max_dd,
            'annual_volatility': np.std(returns_array) * np.sqrt(365) if len(returns_array) > 1 else 0,
            'var_95': np.percentile(returns_array, 5) if len(returns_array) > 0 else 0,
            'profit_loss_ratio': profit_loss_ratio,
            'win_rate': win_rate,
            'total_trades': len(self.trades),
            'buy_hold_return': buy_hold_return,
            'buy_hold_annual_return': buy_hold_return * (365 / total_days) if total_days > 0 else 0,
            'buy_hold_max_drawdown': 0,
            'buy_hold_volatility': 0,
            'buy_hold_sharpe': 0,
            'period_days': total_days
        }

    def get_trade_summary(self) -> pd.DataFrame:
        """获取交易摘要"""
        if not self.trades:
            return pd.DataFrame()

        data = []
        for t in self.trades:
            data.append({
                'entry_date': t.entry_date.strftime('%Y-%m-%d'),
                'exit_date': t.exit_date.strftime('%Y-%m-%d') if t.exit_date else t.entry_date.strftime('%Y-%m-%d'),
                'direction': 'LONG' if t.position == Position.LONG else 'SHORT',
                'entry_price': t.entry_price,
                'exit_price': t.exit_price if t.exit_price is not None else t.entry_price,
                'pnl': (t.pnl_pct if t.pnl_pct is not None else 0),
                'exit_reason': t.exit_reason or ''
            })

        return pd.DataFrame(data)
