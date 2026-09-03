"""BTC价格预测模块

使用技术指标分析和蒙特卡洛模拟预测BTC未来走势
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass

from src.database import Database


@dataclass
class PredictionResult:
    """预测结果数据类"""
    current_price: float
    predicted_price_mean: float
    predicted_price_median: float
    up_probability: float  # 上涨概率
    down_probability: float  # 下跌概率
    confidence_interval_5: float  # 5%分位数
    confidence_interval_95: float  # 95%分位数
    expected_return: float  # 预期收益率
    risk_reward_ratio: float  # 风险收益比
    simulation_paths: np.ndarray  # 模拟路径（用于可视化）
    forecast_days: int  # 预测天数
    technical_signals: Dict[str, any]  # 技术指标信号


class BTCPredictor:
    """BTC价格预测器"""

    def __init__(self, forecast_days: int = 7, n_simulations: int = 10000,
                 asset_code: str = 'BTC', db: Optional[Database] = None,
                 currency_code: str = 'USDT'):
        """
        初始化预测器

        Args:
            forecast_days: 预测天数，默认7天（一周）
            n_simulations: 蒙特卡洛模拟次数，默认10000次
            asset_code: 资产代码，默认BTC
            db: 可复用的数据库实例（不传则自建）
            currency_code: 计价货币
        """
        self.forecast_days = forecast_days
        self.n_simulations = n_simulations
        self.asset_code = asset_code.upper()
        self.currency_code = currency_code.upper()
        self.db = db if db is not None else Database()

    def fetch_data(self, days: int = 365) -> pd.DataFrame:
        """
        获取历史价格数据

        Args:
            days: 获取最近多少天的数据

        Returns:
            DataFrame with OHLCV data
        """
        now = datetime.now(timezone.utc)
        end_date = now.strftime('%Y-%m-%d')
        start_date = (now - timedelta(days=days)).strftime('%Y-%m-%d')

        df = self.db.get_price_data(self.asset_code, self.currency_code, start_date, end_date)
        return df

    def calculate_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算收益率"""
        df = df.copy()
        # 日收益率
        df['daily_return'] = df['close_price'].pct_change()
        # 对数收益率（更适合统计建模）
        df['log_return'] = np.log(df['close_price'] / df['close_price'].shift(1))
        return df

    def calculate_technical_indicators(self, df: pd.DataFrame) -> Dict[str, any]:
        """
        计算技术指标

        Returns:
            技术指标信号字典
        """
        df = df.copy()
        close = df['close_price']
        high = df['max_price']
        low = df['min_price']

        signals = {}

        # 1. 移动平均线 (MA)
        df['MA7'] = close.rolling(window=7).mean()
        df['MA30'] = close.rolling(window=30).mean()
        df['MA60'] = close.rolling(window=60).mean()

        # 均线信号：短期均线上穿长期均线为买入信号
        signals['ma_trend'] = 'bullish' if df['MA7'].iloc[-1] > df['MA30'].iloc[-1] else 'bearish'
        signals['ma_golden_cross'] = df['MA7'].iloc[-1] > df['MA30'].iloc[-1] and df['MA7'].iloc[-5] <= df['MA30'].iloc[-5]

        # 2. RSI (相对强弱指数)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        signals['rsi'] = rsi.iloc[-1]
        signals['rsi_signal'] = 'oversold' if rsi.iloc[-1] < 30 else 'overbought' if rsi.iloc[-1] > 70 else 'neutral'

        # 3. MACD
        exp1 = close.ewm(span=12, adjust=False).mean()
        exp2 = close.ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()

        signals['macd'] = macd.iloc[-1]
        signals['macd_signal'] = signal.iloc[-1]
        signals['macd_histogram'] = macd.iloc[-1] - signal.iloc[-1]
        signals['macd_cross'] = 'bullish' if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2] else \
                               'bearish' if macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2] else 'none'

        # 4. 布林带
        df['BB_middle'] = close.rolling(window=20).mean()
        df['BB_std'] = close.rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (df['BB_std'] * 2)
        df['BB_lower'] = df['BB_middle'] - (df['BB_std'] * 2)

        current_price = close.iloc[-1]
        bb_position = (current_price - df['BB_lower'].iloc[-1]) / (df['BB_upper'].iloc[-1] - df['BB_lower'].iloc[-1])
        signals['bb_position'] = bb_position  # 0-1之间，接近0表示在下轨，接近1表示在上轨
        signals['bb_signal'] = 'oversold' if bb_position < 0.2 else 'overbought' if bb_position > 0.8 else 'neutral'

        # 5. ATR (平均真实波幅) - 用于衡量波动性
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()

        signals['atr'] = atr.iloc[-1]
        signals['atr_percent'] = (atr.iloc[-1] / current_price) * 100

        # 6. 波动率 (过去30天)
        returns = self.calculate_returns(df)
        volatility = returns['log_return'].rolling(window=30).std() * np.sqrt(365)
        signals['volatility_annual'] = volatility.iloc[-1]
        signals['volatility_daily'] = returns['log_return'].rolling(window=30).std().iloc[-1]

        # 7. 趋势强度 (基于价格变化)
        price_change_7d = (close.iloc[-1] - close.iloc[-8]) / close.iloc[-8] * 100
        price_change_30d = (close.iloc[-1] - close.iloc[-31]) / close.iloc[-31] * 100 if len(close) >= 31 else 0
        signals['price_change_7d'] = price_change_7d
        signals['price_change_30d'] = price_change_30d

        # 8. 综合评分 (-100到+100，正值看涨，负值看跌)
        score = 0
        if signals['ma_trend'] == 'bullish':
            score += 20
        else:
            score -= 20

        if signals['rsi_signal'] == 'oversold':
            score += 25
        elif signals['rsi_signal'] == 'overbought':
            score -= 25
        elif signals['rsi'] < 50:
            score += 10
        else:
            score -= 10

        if signals['macd_histogram'] > 0:
            score += 20
        else:
            score -= 20

        if signals['bb_signal'] == 'oversold':
            score += 15
        elif signals['bb_signal'] == 'overbought':
            score -= 15

        if price_change_7d > 0:
            score += 10
        else:
            score -= 10

        signals['composite_score'] = max(-100, min(100, score))
        signals['composite_signal'] = 'bullish' if score > 20 else 'bearish' if score < -20 else 'neutral'

        return signals

    def monte_carlo_simulation(self, df: pd.DataFrame, use_trend: bool = True,
                               signals: Optional[Dict] = None) -> np.ndarray:
        """
        蒙特卡洛模拟价格路径

        Args:
            df: 历史价格数据
            use_trend: 是否考虑当前趋势
            signals: 已计算好的技术指标（避免重复计算整条指标管线）

        Returns:
            模拟路径数组 (forecast_days x n_simulations)
        """
        returns = self.calculate_returns(df)

        # 获取最近的统计参数
        recent_returns = returns['log_return'].dropna().tail(90)  # 使用最近90天数据
        mu = recent_returns.mean()
        sigma = recent_returns.std()

        # 根据技术指标调整漂移率
        if use_trend:
            if signals is None:
                signals = self.calculate_technical_indicators(df)
            # 将综合评分转换为漂移率调整 (-0.5% 到 +0.5% 每日)
            trend_adjustment = signals['composite_score'] / 100 * 0.005
            mu += trend_adjustment

        # 当前价格
        current_price = df['close_price'].iloc[-1]

        # 生成随机路径（几何布朗运动，向量化）:
        # dS/S = mu*dt + sigma*dW，逐步累乘等价于 cumprod
        random_shocks = np.random.standard_normal((self.forecast_days, self.n_simulations))
        increments = np.exp((mu - 0.5 * sigma**2) + sigma * random_shocks)
        price_paths = current_price * np.cumprod(increments, axis=0)

        return price_paths  # 未来forecast_days的路径（不包括当前价格）

    def predict(self, use_trend: bool = True) -> PredictionResult:
        """
        预测BTC未来走势

        Args:
            use_trend: 是否考虑当前趋势调整

        Returns:
            预测结果
        """
        # 获取数据
        df = self.fetch_data(days=365)

        if len(df) < 60:
            raise ValueError(f"历史数据不足，需要至少60天数据，当前只有{len(df)}天")

        current_price = df['close_price'].iloc[-1]

        # 计算技术指标（只算一次，传给蒙特卡洛复用）
        signals = self.calculate_technical_indicators(df)

        # 蒙特卡洛模拟
        simulation_paths = self.monte_carlo_simulation(df, use_trend=use_trend, signals=signals)

        # 最终价格分布
        final_prices = simulation_paths[-1]

        # 计算概率
        up_probability = np.mean(final_prices > current_price)
        down_probability = 1 - up_probability

        # 统计指标
        predicted_mean = np.mean(final_prices)
        predicted_median = np.median(final_prices)
        confidence_5 = np.percentile(final_prices, 5)
        confidence_95 = np.percentile(final_prices, 95)

        # 预期收益率
        expected_return = (predicted_mean - current_price) / current_price * 100

        # 风险收益比 (预期收益 / 下行风险)
        downside_risk = (current_price - confidence_5) / current_price * 100
        risk_reward_ratio = expected_return / downside_risk if downside_risk > 0 else 0

        return PredictionResult(
            current_price=current_price,
            predicted_price_mean=predicted_mean,
            predicted_price_median=predicted_median,
            up_probability=up_probability * 100,
            down_probability=down_probability * 100,
            confidence_interval_5=confidence_5,
            confidence_interval_95=confidence_95,
            expected_return=expected_return,
            risk_reward_ratio=risk_reward_ratio,
            simulation_paths=simulation_paths,
            forecast_days=self.forecast_days,
            technical_signals=signals
        )

    @staticmethod
    def get_recommendation(result: PredictionResult) -> str:
        """根据预测结果生成建议（纯函数，无需实例化预测器）"""
        signals = result.technical_signals

        recommendation = []

        # 基于概率的建议
        if result.up_probability > 60:
            recommendation.append("[看涨] 上涨概率较高，偏向看多")
        elif result.down_probability > 60:
            recommendation.append("[看跌] 下跌概率较高，偏向看空")
        else:
            recommendation.append("[中性] 涨跌概率接近，市场方向不明朗")

        # 基于风险收益比的建议
        if result.risk_reward_ratio > 1.5:
            recommendation.append("[建议] 风险收益比较好，值得考虑")
        elif result.risk_reward_ratio < 0.5:
            recommendation.append("[警告] 风险收益比较差，需谨慎")

        # 基于技术指标的建议
        if signals['composite_signal'] == 'bullish':
            recommendation.append("[指标] 技术指标整体偏向多头")
        elif signals['composite_signal'] == 'bearish':
            recommendation.append("[指标] 技术指标整体偏向空头")
        else:
            recommendation.append("[指标] 技术指标显示震荡格局")

        # 波动率提醒
        if signals['volatility_annual'] > 0.8:
            recommendation.append("[提醒] 当前波动率较高，注意风险控制")

        return "\n".join(recommendation)


def print_prediction_report(result: PredictionResult):
    """打印预测报告"""
    signals = result.technical_signals

    print("=" * 60)
    print(f"[BTC价格预测报告] ({result.forecast_days}天)")
    print("=" * 60)

    print(f"\n[当前价格] ${result.current_price:,.2f}")

    print(f"\n[价格预测]")
    print(f"   预期价格 (均值): ${result.predicted_price_mean:,.2f}")
    print(f"   预期价格 (中位数): ${result.predicted_price_median:,.2f}")
    print(f"   90%置信区间: ${result.confidence_interval_5:,.2f} ~ ${result.confidence_interval_95:,.2f}")

    print(f"\n[概率分布]")
    print(f"   上涨概率: {result.up_probability:.1f}%")
    print(f"   下跌概率: {result.down_probability:.1f}%")
    print(f"   预期收益率: {result.expected_return:+.2f}%")
    print(f"   风险收益比: {result.risk_reward_ratio:.2f}")

    print(f"\n[技术指标]")
    print(f"   RSI (14): {signals['rsi']:.2f} ({signals['rsi_signal']})")
    print(f"   MACD: {signals['macd']:.2f} (柱状图: {signals['macd_histogram']:+.2f})")
    print(f"   均线趋势: {signals['ma_trend']}")
    print(f"   布林带位置: {signals['bb_position']*100:.1f}%")
    print(f"   年化波动率: {signals['volatility_annual']*100:.1f}%")
    print(f"   近7日涨跌: {signals['price_change_7d']:+.2f}%")
    print(f"   近30日涨跌: {signals['price_change_30d']:+.2f}%")
    print(f"   综合评分: {signals['composite_score']:+.0f} ({signals['composite_signal']})")

    print(f"\n[投资建议]")
    print(BTCPredictor.get_recommendation(result))

    print("=" * 60)


if __name__ == '__main__':
    # 测试预测器
    predictor = BTCPredictor(forecast_days=7, n_simulations=10000)
    result = predictor.predict(use_trend=True)
    print_prediction_report(result)
