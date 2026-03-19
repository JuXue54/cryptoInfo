"""
回测结果可视化模块
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import Optional, List

from ..strategies.base import BacktestResult


def plot_backtest_result(
    result: BacktestResult,
    save_path: Optional[str] = None,
    show: bool = True
):
    """
    绘制回测结果图表

    Args:
        result: 回测结果
        save_path: 保存路径
        show: 是否显示图表
    """
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(
        f'Backtest Result: {result.strategy_name}\n'
        f'{result.start_date} ~ {result.end_date} ({result.total_predictions} predictions)',
        fontsize=14, fontweight='bold'
    )

    predictions = result.predictions

    # ========== 图1: 方向准确率 ==========
    ax1 = axes[0, 0]

    categories = ['Overall', 'Up\nPrediction', 'Down\nPrediction']
    accuracies = [
        result.direction_accuracy,
        result.direction_accuracy_up,
        result.direction_accuracy_down
    ]
    colors = ['#3498db', '#27ae60', '#e74c3c']

    bars = ax1.bar(categories, accuracies, color=colors, alpha=0.7, edgecolor='black')
    ax1.axhline(y=50, color='gray', linestyle='--', label='Random (50%)')
    ax1.set_ylabel('Accuracy (%)')
    ax1.set_title('Direction Prediction Accuracy')
    ax1.set_ylim(0, 100)
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis='y')

    # 添加数值标签
    for bar, acc in zip(bars, accuracies):
        height = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., height,
                f'{acc:.1f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')

    # ========== 图2: 策略 vs 买入持有收益对比 ==========
    ax2 = axes[0, 1]

    dates = pd.to_datetime([p['date'] for p in predictions])

    # 计算策略累计收益（基于实际交易）
    strategy_returns = []
    buy_hold_returns = []

    # 策略收益：基于预测方向
    for i, p in enumerate(predictions):
        up_prob = p['predicted_up_probability']
        actual_return = p['actual_return']

        if up_prob > 60:
            position = 1  # 做多
        elif up_prob < 40:
            position = -1  # 做空
        else:
            position = 0  # 观望

        strategy_returns.append(position * actual_return)

        # 买入持有收益（简化为每个step的收益）
        buy_hold_returns.append(actual_return)

    # 累计收益
    strategy_cumulative = np.cumsum(strategy_returns)
    buy_hold_cumulative = np.cumsum(buy_hold_returns)

    ax2.plot(dates, strategy_cumulative, 'g-', linewidth=2, label=f'Strategy: {strategy_cumulative[-1]:.1f}%', marker='o', markersize=4)
    ax2.plot(dates, buy_hold_cumulative, 'b--', linewidth=2, label=f'Buy & Hold: {buy_hold_cumulative[-1]:.1f}%', marker='s', markersize=4)
    ax2.axhline(y=0, color='gray', linestyle=':', alpha=0.5)

    ax2.set_xlabel('Date')
    ax2.set_ylabel('Cumulative Return (%)')
    ax2.set_title('Strategy vs Buy & Hold Performance')
    ax2.legend(loc='upper left')
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    # ========== 图3: 预测 vs 实际散点图 ==========
    ax3 = axes[1, 0]

    actual_future_prices = [p['actual_future_price'] for p in predictions]
    predicted_prices = [p['predicted_price'] for p in predictions]

    ax3.scatter(predicted_prices, actual_future_prices, alpha=0.6, s=50, color='purple')

    # 添加完美预测线
    min_price = min(min(actual_future_prices), min(predicted_prices))
    max_price = max(max(actual_future_prices), max(predicted_prices))
    ax3.plot([min_price, max_price], [min_price, max_price], 'r--', linewidth=2, label='Perfect Prediction')

    # 添加误差区间
    mae = result.mae
    ax3.plot([min_price, max_price], [min_price + mae, max_price + mae], 'g:', alpha=0.5, label=f'+/- MAE (${mae:,.0f})')
    ax3.plot([min_price, max_price], [min_price - mae, max_price - mae], 'g:', alpha=0.5)

    ax3.set_xlabel('Predicted Price ($)')
    ax3.set_ylabel('Actual Price ($)')
    ax3.set_title('Predicted vs Actual Prices')
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    # ========== 图4: 回测指标摘要 ==========
    ax4 = axes[1, 1]
    ax4.axis('off')

    # 计算超额收益
    excess_return = result.trading_return - result.buy_hold_return
    excess_annual = result.trading_annual_return - result.buy_hold_annual_return

    # 创建摘要表格
    summary_text = f"""
    +{'='*50}+
    |           BACKTEST SUMMARY                     |
    +{'='*50}+
    |  Strategy:          {result.strategy_name:<28} |
    |  Period:            {result.start_date} to {result.end_date}    |
    |  Duration:          {result.period_days:<28} days |
    |  Total Predictions: {result.total_predictions:<28} |
    |  Forecast Days:     {result.forecast_days:<28} |
    +{'='*50}+
    |  PERFORMANCE COMPARISON                        |
    +{'='*50}+
    |                    Strategy    Buy & Hold  Diff  |
    +{'='*50}+
    |  Total Return:     {result.trading_return:>+10.2f}%  {result.buy_hold_return:>+10.2f}%  {excess_return:>+6.2f}% |
    |  Annual Return:    {result.trading_annual_return:>+10.2f}%  {result.buy_hold_annual_return:>+10.2f}%  {excess_annual:>+6.2f}% |
    |  Max Drawdown:     {result.max_drawdown:>10.2f}%  {result.buy_hold_max_drawdown:>10.2f}%        |
    |  Sharpe Ratio:     {result.trading_sharpe:>10.2f}   {result.buy_hold_sharpe:>10.2f}          |
    +{'='*50}+
    |  ACCURACY METRICS                              |
    +{'='*50}+
    |  Direction Accuracy:        {result.direction_accuracy:>10.2f}%       |
    |  Up Prediction Accuracy:    {result.direction_accuracy_up:>10.2f}%       |
    |  Down Prediction Accuracy:  {result.direction_accuracy_down:>10.2f}%       |
    |  Win Rate:                  {result.win_rate:>10.2f}%       |
    +{'='*50}+
    |  RISK METRICS                                  |
    +{'='*50}+
    |  Volatility (Ann.):  {result.annual_volatility:>10.2f}%                    |
    |  VaR 95%:           {result.var_95:>10.2f}%                    |
    |  Profit/Loss Ratio: {result.profit_loss_ratio:>10.2f}                     |
    |  Total Trades:      {result.total_trades:>10}                       |
    +{'='*50}+
    |  ERROR METRICS                                 |
    +{'='*50}+
    |  MAE:   ${result.mae:>12,.2f}                        |
    |  RMSE:  ${result.rmse:>12,.2f}                        |
    |  MAPE:  {result.mape:>11.2f}%                        |
    +{'='*50}+
    """

    ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes, fontsize=9,
             verticalalignment='top', fontfamily='monospace',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Backtest chart saved to: {save_path}")

    if show:
        plt.show()

    plt.close()


def plot_strategy_comparison(
    results: List[BacktestResult],
    save_path: Optional[str] = None,
    show: bool = True
):
    """
    比较多个策略的回测结果

    Args:
        results: 回测结果列表
        save_path: 保存路径
        show: 是否显示图表
    """
    if not results:
        return

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle('Strategy Comparison', fontsize=16, fontweight='bold')

    strategies = [r.strategy_name for r in results]

    # ========== 图1: 方向准确率对比 ==========
    ax1 = axes[0, 0]
    x = np.arange(len(strategies))
    width = 0.25

    accuracies = [r.direction_accuracy for r in results]
    accuracies_up = [r.direction_accuracy_up for r in results]
    accuracies_down = [r.direction_accuracy_down for r in results]

    ax1.bar(x - width, accuracies, width, label='Overall', color='#3498db')
    ax1.bar(x, accuracies_up, width, label='Up', color='#27ae60')
    ax1.bar(x + width, accuracies_down, width, label='Down', color='#e74c3c')

    ax1.set_ylabel('Accuracy (%)')
    ax1.set_title('Direction Accuracy Comparison')
    ax1.set_xticks(x)
    ax1.set_xticklabels(strategies)
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis='y')

    # ========== 图2: 误差指标对比 ==========
    ax2 = axes[0, 1]

    mape_values = [r.mape for r in results]
    colors = plt.cm.RdYlGn_r(np.linspace(0.2, 0.8, len(results)))

    bars = ax2.bar(strategies, mape_values, color=colors, edgecolor='black')
    ax2.set_ylabel('MAPE (%)')
    ax2.set_title('Mean Absolute Percentage Error (Lower is Better)')
    ax2.grid(True, alpha=0.3, axis='y')

    for bar, mape in zip(bars, mape_values):
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height,
                f'{mape:.2f}%', ha='center', va='bottom', fontsize=10)

    # ========== 图3: 交易收益对比 ==========
    ax3 = axes[1, 0]

    returns = [r.trading_return for r in results]
    colors = ['#27ae60' if r > 0 else '#e74c3c' for r in returns]

    bars = ax3.bar(strategies, returns, color=colors, edgecolor='black', alpha=0.7)
    ax3.axhline(y=0, color='black', linestyle='-', linewidth=1)
    ax3.set_ylabel('Total Return (%)')
    ax3.set_title('Trading Simulation Returns')
    ax3.grid(True, alpha=0.3, axis='y')

    for bar, ret in zip(bars, returns):
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width()/2., height,
                f'{ret:+.2f}%', ha='center',
                va='bottom' if height > 0 else 'top', fontsize=10, fontweight='bold')

    # ========== 图4: 综合评分雷达图 ==========
    ax4 = axes[1, 1]

    # 计算综合评分
    metrics = ['Accuracy', 'Low Error', 'Return', 'Sharpe']
    scores = []

    for r in results:
        # 归一化各个指标到0-100
        acc_score = r.direction_accuracy
        error_score = max(0, 100 - r.mape * 10)  # MAPE越低分数越高
        return_score = min(100, max(0, 50 + r.trading_return))  # 收益归一化
        sharpe_score = min(100, max(0, 50 + r.trading_sharpe * 20))  # 夏普比率归一化

        scores.append([acc_score, error_score, return_score, sharpe_score])

    # 绘制柱状图
    x = np.arange(len(metrics))
    width = 0.8 / len(results)

    for i, (strategy, score) in enumerate(zip(strategies, scores)):
        ax4.bar(x + i * width, score, width, label=strategy, alpha=0.8)

    ax4.set_ylabel('Score')
    ax4.set_title('Comprehensive Score Comparison')
    ax4.set_xticks(x + width * (len(results) - 1) / 2)
    ax4.set_xticklabels(metrics)
    ax4.legend()
    ax4.grid(True, alpha=0.3, axis='y')
    ax4.set_ylim(0, 100)

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Comparison chart saved to: {save_path}")

    if show:
        plt.show()

    plt.close()


def generate_backtest_report(result: BacktestResult) -> str:
    """
    生成回测报告文本

    Args:
        result: 回测结果

    Returns:
        str: 报告文本
    """
    report = f"""
╔══════════════════════════════════════════════════════════════╗
║                     BACKTEST REPORT                          ║
╠══════════════════════════════════════════════════════════════╣
║  Strategy: {result.strategy_name:<50} ║
║  Period:   {result.start_date} to {result.end_date:<29} ║
║  Predictions: {result.total_predictions:<46} ║
╠══════════════════════════════════════════════════════════════╣
║  DIRECTION ACCURACY                                          ║
╠══════════════════════════════════════════════════════════════╣
║  Overall:           {result.direction_accuracy:>10.2f}%                             ║
║  Up Predictions:    {result.direction_accuracy_up:>10.2f}%                             ║
║  Down Predictions:  {result.direction_accuracy_down:>10.2f}%                             ║
╠══════════════════════════════════════════════════════════════╣
║  ERROR METRICS                                               ║
╠══════════════════════════════════════════════════════════════╣
║  MAE:   ${result.mae:>12,.2f}                                  ║
║  RMSE:  ${result.rmse:>12,.2f}                                  ║
║  MAPE:  {result.mape:>11.2f}%                                  ║
╠══════════════════════════════════════════════════════════════╣
║  TRADING SIMULATION                                          ║
╠══════════════════════════════════════════════════════════════╣
║  Total Return:      {result.trading_return:>+10.2f}%                             ║
║  Sharpe Ratio:      {result.trading_sharpe:>10.2f}                              ║
║  Max Drawdown:      {result.max_drawdown:>10.2f}%                             ║
╚══════════════════════════════════════════════════════════════╝
"""
    return report
