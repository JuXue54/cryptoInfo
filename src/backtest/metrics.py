"""
回测指标计算模块
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Any


def calculate_metrics(predictions: List[Dict[str, Any]],
                       strategy_params: Dict[str, Any] = None) -> Dict[str, float]:
    """
    计算回测指标

    Args:
        predictions: 预测记录列表
        strategy_params: 策略参数字典
            - long_threshold: 做多阈值 (默认60)
            - short_threshold: 做空阈值 (默认40)
            - use_position_sizing: 是否使用仓位管理 (默认False)
            - trend_filter: 趋势过滤 ('none', 'bull_only', 'bear_only')

    Returns:
        Dict: 指标字典
    """
    if not predictions:
        return {}

    # 默认参数
    params = {
        'long_threshold': 60,
        'short_threshold': 40,
        'use_position_sizing': False,
        'trend_filter': 'none'
    }
    if strategy_params:
        params.update(strategy_params)

    # 方向准确率
    direction_correct = [p for p in predictions if p['direction_correct']]
    direction_accuracy = len(direction_correct) / len(predictions) * 100

    # 上涨和下跌的分别准确率
    up_predictions = [p for p in predictions if p['predicted_up_probability'] > 50]
    down_predictions = [p for p in predictions if p['predicted_down_probability'] > 50]

    direction_accuracy_up = (
        len([p for p in up_predictions if p['direction_correct']]) / len(up_predictions) * 100
        if up_predictions else 0
    )
    direction_accuracy_down = (
        len([p for p in down_predictions if p['direction_correct']]) / len(down_predictions) * 100
        if down_predictions else 0
    )

    # 误差指标
    errors = [p['actual_future_price'] - p['predicted_price'] for p in predictions]
    abs_errors = [abs(e) for e in errors]
    pct_errors = [
        abs(p['actual_future_price'] - p['predicted_price']) / p['current_price'] * 100
        for p in predictions
    ]

    mae = np.mean(abs_errors)
    rmse = np.sqrt(np.mean([e ** 2 for e in errors]))
    mape = np.mean(pct_errors)

    # 概率校准度（预测概率与实际发生频率的匹配程度）
    probability_calibration = _calculate_probability_calibration(predictions)

    # 交易模拟结果（基于预测方向进行交易，传入策略参数）
    trading_metrics = _simulate_trading(
        predictions,
        long_threshold=params['long_threshold'],
        short_threshold=params['short_threshold'],
        use_position_sizing=params['use_position_sizing'],
        trend_filter=params['trend_filter']
    )

    # 买入持有策略对比
    buy_hold_metrics = _calculate_buy_hold_metrics(predictions)

    return {
        'direction_accuracy': direction_accuracy,
        'direction_accuracy_up': direction_accuracy_up,
        'direction_accuracy_down': direction_accuracy_down,
        'mae': mae,
        'rmse': rmse,
        'mape': mape,
        'probability_calibration': probability_calibration,
        **trading_metrics,
        **buy_hold_metrics
    }


def _calculate_probability_calibration(predictions: List[Dict[str, Any]]) -> float:
    """
    计算概率校准度

    将预测概率分组，看实际发生频率是否与预测概率一致

    Returns:
        float: 校准度得分 (0-100, 越接近100越校准)
    """
    if not predictions:
        return 0

    # 按预测概率分组
    bins = []
    for i in range(10):
        low_prob = i * 10
        high_prob = (i + 1) * 10
        bin_predictions = [
            p for p in predictions
            if low_prob <= p['predicted_up_probability'] < high_prob
            or (high_prob == 100 and p['predicted_up_probability'] == 100)
        ]

        if bin_predictions:
            # 这个组中实际上涨的比例
            actual_up_ratio = len([
                p for p in bin_predictions
                if p['actual_future_price'] > p['current_price']
            ]) / len(bin_predictions) * 100

            # 预测概率的中位数
            predicted_prob = np.median([p['predicted_up_probability'] for p in bin_predictions])

            bins.append(abs(actual_up_ratio - predicted_prob))

    # 平均误差越小，校准度越高
    if bins:
        calibration_error = np.mean(bins)
        return max(0, 100 - calibration_error)
    return 0


def _simulate_trading(predictions: List[Dict[str, Any]],
                       long_threshold: float = 60,
                       short_threshold: float = 40,
                       use_position_sizing: bool = False,
                       trend_filter: str = 'none') -> Dict[str, float]:
    """
    模拟基于预测的交易，支持参数调整

    Args:
        predictions: 预测记录列表
        long_threshold: 做多阈值（概率 > 此值时做多）
        short_threshold: 做空阈值（概率 < 此值时做空）
        use_position_sizing: 是否使用仓位管理
        trend_filter: 趋势过滤 ('none', 'bull_only', 'bear_only')

    Returns:
        Dict: 交易指标
    """
    if not predictions:
        return {'trading_return': 0, 'trading_sharpe': 0, 'max_drawdown': 0}

    returns = []
    positions = []
    position_sizes = []

    # 计算趋势（用于趋势过滤）
    market_trend = _calculate_market_trend(predictions)

    for i, p in enumerate(predictions):
        up_prob = p['predicted_up_probability']
        actual_return = p['actual_return']

        # 基础仓位计算
        if up_prob > long_threshold:
            base_position = 1  # 做多
        elif up_prob < short_threshold:
            base_position = -1  # 做空
        else:
            base_position = 0  # 观望

        # 趋势过滤
        position = base_position
        if trend_filter == 'bull_only' and position == -1:
            # 牛市中禁止做空
            position = 0
        elif trend_filter == 'bear_only' and position == 1:
            # 熊市中禁止做多
            position = 0

        # 仓位管理
        if use_position_sizing and position != 0:
            # 根据概率强度调整仓位：|prob - 50| / 50
            # 概率越极端（远离50%），仓位越大
            confidence = abs(up_prob - 50) / 50
            # 仓位范围：0.2 - 1.0
            position_size = 0.2 + confidence * 0.8
        else:
            position_size = 1.0 if position != 0 else 0

        positions.append(position)
        position_sizes.append(position_size)

        # 持仓收益 = 方向 * 仓位大小 * 实际收益
        trade_return = position * position_size * actual_return
        returns.append(trade_return)

    if not returns:
        return {'trading_return': 0, 'trading_sharpe': 0, 'max_drawdown': 0}

    # 总收益率
    total_return = sum(returns)

    # 夏普比率 (简化版，假设无风险利率为0)
    returns_array = np.array(returns)
    sharpe = np.mean(returns_array) / np.std(returns_array) if np.std(returns_array) > 0 else 0

    # 最大回撤
    cumulative = np.cumsum(returns)
    max_dd = 0
    peak = 0
    for value in cumulative:
        if value > peak:
            peak = value
        drawdown = peak - value
        if drawdown > max_dd:
            max_dd = drawdown

    # 计算年化收益率 (基于预测间隔天数)
    if not predictions:
        annual_return = 0
        annual_volatility = 0
        trading_annual_return = 0
    else:
        # 计算回测期间的总天数
        first_date = pd.to_datetime(predictions[0]['date'])
        last_date = pd.to_datetime(predictions[-1]['date'])
        total_days = (last_date - first_date).days

        if total_days > 0:
            # 策略年化收益率
            trading_annual_return = ((1 + total_return / 100) ** (365 / total_days) - 1) * 100
            # 年化波动率
            annual_volatility = np.std(returns_array) * np.sqrt(365 / total_days * len(returns)) if len(returns) > 1 else 0
        else:
            trading_annual_return = 0
            annual_volatility = 0

        # 风险价值 VaR (95%置信度)
        var_95 = np.percentile(returns_array, 5) if len(returns) > 0 else 0

        # 盈亏比
        positive_returns = [r for r in returns if r > 0]
        negative_returns = [r for r in returns if r < 0]
        profit_loss_ratio = (np.mean(positive_returns) / abs(np.mean(negative_returns))) if negative_returns and positive_returns else 0

        # 胜率
        win_rate = len(positive_returns) / len(returns) * 100 if returns else 0

        return {
            'trading_return': total_return,
            'trading_annual_return': trading_annual_return,
            'trading_sharpe': sharpe,
            'max_drawdown': max_dd,
            'annual_volatility': annual_volatility,
            'var_95': var_95,
            'profit_loss_ratio': profit_loss_ratio,
            'win_rate': win_rate,
            'total_trades': len([p for p in positions if p != 0])
        }


def _calculate_market_trend(predictions: List[Dict[str, Any]]) -> str:
    """
    计算市场趋势

    Args:
        predictions: 预测记录列表

    Returns:
        str: 'bull', 'bear', 或 'neutral'
    """
    if not predictions:
        return 'neutral'

    # 使用第一个和最后一个预测点的价格变化判断趋势
    first_price = predictions[0]['current_price']
    last_price = predictions[-1]['actual_future_price']

    price_change = (last_price - first_price) / first_price * 100

    # 根据总变化判断趋势
    if price_change > 50:  # 期间涨幅超过50%认为是牛市
        return 'bull'
    elif price_change < -30:  # 期间跌幅超过30%认为是熊市
        return 'bear'
    else:
        return 'neutral'


def _calculate_buy_hold_metrics(predictions: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    计算买入持有策略的指标（作为对比基准）

    假设在第一个预测点全仓买入，持有到最后一个预测点

    Returns:
        Dict: 买入持有策略指标
    """
    if not predictions or len(predictions) < 2:
        return {
            'buy_hold_return': 0,
            'buy_hold_annual_return': 0,
            'buy_hold_max_drawdown': 0,
            'buy_hold_volatility': 0,
            'buy_hold_sharpe': 0
        }

    # 第一个点和最后一个点
    first_price = predictions[0]['current_price']
    last_price = predictions[-1]['actual_future_price']

    # 总收益率
    total_return = (last_price - first_price) / first_price * 100

    # 计算期间日收益率序列（用于计算波动率和回撤）
    daily_returns = []
    prices = [p['current_price'] for p in predictions] + [predictions[-1]['actual_future_price']]

    for i in range(1, len(prices)):
        daily_return = (prices[i] - prices[i-1]) / prices[i-1] * 100
        daily_returns.append(daily_return)

    returns_array = np.array(daily_returns)

    # 计算期间天数
    first_date = pd.to_datetime(predictions[0]['date'])
    last_date = pd.to_datetime(predictions[-1]['actual_future_date'] if 'actual_future_date' in predictions[-1] else predictions[-1]['date'])
    total_days = max(1, (last_date - first_date).days)

    # 年化收益率
    annual_return = ((1 + total_return / 100) ** (365 / total_days) - 1) * 100

    # 年化波动率
    annual_volatility = np.std(returns_array) * np.sqrt(365) if len(returns_array) > 1 else 0

    # 夏普比率 (简化版)
    sharpe = np.mean(returns_array) / np.std(returns_array) * np.sqrt(365) if np.std(returns_array) > 0 else 0

    # 最大回撤
    cumulative = np.cumsum(returns_array)
    max_dd = 0
    peak = 0
    for value in cumulative:
        if value > peak:
            peak = value
        drawdown = peak - value
        if drawdown > max_dd:
            max_dd = drawdown

    # 相对于买入持有的超额收益
    strategy_return = sum([p['actual_return'] for p in predictions]) if predictions else 0
    # 注意：这里简化处理，实际应该根据策略持仓计算

    return {
        'buy_hold_return': total_return,
        'buy_hold_annual_return': annual_return,
        'buy_hold_max_drawdown': max_dd,
        'buy_hold_volatility': annual_volatility,
        'buy_hold_sharpe': sharpe,
        'period_days': total_days
    }
