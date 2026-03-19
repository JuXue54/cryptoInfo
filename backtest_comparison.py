"""
策略对比测试脚本

对比不同策略 vs 买入持有的表现
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, 'E:\\Users\\Admin\\claudeProjects\\cryptoInfo')

from src.strategies import (
    MonteCarloStrategy,
    TrendFollowingStrategy,
    MeanReversionStrategy,
    EnsembleStrategy,
    RegimeAwareStrategy
)
from src.backtest import BacktestEngine, EnhancedBacktestEngine
from src.backtest.visualization import plot_backtest_result, plot_strategy_comparison
from src.database import Database


def load_data(symbol='BTC', days=1000):
    """加载历史数据"""
    db = Database()
    df = db.get_price_data(symbol, days=days)
    return df


def run_original_backtest(df, strategy):
    """运行原始回测（离散交易）"""
    engine = BacktestEngine(strategy)
    result = engine.run_backtest(
        df,
        forecast_days=7,
        step_days=7,
        strategy_params={
            'long_threshold': 60,
            'short_threshold': 40,
            'use_position_sizing': True,
            'trend_filter': 'none'
        }
    )
    return result


def run_enhanced_backtest(df, strategy):
    """运行增强回测（连续持仓，复利）"""
    engine = EnhancedBacktestEngine(strategy)
    result = engine.run_backtest(
        df,
        initial_capital=10000,
        position_size=0.8,
        use_compound=True,
        stop_loss_pct=10,      # 10%止损
        take_profit_pct=20,    # 20%止盈
        trailing_stop_pct=8,   # 8%移动止损
        rebalance_freq='daily',
        strategy_params={
            'use_position_sizing': True
        }
    )
    return result, engine


def compare_strategies(df):
    """对比多种策略"""
    print("=" * 70)
    print("策略对比测试")
    print("=" * 70)

    # 定义策略
    strategies = {
        'MonteCarlo': MonteCarloStrategy(n_simulations=5000),
        'TrendFollowing': TrendFollowingStrategy(),
        'MeanReversion': MeanReversionStrategy(),
        'Ensemble': EnsembleStrategy(
            strategies=[
                TrendFollowingStrategy(),
                MeanReversionStrategy(),
                MonteCarloStrategy(n_simulations=2000)
            ],
            weights=[0.4, 0.3, 0.3],
            voting_method='weighted_average'
        ),
        'RegimeAware': RegimeAwareStrategy(
            trend_strategy=TrendFollowingStrategy(),
            range_strategy=MeanReversionStrategy(),
            adx_threshold=25
        )
    }

    results = {}
    engines = {}

    for name, strategy in strategies.items():
        print(f"\n{'='*50}")
        print(f"测试策略: {name}")
        print('='*50)

        try:
            # 增强版回测
            result, engine = run_enhanced_backtest(df, strategy)
            results[name] = result
            engines[name] = engine

            print(f"总收益: {result.trading_return:+.2f}%")
            print(f"年化收益: {result.trading_annual_return:+.2f}%")
            print(f"买入持有: {result.buy_hold_return:+.2f}%")
            print(f"超额收益: {result.trading_return - result.buy_hold_return:+.2f}%")
            print(f"夏普比率: {result.trading_sharpe:.2f}")
            print(f"最大回撤: {result.max_drawdown:.2f}%")
            print(f"胜率: {result.win_rate:.1f}%")
            print(f"盈亏比: {result.profit_loss_ratio:.2f}")
            print(f"交易次数: {result.total_trades}")

            # 打印交易摘要
            trade_summary = engine.get_trade_summary()
            if not trade_summary.empty:
                print("\n最近5笔交易:")
                print(trade_summary.tail().to_string())

        except Exception as e:
            print(f"回测失败: {e}")
            import traceback
            traceback.print_exc()

    # 汇总对比
    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"{'策略':<15} {'总收益':>10} {'年化':>10} {'B&H':>10} {'超额':>10} {'夏普':>8} {'回撤':>8}")
    print("-" * 70)

    for name, result in results.items():
        excess = result.trading_return - result.buy_hold_return
        print(f"{name:<15} {result.trading_return:>+9.1f}% {result.trading_annual_return:>+9.1f}% "
              f"{result.buy_hold_return:>+9.1f}% {excess:>+9.1f}% {result.trading_sharpe:>8.2f} "
              f"{result.max_drawdown:>7.1f}%")

    return results, engines


def optimize_parameters(df):
    """参数优化示例"""
    print("\n" + "=" * 70)
    print("参数优化: 止损/止盈")
    print("=" * 70)

    strategy = TrendFollowingStrategy()
    best_result = None
    best_params = None
    best_return = -float('inf')

    # 网格搜索
    for sl in [5, 10, 15]:
        for tp in [10, 20, 30]:
            for ts in [5, 8, 12]:
                engine = EnhancedBacktestEngine(strategy)
                result = engine.run_backtest(
                    df,
                    initial_capital=10000,
                    position_size=0.8,
                    stop_loss_pct=sl,
                    take_profit_pct=tp,
                    trailing_stop_pct=ts,
                    rebalance_freq='daily'
                )

                excess = result.trading_return - result.buy_hold_return
                if result.trading_return > best_return:
                    best_return = result.trading_return
                    best_result = result
                    best_params = {'sl': sl, 'tp': tp, 'ts': ts}

                print(f"SL={sl}% TP={tp}% TS={ts}%: 收益={result.trading_return:+.1f}%, "
                      f"超额={excess:+.1f}%, 夏普={result.trading_sharpe:.2f}")

    print(f"\n最优参数: SL={best_params['sl']}%, TP={best_params['tp']}%, TS={best_params['ts']}%")
    print(f"最优收益: {best_result.trading_return:+.2f}%")
    print(f"买入持有: {best_result.buy_hold_return:+.2f}%")

    return best_params


if __name__ == '__main__':
    print("加载数据...")
    df = load_data(days=1000)
    print(f"数据范围: {df.index[0]} ~ {df.index[-1]}, 共{len(df)}条")

    # 运行对比
    results, engines = compare_strategies(df)

    # 参数优化（可选，比较耗时）
    # optimize_parameters(df)

    # 可视化第一个策略
    if results:
        first_strategy = list(results.keys())[0]
        print(f"\n生成{first_strategy}策略图表...")
        plot_backtest_result(
            results[first_strategy],
            save_path=f'backtest_{first_strategy}.png',
            show=False
        )

        # 如果有多个策略，生成对比图
        if len(results) > 1:
            print("生成策略对比图表...")
            plot_strategy_comparison(
                list(results.values()),
                save_path='strategy_comparison.png',
                show=False
            )

    print("\n完成!")
