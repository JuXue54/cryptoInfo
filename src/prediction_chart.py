"""预测结果可视化模块"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from typing import Optional

from src.btc_predictor import PredictionResult


def plot_prediction(
    result: PredictionResult,
    historical_days: int = 60,
    save_path: Optional[str] = None,
    show: bool = True
):
    """
    绘制预测结果图表

    Args:
        result: 预测结果
        historical_days: 显示多少天的历史数据
        save_path: 保存路径
        show: 是否显示图表
    """
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'BTC Price Prediction - Next {result.forecast_days} Days', fontsize=16, fontweight='bold')

    # 生成日期序列
    future_dates = [datetime.now() + timedelta(days=i+1) for i in range(result.forecast_days)]

    # ========== 图1: 价格路径模拟 ==========
    ax1 = axes[0, 0]

    # 绘制部分模拟路径（只显示100条避免图表过于拥挤）
    n_display = min(100, result.simulation_paths.shape[1])
    for i in range(n_display):
        ax1.plot(future_dates, result.simulation_paths[:, i], alpha=0.1, color='blue')

    # 绘制均值路径
    mean_path = np.mean(result.simulation_paths, axis=1)
    ax1.plot(future_dates, mean_path, 'r-', linewidth=2, label='Expected Price')

    # 绘制置信区间
    p5 = np.percentile(result.simulation_paths, 5, axis=1)
    p95 = np.percentile(result.simulation_paths, 95, axis=1)
    ax1.fill_between(future_dates, p5, p95, alpha=0.3, color='green', label='90% Confidence Interval')

    # 标记当前价格
    ax1.axhline(y=result.current_price, color='gray', linestyle='--', alpha=0.7, label=f'Current: ${result.current_price:,.0f}')

    ax1.set_title('Monte Carlo Simulation Paths')
    ax1.set_xlabel('Date')
    ax1.set_ylabel('Price (USD)')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))

    # ========== 图2: 最终价格分布 ==========
    ax2 = axes[0, 1]

    final_prices = result.simulation_paths[-1]

    # 绘制直方图
    ax2.hist(final_prices, bins=50, color='skyblue', edgecolor='black', alpha=0.7, density=True)

    # 标记关键价格点
    ax2.axvline(x=result.current_price, color='gray', linestyle='--', linewidth=2, label=f'Current: ${result.current_price:,.0f}')
    ax2.axvline(x=result.predicted_price_mean, color='red', linestyle='-', linewidth=2, label=f'Mean: ${result.predicted_price_mean:,.0f}')
    ax2.axvline(x=result.predicted_price_median, color='green', linestyle='-', linewidth=2, label=f'Median: ${result.predicted_price_median:,.0f}')
    ax2.axvline(x=result.confidence_interval_5, color='orange', linestyle=':', linewidth=2, label=f'5%: ${result.confidence_interval_5:,.0f}')
    ax2.axvline(x=result.confidence_interval_95, color='orange', linestyle=':', linewidth=2, label=f'95%: ${result.confidence_interval_95:,.0f}')

    ax2.set_title(f'Price Distribution After {result.forecast_days} Days')
    ax2.set_xlabel('Price (USD)')
    ax2.set_ylabel('Probability Density')
    ax2.legend(loc='upper left')
    ax2.grid(True, alpha=0.3)

    # ========== 图3: 概率信息展示 ==========
    ax3 = axes[1, 0]
    ax3.axis('off')

    # 创建文本信息
    signals = result.technical_signals

    info_text = f"""
    ╔══════════════════════════════════════════════════════════════╗
    ║                    PREDICTION SUMMARY                        ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Current Price:        ${result.current_price:>12,.2f}                     ║
    ║  Expected Price:       ${result.predicted_price_mean:>12,.2f}                     ║
    ║  Expected Return:      {result.expected_return:>+11.2f}%                      ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  PROBABILITY DISTRIBUTION:                                   ║
    ║    Up Probability:      {result.up_probability:>10.1f}%                        ║
    ║    Down Probability:    {result.down_probability:>10.1f}%                        ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  CONFIDENCE INTERVAL:                                        ║
    ║    5%  Percentile:     ${result.confidence_interval_5:>12,.2f}                     ║
    ║    95% Percentile:     ${result.confidence_interval_95:>12,.2f}                     ║
    ║    Risk/Reward Ratio:  {result.risk_reward_ratio:>13.2f}                     ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  TECHNICAL INDICATORS:                                       ║
    ║    RSI (14):           {signals['rsi']:>13.2f} ({signals['rsi_signal']:<10})    ║
    ║    MACD:               {signals['macd']:>13.2f}                      ║
    ║    MA Trend:           {signals['ma_trend']:>13}                      ║
    ║    BB Position:        {signals['bb_position']*100:>12.1f}%                      ║
    ║    Volatility (Ann.):  {signals['volatility_annual']*100:>12.1f}%                      ║
    ║    7-Day Change:       {signals['price_change_7d']:>+11.2f}%                      ║
    ║    30-Day Change:      {signals['price_change_30d']:>+11.2f}%                      ║
    ║    Composite Score:    {signals['composite_score']:>+13.0f}                      ║
    ╚══════════════════════════════════════════════════════════════╝
    """

    ax3.text(0.05, 0.95, info_text, transform=ax3.transAxes, fontsize=10,
             verticalalignment='top', fontfamily='monospace',
             bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    # ========== 图4: 涨跌概率饼图 ==========
    ax4 = axes[1, 1]

    labels = [f'Up\n{result.up_probability:.1f}%', f'Down\n{result.down_probability:.1f}%']
    sizes = [result.up_probability, result.down_probability]
    colors = ['#4CAF50', '#F44336']  # 绿色和红色
    explode = (0.05, 0.05)

    wedges, texts, autotexts = ax4.pie(sizes, explode=explode, labels=labels, colors=colors,
                                        autopct='', startangle=90, textprops={'fontsize': 12})

    # 根据概率显示趋势
    if result.up_probability > 60:
        trend_text = 'BULLISH'
        trend_color = '#4CAF50'
    elif result.down_probability > 60:
        trend_text = 'BEARISH'
        trend_color = '#F44336'
    else:
        trend_text = 'NEUTRAL'
        trend_color = '#FFC107'

    ax4.text(0, -0.15, trend_text, ha='center', fontsize=20, fontweight='bold',
             color=trend_color, transform=ax4.transAxes)

    ax4.set_title('Up vs Down Probability', fontsize=14, fontweight='bold')

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Prediction chart saved to: {save_path}")

    if show:
        plt.show()

    plt.close()


if __name__ == '__main__':
    # 测试可视化
    from src.btc_predictor import BTCPredictor

    predictor = BTCPredictor(forecast_days=7, n_simulations=5000)
    result = predictor.predict(use_trend=True)

    plot_prediction(result, save_path='btc_prediction.png', show=True)
