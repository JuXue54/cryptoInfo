
# 策略系统使用说明

## 概述

本项目现在支持多种交易策略，从简单的蒙特卡洛预测到复杂的趋势跟踪和均值回归策略。

## 策略类型

### 1. 蒙特卡洛策略 (MonteCarloStrategy)

基于几何布朗运动的随机模拟策略。

```python
from src.strategies import MonteCarloStrategy

strategy = MonteCarloStrategy(
    n_simulations=10000,      # 模拟次数
    use_trend_adjustment=True,  # 使用趋势调整
    confidence_level=0.90     # 置信水平
)
```

**适用场景**: 了解价格可能的分布范围
**缺点**: 难以跑赢趋势市场

---

### 2. 趋势跟踪策略 (TrendFollowingStrategy)

基于多时间框架动量的趋势跟踪策略。

```python
from src.strategies import TrendFollowingStrategy

strategy = TrendFollowingStrategy(
    short_window=7,      # 短期窗口
    medium_window=30,    # 中期窗口
    long_window=90,      # 长期窗口
    volatility_lookback=30
)
```

**适用场景**: 强趋势市场（牛市/熊市）
**特点**: 让利润奔跑，截断亏损

---

### 3. 均值回归策略 (MeanReversionStrategy)

基于RSI和布林带的均值回归策略。

```python
from src.strategies import MeanReversionStrategy

strategy = MeanReversionStrategy(
    rsi_period=14,
    rsi_overbought=70,
    rsi_oversold=30,
    bb_period=20,
    bb_std=2.0
)
```

**适用场景**: 震荡市场
**特点**: 高抛低吸，反人性交易

---

### 4. 策略组合器 (EnsembleStrategy)

组合多个策略的预测结果。

```python
from src.strategies import EnsembleStrategy, TrendFollowingStrategy, MeanReversionStrategy

strategy = EnsembleStrategy(
    strategies=[
        TrendFollowingStrategy(),
        MeanReversionStrategy()
    ],
    weights=[0.6, 0.4],           # 权重
    voting_method='weighted_average'  # 投票方法
)
```

**投票方法**:
- `weighted_average`: 加权平均
- `majority`: 多数投票
- `confidence`: 置信度自适应加权

---

### 5. 状态感知策略 (RegimeAwareStrategy)

根据市场状态自动切换策略。

```python
from src.strategies import RegimeAwareStrategy, TrendFollowingStrategy, MeanReversionStrategy

strategy = RegimeAwareStrategy(
    trend_strategy=TrendFollowingStrategy(),      # 趋势市场使用
    range_strategy=MeanReversionStrategy(),       # 震荡市场使用
    adx_threshold=25                              # 趋势/震荡阈值
)
```

---

## 回测引擎

### 原始回测引擎 (BacktestEngine)

离散交易，每个预测周期独立交易。

```python
from src.backtest import BacktestEngine

engine = BacktestEngine(strategy)
result = engine.run_backtest(
    df,
    forecast_days=7,
    step_days=7,
    strategy_params={
        'long_threshold': 60,
        'short_threshold': 40
    }
)
```

### 增强回测引擎 (EnhancedBacktestEngine)

支持连续持仓、复利计算、止损止盈。

```python
from src.backtest import EnhancedBacktestEngine

engine = EnhancedBacktestEngine(strategy)
result = engine.run_backtest(
    df,
    initial_capital=10000,
    position_size=0.8,
    use_compound=True,           # 复利
    stop_loss_pct=10,            # 10%止损
    take_profit_pct=20,          # 20%止盈
    trailing_stop_pct=8,         # 移动止损
    max_position_hold_days=30,   # 最大持仓天数
    rebalance_freq='daily'       # 再平衡频率
)

# 获取交易记录
trade_summary = engine.get_trade_summary()
print(trade_summary)
```

---

## 快速开始

运行策略对比测试:

```bash
python backtest_comparison.py
```

这将对比所有策略的表现，并生成图表。

---

## 策略选择建议

| 市场环境 | 推荐策略 | 理由 |
|---------|---------|------|
| 强牛市 | TrendFollowing | 跟随趋势，不逆势 |
| 强熊市 | TrendFollowing | 可以做空 |
| 震荡市 | MeanReversion | 高抛低吸 |
| 不确定 | Ensemble/RegimeAware | 分散风险 |

---

## 跑赢HODL的关键

1. **连续持仓**: 使用 `EnhancedBacktestEngine` 而不是离散交易
2. **复利效应**: 设置 `use_compound=True`
3. **风险控制**: 设置合理的止损（10-15%）
4. **动态仓位**: 根据信号强度调整仓位大小
5. **趋势过滤**: 在强趋势市避免逆势交易
