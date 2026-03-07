"""图表模块 - 使用mplfinance绘制K线图"""
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
from typing import Optional, List
import numpy as np

from config import CHART_STYLE, CHART_FIGSIZE, MA_PERIODS
from src.aggregator import DataAggregator


class ChartPlotter:
    """K线图绘制器"""

    def __init__(self, style: str = CHART_STYLE, figsize: tuple = CHART_FIGSIZE):
        self.style = style
        self.figsize = figsize

    def plot(self, df: pd.DataFrame, title: str = "K线图",
             timeframe: str = 'day',
             ma_periods: Optional[List[int]] = None,
             log_scale: bool = False,
             volume: bool = False,
             save_path: Optional[str] = None,
             show: bool = True):
        """
        绘制K线图

        Args:
            df: 价格DataFrame，包含open_price, close_price, max_price, min_price
            title: 图表标题
            timeframe: 时间周期，'day'日线, 'week'周线, 'month'月线
            ma_periods: 均线周期列表，如[5, 10, 20]，None则不显示均线
            log_scale: 是否使用对数坐标轴
            volume: 是否显示成交量（当前版本不支持，预留）
            save_path: 保存图片路径，None则显示图表
            show: 是否显示图表
        """
        if df.empty:
            print("没有数据可以绘制")
            return

        # 根据时间周期聚合数据
        agg_df = DataAggregator.get_data_by_timeframe(df, timeframe)

        if agg_df.empty:
            print(f"{timeframe}周期数据为空")
            return

        # 添加均线（在列重命名之前）
        if ma_periods:
            agg_df = DataAggregator.add_moving_averages(agg_df, ma_periods)

        # 准备数据
        chart_df = DataAggregator.prepare_for_chart(agg_df)

        # 构建标题
        timeframe_names = {'day': '日K', 'week': '周K', 'month': '月K'}
        timeframe_name = timeframe_names.get(timeframe, 'K线')
        full_title = f"{title} {timeframe_name}"

        if log_scale:
            full_title += " (对数坐标)"

        # 准备绘图数据
        plot_df = chart_df[['Open', 'High', 'Low', 'Close']].copy()

        # 对数坐标需要正数数据
        if log_scale:
            for col in ['Open', 'High', 'Low', 'Close']:
                invalid_count = (plot_df[col] <= 0).sum()
                if invalid_count > 0:
                    print(f"警告: {col} 包含 {invalid_count} 个非正值，已过滤")
            plot_df = plot_df[(plot_df > 0).all(axis=1)]

        # 创建图表
        fig, axes = mpf.plot(
            plot_df,
            type='candle',
            style=self.style,
            title=full_title,
            ylabel='价格 (USDT)',
            yscale='log' if log_scale else 'linear',
            figsize=self.figsize,
            returnfig=True,
            tight_layout=True,
        )

        ax = axes[0]

        # 添加均线
        if ma_periods:
            colors = ['orange', 'blue', 'red', 'green', 'purple', 'brown', 'pink', 'gray', 'olive', 'cyan']
            x_positions = range(len(chart_df))

            for i, period in enumerate(ma_periods):
                ma_col = f'MA{period}'
                if ma_col in chart_df.columns:
                    color = colors[i % len(colors)]
                    ax.plot(x_positions, chart_df[ma_col],
                           color=color, linewidth=1.5, label=f'MA{period}')

        # 对数坐标下设置友好的刻度格式
        if log_scale:
            import matplotlib.ticker as mticker
            from matplotlib.ticker import LogLocator

            def price_formatter(x, pos):
                if x >= 1000:
                    return f'{int(x):,}'
                elif x >= 1:
                    return f'{x:.2f}'
                else:
                    return f'{x:.4f}'

            ax.yaxis.set_major_formatter(mticker.FuncFormatter(price_formatter))

            # 设置刻度定位器，使用 subs 参数在10的幂之间插入更多刻度
            # 例如：在10000和100000之间显示20000, 30000, ..., 90000
            ax.yaxis.set_major_locator(LogLocator(base=10.0, subs=np.arange(1, 10), numticks=12))

        # 添加图例
        if ma_periods:
            ax.legend(loc='upper left', fontsize=10)

        # 保存或显示
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"图表已保存到: {save_path}")

        if show:
            plt.show()

        plt.close()

    def plot_with_moving_averages(self, df: pd.DataFrame, title: str = "K线图",
                                   ma_periods: Optional[List[int]] = None,
                                   **kwargs):
        """
        绘制带均线的K线图

        Args:
            df: 价格DataFrame
            title: 图表标题
            ma_periods: 均线周期列表，默认使用配置中的常用周期
            **kwargs: 其他plot参数
        """
        if ma_periods is None:
            ma_periods = [20, 60]

        return self.plot(df, title=title, ma_periods=ma_periods, **kwargs)

    def plot_comparison(self, df1: pd.DataFrame, df2: pd.DataFrame,
                        label1: str, label2: str,
                        title: str = "价格对比",
                        normalize: bool = True,
                        log_scale: bool = False,
                        save_path: Optional[str] = None,
                        show: bool = True):
        """
        绘制两个币种的价格对比图
        """
        if df1.empty or df2.empty:
            print("数据不足，无法绘制对比图")
            return

        fig, ax = plt.subplots(figsize=self.figsize)

        price1 = df1['close_price'].copy()
        price2 = df2['close_price'].copy()

        if normalize:
            price1 = price1 / price1.iloc[0] * 100
            price2 = price2 / price2.iloc[0] * 100
            ylabel = '归一化价格 (起始日=100)'
        else:
            ylabel = '价格 (USDT)'

        ax.plot(price1.index, price1, label=label1, linewidth=1.5)
        ax.plot(price2.index, price2, label=label2, linewidth=1.5)

        ax.set_title(title)
        ax.set_xlabel('日期')
        ax.set_ylabel(ylabel)

        if log_scale:
            ax.set_yscale('log')
            import matplotlib.ticker as mticker
            def price_formatter(x, pos):
                if x >= 1000:
                    return f'{int(x):,}'
                else:
                    return f'{x:.2f}'
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(price_formatter))

        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"图表已保存到: {save_path}")

        if show:
            plt.show()

        plt.close()
