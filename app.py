#!/usr/bin/env python3
"""
加密货币追踪工具 - Web后端API
使用Flask提供REST API服务
"""
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
import os
import json
from datetime import datetime

from config import SUPPORTED_ASSETS, DEFAULT_CURRENCY, MA_PERIODS
from src.database import Database
from src.data_fetcher import DataFetcher
from src.aggregator import DataAggregator

# 初始化 Anthropic 客户端（如果配置了 API Key）
anthropic_client = None
if os.environ.get('ANTHROPIC_API_KEY'):
    try:
        import anthropic
        client_kwargs = {'api_key': os.environ.get('ANTHROPIC_API_KEY')}
        if os.environ.get('ANTHROPIC_BASE_URL'):
            client_kwargs['base_url'] = os.environ.get('ANTHROPIC_BASE_URL')
        anthropic_client = anthropic.Anthropic(**client_kwargs)
    except ImportError:



        print("Warning: anthropic SDK not installed, Claude analysis will be unavailable")

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

db = Database()
fetcher = DataFetcher()

# ============== API 路由 ==============

@app.route('/')
def index():
    """主页 - 返回前端HTML"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/assets')
def get_assets():
    """获取支持的币种列表"""
    assets = []
    for code, info in SUPPORTED_ASSETS.items():
        summary = db.get_data_summary(code, DEFAULT_CURRENCY)
        assets.append({
            'code': code,
            'name': info['name'],
            'data_count': summary['count'],
            'start_date': summary['start_date'],
            'end_date': summary['end_date'],
            'min_price': summary['min_price'],
            'max_price': summary['max_price']
        })
    return jsonify(assets)


@app.route('/api/config')
def get_config():
    """获取后端配置信息（用于诊断）"""
    return jsonify({
        'anthropic_available': anthropic_client is not None,
        'anthropic_api_key_set': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'anthropic_base_url_set': bool(os.environ.get('ANTHROPIC_BASE_URL')),
        'supported_assets': list(SUPPORTED_ASSETS.keys()),
        'default_currency': DEFAULT_CURRENCY
    })


@app.route('/api/data/<asset_code>')
def get_data(asset_code):
    """获取指定币种的历史数据"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    timeframe = request.args.get('timeframe', 'day')
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    try:
        # 获取原始日线数据
        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if df.empty:
            return jsonify({'error': 'No data available'}), 404

        # 根据时间周期聚合
        if timeframe == 'week':
            df = DataAggregator.resample_to_weekly(df)
        elif timeframe == 'month':
            df = DataAggregator.resample_to_monthly(df)

        # 转换为前端需要的格式
        data = []
        for date, row in df.iterrows():
            data.append({
                'time': int(date.timestamp()),
                'date': date.strftime('%Y-%m-%d'),
                'open': float(row['open_price']),
                'high': float(row['max_price']),
                'low': float(row['min_price']),
                'close': float(row['close_price'])
            })

        return jsonify({
            'asset': asset_code,
            'timeframe': timeframe,
            'count': len(data),
            'data': data
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/data/<asset_code>/with_ma')
def get_data_with_ma(asset_code):
    """获取带均线的数据"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    timeframe = request.args.get('timeframe', 'day')
    ma_periods_str = request.args.get('ma_periods', '')
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    try:
        # 解析均线周期
        ma_periods = []
        if ma_periods_str:
            ma_periods = [int(p.strip()) for p in ma_periods_str.split(',') if p.strip()]

        # 获取数据
        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if df.empty:
            return jsonify({'error': 'No data available'}), 404

        # 根据时间周期聚合（聚合后再计算MA，因为日线MA不适用于周线/月线）
        if timeframe == 'week':
            df = DataAggregator.resample_to_weekly(df)
        elif timeframe == 'month':
            df = DataAggregator.resample_to_monthly(df)

        # 计算均线（在聚合后计算）
        for period in ma_periods:
            df[f'MA{period}'] = df['close_price'].rolling(window=period, min_periods=1).mean()

        # 转换为前端格式
        data = []
        ma_data = {f'MA{p}': [] for p in ma_periods}

        for date, row in df.iterrows():
            item = {
                'time': int(date.timestamp()),
                'date': date.strftime('%Y-%m-%d'),
                'open': float(row['open_price']),
                'high': float(row['max_price']),
                'low': float(row['min_price']),
                'close': float(row['close_price'])
            }

            # 添加均线数据
            for period in ma_periods:
                ma_col = f'MA{period}'
                if ma_col in row:
                    item[ma_col] = float(row[ma_col]) if pd.notna(row[ma_col]) else None

            data.append(item)

        return jsonify({
            'asset': asset_code,
            'timeframe': timeframe,
            'ma_periods': ma_periods,
            'count': len(data),
            'data': data
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update/<asset_code>', methods=['POST'])
def update_data(asset_code):
    """更新指定币种的数据"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        latest_date = db.get_latest_date(asset_code, DEFAULT_CURRENCY)
        new_data = fetcher.fetch_incremental_data(asset_code, latest_date)

        if new_data:
            count = db.save_price_data(asset_code, DEFAULT_CURRENCY, new_data)
            return jsonify({
                'success': True,
                'message': f'Successfully saved {count} records',
                'count': count
            })
        else:
            return jsonify({
                'success': True,
                'message': 'Data is already up to date',
                'count': 0
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_all', methods=['POST'])
def update_all():
    """更新所有币种的数据"""
    results = []

    for asset_code in SUPPORTED_ASSETS.keys():
        try:
            latest_date = db.get_latest_date(asset_code, DEFAULT_CURRENCY)
            new_data = fetcher.fetch_incremental_data(asset_code, latest_date)

            if new_data:
                count = db.save_price_data(asset_code, DEFAULT_CURRENCY, new_data)
                results.append({'asset': asset_code, 'status': 'success', 'count': count})
            else:
                results.append({'asset': asset_code, 'status': 'up_to_date', 'count': 0})

        except Exception as e:
            results.append({'asset': asset_code, 'status': 'error', 'error': str(e)})

    return jsonify({'results': results})

# ============== 预测API ==============

@app.route('/api/predict/<asset_code>')
def predict_asset(asset_code):
    """预测指定币种的未来走势"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        # 获取参数
        forecast_days = int(request.args.get('days', 7))
        simulations = int(request.args.get('simulations', 10000))
        strategy_name = request.args.get('strategy', 'monte_carlo')
        strategy_params = request.args.get('params', {})
        if isinstance(strategy_params, str):
            import json
            strategy_params = json.loads(strategy_params)

        # 获取历史数据
        df = db.get_price_data(asset_code, DEFAULT_CURRENCY)

        if len(df) < 60:
            return jsonify({'error': 'Insufficient historical data'}), 400

        # 创建策略
        strategy = create_strategy(strategy_name, strategy_params)

        # 执行预测
        result = strategy.predict(df, forecast_days=forecast_days)

        # 构建响应
        response = {
            'asset': asset_code,
            'strategy': strategy_name,
            'forecast_days': forecast_days,
            'timestamp': result.timestamp.isoformat(),
            'current_price': float(result.current_price),
            'predicted_price_mean': float(result.predicted_price_mean),
            'predicted_price_median': float(result.predicted_price_median),
            'confidence_interval': {
                'low': float(result.confidence_interval_low),
                'high': float(result.confidence_interval_high)
            },
            'probabilities': {
                'up': float(result.up_probability),
                'down': float(result.down_probability)
            },
            'expected_return': float(result.expected_return),
            'risk_reward_ratio': float(result.risk_reward_ratio),
            'metadata': convert_to_native(result.metadata),
            'simulation_paths': convert_to_native(result.simulation_paths) if result.simulation_paths and len(result.simulation_paths) < 1000 else None
        }

        return jsonify(response)

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/predict/<asset_code>/chart')
def predict_asset_chart(asset_code):
    """生成预测图表"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        forecast_days = int(request.args.get('days', 7))
        simulations = int(request.args.get('simulations', 5000))

        from src.btc_predictor import BTCPredictor
        from src.prediction_chart import plot_prediction
        import io
        import base64

        predictor = BTCPredictor(forecast_days=forecast_days, n_simulations=simulations)
        result = predictor.predict(use_trend=True)

        # 生成图表
        buf = io.BytesIO()
        plot_prediction(result, save_path=buf, show=False)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')

        return jsonify({
            'image': f'data:image/png;base64,{image_base64}'
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============== 辅助函数 ==============

def convert_to_native(obj):
    """将 numpy 类型转换为 Python 原生类型，用于 JSON 序列化"""
    import numpy as np
    from datetime import datetime

    if isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif hasattr(obj, 'isoformat'):  # pandas Timestamp
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: convert_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_native(item) for item in obj]
    return obj


# ============== 回测API ==============

def create_strategy(strategy_name: str, params: dict):
    """创建策略实例"""
    from src.strategies import (
        MonteCarloStrategy, TrendFollowingStrategy,
        MeanReversionStrategy, EnsembleStrategy, RegimeAwareStrategy,
        LSTMStrategy
    )

    if strategy_name == 'monte_carlo':
        return MonteCarloStrategy(
            n_simulations=params.get('n_simulations', 10000),
            use_trend_adjustment=params.get('use_trend_adjustment', True)
        )
    elif strategy_name == 'trend_following':
        return TrendFollowingStrategy(
            short_window=params.get('short_window', 7),
            medium_window=params.get('medium_window', 30),
            long_window=params.get('long_window', 90)
        )
    elif strategy_name == 'mean_reversion':
        return MeanReversionStrategy(
            rsi_period=params.get('rsi_period', 14),
            rsi_overbought=params.get('rsi_overbought', 70),
            rsi_oversold=params.get('rsi_oversold', 30)
        )
    elif strategy_name == 'ensemble':
        return EnsembleStrategy(
            strategies=[
                TrendFollowingStrategy(),
                MeanReversionStrategy(),
                MonteCarloStrategy(n_simulations=2000)
            ],
            weights=[0.4, 0.3, 0.3],
            voting_method=params.get('voting_method', 'weighted_average')
        )
    elif strategy_name == 'regime_aware':
        return RegimeAwareStrategy(
            trend_strategy=TrendFollowingStrategy(),
            range_strategy=MeanReversionStrategy(),
            adx_threshold=params.get('adx_threshold', 25)
        )
    elif strategy_name == 'lstm':
        return LSTMStrategy(
            seq_len=params.get('seq_len', 60),
            hidden_size=params.get('hidden_size', 128),
            num_layers=params.get('num_layers', 2)
        )
    else:
        raise ValueError(f'Unknown strategy: {strategy_name}')


@app.route('/api/backtest/<asset_code>', methods=['POST'])
def backtest_asset(asset_code):
    """回测预测策略"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        data = request.get_json() or {}

        # 参数
        strategy_name = data.get('strategy', 'monte_carlo')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        forecast_days = int(data.get('forecast_days', 7))
        step_days = int(data.get('step_days', 7))
        strategy_params = data.get('strategy_params', {})
        engine_type = data.get('engine', 'standard')  # 'standard' or 'enhanced'

        # 增强引擎参数
        enhanced_params = data.get('enhanced_params', {})

        # 获取历史数据
        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if len(df) < 120:
            return jsonify({'error': f'Insufficient data. Need at least 120 days, got {len(df)}'}), 400

        # 创建策略
        strategy = create_strategy(strategy_name, strategy_params)

        # 运行回测
        if engine_type == 'enhanced':
            # 使用增强回测引擎
            from src.backtest import EnhancedBacktestEngine
            engine = EnhancedBacktestEngine(strategy)

            result = engine.run_backtest(
                df=df,
                start_date=start_date,
                end_date=end_date,
                initial_capital=enhanced_params.get('initial_capital', 10000),
                position_size=enhanced_params.get('position_size', 0.8),
                use_compound=enhanced_params.get('use_compound', True),
                stop_loss_pct=enhanced_params.get('stop_loss_pct', 10),
                take_profit_pct=enhanced_params.get('take_profit_pct', 20),
                trailing_stop_pct=enhanced_params.get('trailing_stop_pct', 8),
                max_position_hold_days=enhanced_params.get('max_position_hold_days', 30),
                rebalance_freq=enhanced_params.get('rebalance_freq', 'daily'),
                strategy_params=strategy_params
            )

            # 获取交易记录
            trade_summary = engine.get_trade_summary()
            trades_data = trade_summary.to_dict('records') if not trade_summary.empty else []
        else:
            # 使用标准回测引擎
            from src.backtest import BacktestEngine
            engine = BacktestEngine(strategy)

            # 设置默认交易参数
            if 'long_threshold' not in strategy_params:
                strategy_params['long_threshold'] = 60
            if 'short_threshold' not in strategy_params:
                strategy_params['short_threshold'] = 40
            if 'use_position_sizing' not in strategy_params:
                strategy_params['use_position_sizing'] = False
            if 'trend_filter' not in strategy_params:
                strategy_params['trend_filter'] = 'none'

            result = engine.run_backtest(
                df=df,
                start_date=start_date,
                end_date=end_date,
                forecast_days=forecast_days,
                step_days=step_days,
                strategy_params=strategy_params
            )
            trades_data = []

        # 构建响应
        response = {
            'asset': asset_code,
            'strategy': result.strategy_name,
            'engine': engine_type,
            'start_date': result.start_date,
            'end_date': result.end_date,
            'forecast_days': result.forecast_days,
            'total_predictions': result.total_predictions,
            'period_days': result.period_days,
            'metrics': {
                # 准确性指标
                'direction_accuracy': float(result.direction_accuracy),
                'direction_accuracy_up': float(result.direction_accuracy_up),
                'direction_accuracy_down': float(result.direction_accuracy_down),
                # 误差指标
                'mae': float(result.mae),
                'rmse': float(result.rmse),
                'mape': float(result.mape),
                'probability_calibration': float(result.probability_calibration),
                # 策略交易指标
                'trading_return': float(result.trading_return),
                'trading_annual_return': float(result.trading_annual_return),
                'trading_sharpe': float(result.trading_sharpe),
                'max_drawdown': float(result.max_drawdown),
                'annual_volatility': float(result.annual_volatility),
                'var_95': float(result.var_95),
                'profit_loss_ratio': float(result.profit_loss_ratio),
                'win_rate': float(result.win_rate),
                'total_trades': int(result.total_trades),
                # 买入持有对比（基准）
                'buy_hold_return': float(result.buy_hold_return),
                'buy_hold_annual_return': float(result.buy_hold_annual_return),
                'buy_hold_max_drawdown': float(result.buy_hold_max_drawdown),
                'buy_hold_volatility': float(result.buy_hold_volatility),
                'buy_hold_sharpe': float(result.buy_hold_sharpe),
                # 超额收益
                'excess_return': float(result.trading_return - result.buy_hold_return),
                'excess_annual_return': float(result.trading_annual_return - result.buy_hold_annual_return)
            },
            'predictions': convert_to_native(result.predictions[:100]),
            'trades': trades_data[:50],  # 限制返回数量
            'position_history': convert_to_native(result.position_history[:500]) if result.position_history else []
        }

        return jsonify(response)

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backtest/<asset_code>/chart', methods=['POST'])
def backtest_asset_chart(asset_code):
    """生成回测结果图表"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        data = request.get_json() or {}

        # 参数
        strategy_name = data.get('strategy', 'monte_carlo')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        forecast_days = int(data.get('forecast_days', 7))
        step_days = int(data.get('step_days', 7))
        strategy_params = data.get('strategy_params', {})
        engine_type = data.get('engine', 'standard')
        enhanced_params = data.get('enhanced_params', {})

        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        # 创建策略
        strategy = create_strategy(strategy_name, strategy_params)

        from src.backtest.visualization import plot_backtest_result, plot_strategy_comparison
        import io
        import base64

        if engine_type == 'enhanced':
            from src.backtest import EnhancedBacktestEngine
            engine = EnhancedBacktestEngine(strategy)
            result = engine.run_backtest(
                df=df,
                start_date=start_date,
                end_date=end_date,
                initial_capital=enhanced_params.get('initial_capital', 10000),
                position_size=enhanced_params.get('position_size', 0.8),
                use_compound=enhanced_params.get('use_compound', True),
                stop_loss_pct=enhanced_params.get('stop_loss_pct', 10),
                take_profit_pct=enhanced_params.get('take_profit_pct', 20),
                trailing_stop_pct=enhanced_params.get('trailing_stop_pct', 8),
                rebalance_freq=enhanced_params.get('rebalance_freq', 'daily'),
                strategy_params=strategy_params
            )
        else:
            from src.backtest import BacktestEngine
            if 'long_threshold' not in strategy_params:
                strategy_params['long_threshold'] = 60
            if 'short_threshold' not in strategy_params:
                strategy_params['short_threshold'] = 40

            engine = BacktestEngine(strategy)
            result = engine.run_backtest(
                df=df,
                start_date=start_date,
                end_date=end_date,
                forecast_days=forecast_days,
                step_days=step_days,
                strategy_params=strategy_params
            )

        # 生成图表
        buf = io.BytesIO()
        plot_backtest_result(result, save_path=buf, show=False)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')

        return jsonify({
            'image': f'data:image/png;base64,{image_base64}',
            'metrics': {
                'direction_accuracy': result.direction_accuracy,
                'trading_return': result.trading_return,
                'buy_hold_return': result.buy_hold_return,
                'excess_return': result.trading_return - result.buy_hold_return
            }
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backtest/<asset_code>/compare', methods=['POST'])
def backtest_compare(asset_code):
    """对比多个策略的回测表现"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        data = request.get_json() or {}

        strategy_names = data.get('strategies', ['monte_carlo', 'trend_following'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        engine_type = data.get('engine', 'enhanced')
        enhanced_params = data.get('enhanced_params', {})
        strategy_params = data.get('strategy_params', {})

        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if len(df) < 120:
            return jsonify({'error': f'Insufficient data. Need at least 120 days, got {len(df)}'}), 400

        results = []

        for strategy_name in strategy_names:
            try:
                strategy = create_strategy(strategy_name, strategy_params.get(strategy_name, {}))

                if engine_type == 'enhanced':
                    from src.backtest import EnhancedBacktestEngine
                    engine = EnhancedBacktestEngine(strategy)
                    result = engine.run_backtest(
                        df=df,
                        start_date=start_date,
                        end_date=end_date,
                        initial_capital=enhanced_params.get('initial_capital', 10000),
                        position_size=enhanced_params.get('position_size', 0.8),
                        use_compound=enhanced_params.get('use_compound', True),
                        stop_loss_pct=enhanced_params.get('stop_loss_pct', 10),
                        take_profit_pct=enhanced_params.get('take_profit_pct', 20),
                        rebalance_freq=enhanced_params.get('rebalance_freq', 'daily'),
                        strategy_params=strategy_params.get(strategy_name, {})
                    )
                else:
                    from src.backtest import BacktestEngine
                    engine = BacktestEngine(strategy)
                    sp = strategy_params.get(strategy_name, {})
                    sp.setdefault('long_threshold', 60)
                    sp.setdefault('short_threshold', 40)
                    result = engine.run_backtest(
                        df=df,
                        start_date=start_date,
                        end_date=end_date,
                        forecast_days=7,
                        step_days=7,
                        strategy_params=sp
                    )

                results.append({
                    'strategy': strategy_name,
                    'trading_return': float(result.trading_return),
                    'trading_annual_return': float(result.trading_annual_return),
                    'trading_sharpe': float(result.trading_sharpe),
                    'max_drawdown': float(result.max_drawdown),
                    'win_rate': float(result.win_rate),
                    'total_trades': int(result.total_trades),
                    'buy_hold_return': float(result.buy_hold_return),
                    'excess_return': float(result.trading_return - result.buy_hold_return)
                })

            except Exception as e:
                results.append({
                    'strategy': strategy_name,
                    'error': str(e)
                })

        return jsonify({
            'asset': asset_code,
            'engine': engine_type,
            'start_date': start_date,
            'end_date': end_date,
            'results': results
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backtest/<asset_code>/compare/chart', methods=['POST'])
def backtest_compare_chart(asset_code):
    """生成策略对比图表"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        data = request.get_json() or {}

        strategy_names = data.get('strategies', ['monte_carlo', 'trend_following'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        engine_type = data.get('engine', 'enhanced')
        enhanced_params = data.get('enhanced_params', {})

        df = db.get_price_data(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        # 收集所有回测结果
        backtest_results = []

        for strategy_name in strategy_names:
            try:
                strategy = create_strategy(strategy_name, {})

                if engine_type == 'enhanced':
                    from src.backtest import EnhancedBacktestEngine
                    engine = EnhancedBacktestEngine(strategy)
                    result = engine.run_backtest(
                        df=df,
                        start_date=start_date,
                        end_date=end_date,
                        initial_capital=enhanced_params.get('initial_capital', 10000),
                        position_size=enhanced_params.get('position_size', 0.8),
                        use_compound=enhanced_params.get('use_compound', True),
                        stop_loss_pct=enhanced_params.get('stop_loss_pct', 10),
                        take_profit_pct=enhanced_params.get('take_profit_pct', 20),
                        rebalance_freq=enhanced_params.get('rebalance_freq', 'daily')
                    )
                else:
                    from src.backtest import BacktestEngine
                    engine = BacktestEngine(strategy)
                    result = engine.run_backtest(df=df, start_date=start_date, end_date=end_date)

                backtest_results.append(result)

            except Exception as e:
                print(f"Strategy {strategy_name} failed: {e}")

        if not backtest_results:
            return jsonify({'error': 'No strategies succeeded'}), 500

        # 生成对比图表
        from src.backtest.visualization import plot_strategy_comparison
        import io
        import base64

        buf = io.BytesIO()
        plot_strategy_comparison(backtest_results, save_path=buf, show=False)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')

        return jsonify({
            'image': f'data:image/png;base64,{image_base64}',
            'strategies': [r.strategy_name for r in backtest_results]
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backtest/<asset_code>/analyze', methods=['POST'])
def analyze_backtest(asset_code):
    """使用 Claude AI 分析回测结果，找出策略表现不如买入持有的原因"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    # 检查 Anthropic 客户端是否可用
    if anthropic_client is None:
        return jsonify({
            'error': 'Claude analysis not available. Please set ANTHROPIC_API_KEY environment variable.'
        }), 503

    try:
        data = request.get_json() or {}

        # 获取请求参数
        strategy_name = data.get('strategy', 'unknown')
        strategy_params = data.get('strategy_params', {})
        metrics = data.get('metrics', {})
        trades = data.get('trades', [])
        position_history = data.get('position_history', [])
        buy_hold_return = data.get('buy_hold_return', 0)
        strategy_return = data.get('strategy_return', 0)
        start_date = data.get('start_date', '')
        end_date = data.get('end_date', '')

        # 构建策略描述
        strategy_descriptions = {
            'monte_carlo': '蒙特卡洛模拟策略 - 基于历史波动率进行随机模拟预测',
            'trend_following': '趋势跟踪策略 - 使用多时间框架移动平均线识别趋势',
            'mean_reversion': '均值回归策略 - 基于RSI和布林带识别超买超卖',
            'ensemble': '策略组合 - 综合多个策略的预测结果',
            'regime_aware': '状态感知策略 - 根据市场状态自动切换策略'
        }
        strategy_desc = strategy_descriptions.get(strategy_name, strategy_name)

        # 计算交易统计
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) <= 0]
        avg_win = sum(t.get('pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0

        # 计算持仓时间统计
        hold_times = []
        for t in trades:
            entry_date = t.get('entry_date', '')
            exit_date = t.get('exit_date', '')
            if entry_date and exit_date:
                try:
                    from datetime import datetime
                    entry = datetime.strptime(entry_date, '%Y-%m-%d')
                    exit = datetime.strptime(exit_date, '%Y-%m-%d')
                    hold_times.append((exit - entry).days)
                except:
                    pass
        avg_hold_time = sum(hold_times) / len(hold_times) if hold_times else 0

        # 识别关键调仓时机（大额盈亏）
        significant_trades = sorted(trades, key=lambda x: abs(x.get('pnl', 0)), reverse=True)[:5]

        # 构建 prompt
        prompt = f"""你是一个专业的量化交易策略分析师。请分析以下策略回测结果，找出策略表现不如买入持有的原因，并给出改进建议。

## 回测基本信息
- 资产: {asset_code}
- 回测期间: {start_date} 至 {end_date}
- 策略: {strategy_desc}
- 策略参数: {json.dumps(strategy_params, ensure_ascii=False)}

## 收益对比
- 策略总收益率: {strategy_return:.2f}%
- 买入持有总收益率: {buy_hold_return:.2f}%
- 收益差距: {strategy_return - buy_hold_return:.2f}% (策略落后)

## 策略交易统计
- 总交易次数: {len(trades)}
- 胜率: {metrics.get('win_rate', 0):.2f}%
- 盈亏比: {metrics.get('profit_loss_ratio', 0):.2f}
- 平均盈利: {avg_win:.2f}%
- 平均亏损: {avg_loss:.2f}%
- 最大回撤: {metrics.get('max_drawdown', 0):.2f}%
- 平均持仓时间: {avg_hold_time:.1f} 天

## 风险指标
- 策略夏普比率: {metrics.get('trading_sharpe', 0):.2f}
- 买入持有夏普比率: {metrics.get('buy_hold_sharpe', 0):.2f}
- 年化波动率: {metrics.get('annual_volatility', 0):.2f}%

## 重要交易记录（按盈亏绝对值排序）
"""
        for i, t in enumerate(significant_trades, 1):
            prompt += f"""
{i}. {t.get('entry_date', 'N/A')} 开仓 -> {t.get('exit_date', 'N/A')} 平仓
   - 方向: {t.get('direction', 'N/A')}
   - 入场价: ${t.get('entry_price', 0):,.2f} -> 出场价: ${t.get('exit_price', 0):,.2f}
   - 盈亏: {t.get('pnl', 0):+.2f}% ({t.get('exit_reason', 'N/A')})
"""

        prompt += f"""
## 分析要求
请从以下几个方面进行分析：
1. **错失机会分析**: 策略在哪些时期空仓或轻仓，错过了主要上涨行情？
2. **错误信号分析**: 策略在哪些时期错误地开仓或持仓，导致亏损？
3. **止损止盈分析**: 止损止盈设置是否过早或过晚？移动止损是否有效？
4. **持仓时间分析**: 平均持仓时间是否合理？是否过早获利了结或过久持有亏损？
5. **波动率适应**: 策略是否适应了市场的波动率变化？

请用中文回复，格式如下：
- analysis: 详细的原因分析（200-400字）
- key_findings: 关键发现列表（3-5条）
- improvement_suggestions: 改进建议列表（3-5条）
"""

        # 调用 Claude API
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        # 解析响应
        analysis_text = response.content[0].text if response.content else ""

        # 提取关键发现和建议（简单的文本解析）
        key_findings = []
        improvement_suggestions = []

        # 尝试从响应中提取列表
        lines = analysis_text.split('\n')
        current_section = None

        for line in lines:
            line = line.strip()
            if '关键发现' in line or 'key_findings' in line.lower():
                current_section = 'findings'
                continue
            elif '改进建议' in line or 'improvement' in line.lower():
                current_section = 'suggestions'
                continue
            elif line.startswith('analysis:') or line.startswith('分析：'):
                continue

            if line.startswith('-') or line.startswith('*') or (len(line) > 2 and line[0].isdigit() and line[1] == '.'):
                item = line.lstrip('-*0123456789. ')
                if current_section == 'findings' and item:
                    key_findings.append(item)
                elif current_section == 'suggestions' and item:
                    improvement_suggestions.append(item)

        # 如果没有提取到，使用默认提取逻辑
        if not key_findings:
            # 尝试提取任何看起来是列表项的内容
            for line in lines:
                line = line.strip()
                if (line.startswith('-') or line.startswith('*')) and len(line) > 10:
                    key_findings.append(line.lstrip('-* '))
                if len(key_findings) >= 5:
                    break

        return jsonify({
            'analysis': analysis_text,
            'key_findings': key_findings[:5] if key_findings else ['未能自动提取关键发现'],
            'improvement_suggestions': improvement_suggestions[:5] if improvement_suggestions else ['未能自动提取改进建议'],
            'strategy': strategy_name,
            'asset': asset_code
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============== 策略列表API ==============

@app.route('/api/strategies')
def get_strategies():
    """获取可用的预测策略列表"""
    strategies = [
        {
            'id': 'monte_carlo',
            'name': 'Monte Carlo Simulation',
            'description': '基于历史波动率的蒙特卡洛模拟预测',
            'category': 'prediction',
            'parameters': {
                'n_simulations': {
                    'type': 'integer',
                    'default': 10000,
                    'min': 1000,
                    'max': 50000,
                    'description': '模拟次数'
                },
                'use_trend_adjustment': {
                    'type': 'boolean',
                    'default': True,
                    'description': '是否使用趋势调整'
                }
            }
        },
        {
            'id': 'trend_following',
            'name': 'Trend Following',
            'description': '多时间框架趋势跟踪策略，适合趋势市场',
            'category': 'trading',
            'parameters': {
                'short_window': {
                    'type': 'integer',
                    'default': 7,
                    'min': 3,
                    'max': 30,
                    'description': '短期窗口'
                },
                'medium_window': {
                    'type': 'integer',
                    'default': 30,
                    'min': 10,
                    'max': 60,
                    'description': '中期窗口'
                },
                'long_window': {
                    'type': 'integer',
                    'default': 90,
                    'min': 30,
                    'max': 200,
                    'description': '长期窗口'
                }
            }
        },
        {
            'id': 'mean_reversion',
            'name': 'Mean Reversion',
            'description': '基于RSI和布林带的均值回归策略，适合震荡市场',
            'category': 'trading',
            'parameters': {
                'rsi_period': {
                    'type': 'integer',
                    'default': 14,
                    'min': 5,
                    'max': 30,
                    'description': 'RSI周期'
                },
                'rsi_overbought': {
                    'type': 'integer',
                    'default': 70,
                    'min': 60,
                    'max': 90,
                    'description': 'RSI超买阈值'
                },
                'rsi_oversold': {
                    'type': 'integer',
                    'default': 30,
                    'min': 10,
                    'max': 40,
                    'description': 'RSI超卖阈值'
                }
            }
        },
        {
            'id': 'ensemble',
            'name': 'Ensemble Strategy',
            'description': '组合多个策略的预测结果，提高稳定性',
            'category': 'trading',
            'parameters': {
                'voting_method': {
                    'type': 'select',
                    'options': ['weighted_average', 'majority', 'confidence'],
                    'default': 'weighted_average',
                    'description': '投票方法'
                }
            }
        },
        {
            'id': 'regime_aware',
            'name': 'Regime Aware',
            'description': '根据市场状态自动切换策略（趋势/震荡）',
            'category': 'trading',
            'parameters': {
                'adx_threshold': {
                    'type': 'integer',
                    'default': 25,
                    'min': 15,
                    'max': 40,
                    'description': 'ADX阈值（高于此值为趋势市）'
                }
            }
        }
    ]
    return jsonify(strategies)


# ============== 模拟持仓页面 ==============

@app.route('/portfolio')
def portfolio_page():
    """模拟持仓页面"""
    with open('templates/portfolio.html', 'r', encoding='utf-8') as f:
        content = f.read()
    return render_template_string(content)


# ============== 模拟持仓 API ==============

@app.route('/api/portfolios', methods=['GET'])
def list_portfolios():
    """获取所有模拟仓位列表"""
    portfolios = db.get_all_portfolios()
    # 获取每个仓位的最新价格计算收益
    result = []
    for p in portfolios:
        asset_code = p['asset_code']
        try:
            price_df = db.get_price_data(asset_code, DEFAULT_CURRENCY)
            current_price = float(price_df['close_price'].iloc[-1]) if not price_df.empty else 0
        except Exception:
            current_price = 0
        stats = db.calculate_portfolio_stats(p['id'], current_price)
        result.append(stats)
    return jsonify(result)


@app.route('/api/portfolios', methods=['POST'])
def create_portfolio():
    """创建新的模拟仓位"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    asset_code = data.get('asset_code', 'BTC').upper()
    mode = data.get('mode', 'manual')  # 'manual' or 'strategy'
    strategy = data.get('strategy')
    initial_capital = float(data.get('initial_capital', 10000))
    description = data.get('description', '')

    if not name:
        return jsonify({'error': '仓位名称不能为空'}), 400
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': '不支持的币种'}), 400

    portfolio_id = db.create_portfolio(name, asset_code, mode, initial_capital, strategy, description)
    return jsonify({'id': portfolio_id, 'message': f'仓位 "{name}" 创建成功'})


@app.route('/api/portfolios/<int:portfolio_id>', methods=['GET'])
def get_portfolio(portfolio_id):
    """获取单个仓位详情及交易记录"""
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        return jsonify({'error': '仓位不存在'}), 404

    asset_code = portfolio['asset_code']
    try:
        price_df = db.get_price_data(asset_code, DEFAULT_CURRENCY)
        current_price = float(price_df['close_price'].iloc[-1]) if not price_df.empty else 0
    except Exception:
        current_price = 0

    stats = db.calculate_portfolio_stats(portfolio_id, current_price)
    trades = db.get_trades(portfolio_id)
    stats['trades'] = trades
    return jsonify(stats)


@app.route('/api/portfolios/<int:portfolio_id>', methods=['DELETE'])
def delete_portfolio(portfolio_id):
    """删除仓位"""
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        return jsonify({'error': '仓位不存在'}), 404
    db.delete_portfolio(portfolio_id)
    return jsonify({'message': '仓位已删除'})


@app.route('/api/portfolios/<int:portfolio_id>/trades', methods=['POST'])
def add_trade(portfolio_id):
    """添加交易记录"""
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        return jsonify({'error': '仓位不存在'}), 404

    data = request.get_json() or {}
    trade_date = data.get('trade_date', datetime.now().strftime('%Y-%m-%d'))
    trade_type = data.get('trade_type', 'buy')  # 'buy' or 'sell'
    price = float(data.get('price', 0))
    quantity = float(data.get('quantity', 0))
    fee = float(data.get('fee', 0))
    note = data.get('note', '')

    if price <= 0 or quantity <= 0:
        return jsonify({'error': '价格和数量必须大于0'}), 400
    if trade_type not in ('buy', 'sell'):
        return jsonify({'error': '交易类型必须是 buy 或 sell'}), 400

    trade_id = db.add_trade(portfolio_id, trade_date, trade_type, price, quantity, fee, note)
    return jsonify({'id': trade_id, 'message': '交易记录已添加'})


@app.route('/api/portfolios/<int:portfolio_id>/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(portfolio_id, trade_id):
    """删除交易记录"""
    db.delete_trade(trade_id)
    return jsonify({'message': '记录已删除'})


@app.route('/api/portfolios/compare', methods=['GET'])
def compare_portfolios():
    """对比所有仓位的收益数据"""
    portfolios = db.get_all_portfolios()
    result = []
    for p in portfolios:
        asset_code = p['asset_code']
        try:
            price_df = db.get_price_data(asset_code, DEFAULT_CURRENCY)
            current_price = float(price_df['close_price'].iloc[-1]) if not price_df.empty else 0
        except Exception:
            current_price = 0
        stats = db.calculate_portfolio_stats(p['id'], current_price)
        result.append(stats)
    return jsonify(result)


@app.route('/api/portfolios/<int:portfolio_id>/equity_curve', methods=['GET'])
def get_equity_curve(portfolio_id):
    """获取仓位的权益曲线（按交易日期）"""
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio:
        return jsonify({'error': '仓位不存在'}), 404

    asset_code = portfolio['asset_code']
    trades = db.get_trades(portfolio_id)
    if not trades:
        return jsonify({'curve': [], 'portfolio': portfolio})

    # 获取从第一笔交易起的价格数据
    first_date = trades[0]['trade_date']
    try:
        price_df = db.get_price_data(asset_code, DEFAULT_CURRENCY, first_date)
    except Exception:
        return jsonify({'curve': [], 'portfolio': portfolio})

    initial_capital = portfolio['initial_capital']

    # 按日期计算每日权益
    curve = []
    trade_index = 0
    buy_queue = []
    realized_pnl = 0.0
    total_fees = 0.0

    for date, row in price_df.iterrows():
        date_str = date.strftime('%Y-%m-%d')
        current_price = float(row['close_price'])

        # 处理当天的交易
        while trade_index < len(trades) and trades[trade_index]['trade_date'] <= date_str:
            t = trades[trade_index]
            qty = float(t['quantity'])
            price = float(t['price'])
            fee = float(t['fee'])
            total_fees += fee
            if t['trade_type'] == 'buy':
                buy_queue.append({'price': price, 'qty': qty})
            elif t['trade_type'] == 'sell':
                remaining = qty
                while remaining > 0 and buy_queue:
                    head = buy_queue[0]
                    if head['qty'] <= remaining:
                        realized_pnl += (price - head['price']) * head['qty']
                        remaining -= head['qty']
                        buy_queue.pop(0)
                    else:
                        realized_pnl += (price - head['price']) * remaining
                        head['qty'] -= remaining
                        remaining = 0
            trade_index += 1

        current_qty = sum(b['qty'] for b in buy_queue)
        unrealized_pnl = (current_price - (sum(b['price']*b['qty'] for b in buy_queue)/current_qty if current_qty > 0 else 0)) * current_qty
        total_equity = initial_capital + realized_pnl + unrealized_pnl - total_fees

        curve.append({
            'date': date_str,
            'equity': round(total_equity, 2),
            'return_pct': round((total_equity - initial_capital) / initial_capital * 100, 4)
        })

    return jsonify({'curve': curve, 'portfolio': portfolio})


# ============== 前端HTML模板 ==============

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>加密货币分析工具</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f0f2f5;
            min-height: 100vh;
        }

        /* 应用容器 */
        .app-container {
            display: flex;
            min-height: 100vh;
        }

        /* 侧边栏 */
        .sidebar {
            width: 220px;
            background: linear-gradient(180deg, #1a1f36 0%, #2d3561 100%);
            color: white;
            display: flex;
            flex-direction: column;
            position: fixed;
            height: 100vh;
            left: 0;
            top: 0;
            z-index: 1000;
            transition: transform 0.3s ease;
        }

        .sidebar-header {
            padding: 24px 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .sidebar-header h1 {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 4px;
        }

        .sidebar-header p {
            font-size: 12px;
            opacity: 0.7;
        }

        .sidebar-nav {
            flex: 1;
            padding: 16px 12px;
            overflow-y: auto;
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 16px;
            margin-bottom: 4px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
            color: rgba(255,255,255,0.8);
            font-size: 14px;
            font-weight: 500;
        }

        .nav-item:hover {
            background: rgba(255,255,255,0.1);
            color: white;
        }

        .nav-item.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }

        .nav-icon {
            width: 20px;
            height: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
        }

        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid rgba(255,255,255,0.1);
            font-size: 12px;
            opacity: 0.6;
            text-align: center;
        }

        /* 移动端菜单按钮 */
        .menu-toggle {
            display: none;
            position: fixed;
            top: 16px;
            left: 16px;
            z-index: 1001;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 18px;
        }

        /* 主内容区 */
        .main-content {
            flex: 1;
            margin-left: 220px;
            padding: 24px;
            min-height: 100vh;
        }

        /* 面板容器 */
        .panel {
            display: none;
        }

        .panel.active {
            display: block;
        }

        .panel-header {
            margin-bottom: 24px;
        }

        .panel-header h2 {
            font-size: 24px;
            font-weight: 600;
            color: #1a1f36;
            margin-bottom: 8px;
        }

        .panel-header p {
            color: #666;
            font-size: 14px;
        }

        /* 卡片样式 */
        .card {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }

        /* 控制面板 */
        .control-panel {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }

        .control-row {
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            align-items: center;
            margin-bottom: 15px;
        }

        .control-row:last-child {
            margin-bottom: 0;
        }

        .control-group {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .control-group label {
            font-weight: 600;
            color: #333;
            font-size: 14px;
        }

        select, button, input {
            padding: 8px 16px;
            border-radius: 6px;
            border: 1px solid #ddd;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s;
        }

        select:hover, input:hover {
            border-color: #667eea;
        }

        button {
            background: #667eea;
            color: white;
            border: none;
            font-weight: 600;
        }

        button:hover {
            background: #5a6fd6;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }

        button.active {
            background: #764ba2;
        }

        button:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }

        button.secondary {
            background: #6c757d;
        }

        button.secondary:hover {
            background: #5a6268;
        }

        button.success {
            background: #28a745;
        }

        button.success:hover {
            background: #218838;
        }

        button.danger {
            background: #dc3545;
        }

        button.danger:hover {
            background: #c82333;
        }

        /* 按钮组 */
        .btn-group {
            display: flex;
            gap: 5px;
        }

        .btn-group button {
            padding: 8px 16px;
        }

        /* 图表容器 */
        .chart-container {
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            position: relative;
        }

        #chart {
            width: 100%;
            height: 500px;
        }

        /* 信息网格 */
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }

        .info-item {
            padding: 16px;
            background: #f8f9fa;
            border-radius: 8px;
        }

        .info-item h4 {
            color: #666;
            font-size: 12px;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .info-item p {
            color: #333;
            font-size: 20px;
            font-weight: 600;
        }

        /* 均线选项 */
        .ma-options {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }

        .ma-checkbox {
            display: flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
            padding: 6px 12px;
            background: #f0f0f0;
            border-radius: 4px;
            transition: all 0.3s;
            font-size: 13px;
        }

        .ma-checkbox:hover {
            background: #e0e0e0;
        }

        .ma-checkbox input {
            cursor: pointer;
            width: 16px;
            height: 16px;
        }

        .ma-checkbox.checked {
            background: #667eea;
            color: white;
        }

        /* 预测结果卡片 */
        .prediction-result {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }

        .result-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
        }

        .result-card h5 {
            font-size: 12px;
            opacity: 0.9;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .result-card .value {
            font-size: 24px;
            font-weight: 700;
        }

        .result-card.bullish {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        }

        .result-card.bearish {
            background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
        }

        .result-card.neutral {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        }

        /* 回测指标 */
        .backtest-metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }

        .metric-card {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 16px;
            border-left: 4px solid #667eea;
        }

        .metric-card h5 {
            font-size: 12px;
            color: #666;
            margin-bottom: 10px;
        }

        .metric-value {
            font-size: 20px;
            font-weight: 700;
            color: #333;
        }

        .metric-value.positive {
            color: #27ae60;
        }

        .metric-value.negative {
            color: #e74c3c;
        }

        /* 进度条 */
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 10px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea, #764ba2);
            transition: width 0.3s ease;
        }

        /* 加载动画 */
        .loading {
            display: none;
            text-align: center;
            padding: 40px;
            color: #667eea;
        }

        .loading.show {
            display: block;
        }

        /* 日志容器 */
        .log-container {
            background: #1e1e1e;
            color: #00ff00;
            font-family: 'Courier New', monospace;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            max-height: 200px;
            overflow-y: auto;
            font-size: 12px;
        }

        .log-entry {
            margin-bottom: 5px;
            padding: 2px 0;
            border-bottom: 1px solid #333;
        }

        .log-entry.error {
            color: #ff4444;
        }

        .log-entry.success {
            color: #00ff88;
        }

        /* 滑块样式 */
        input[type="range"] {
            -webkit-appearance: none;
            width: 120px;
            height: 6px;
            border-radius: 3px;
            background: #e0e0e0;
            outline: none;
            cursor: pointer;
        }

        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 18px;
            height: 18px;
            border-radius: 50%;
            background: #667eea;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }

        /* 表格样式 */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }

        th {
            background: #f8f9fa;
            font-weight: 600;
            color: #333;
        }

        tr:hover {
            background: #f8f9fa;
        }

        /* 响应式布局 */
        @media (max-width: 768px) {
            .sidebar {
                transform: translateX(-100%);
            }

            .sidebar.open {
                transform: translateX(0);
            }

            .main-content {
                margin-left: 0;
                padding: 16px;
                padding-top: 60px;
            }

            .menu-toggle {
                display: block;
            }

            #chart {
                height: 350px;
            }

            .control-row {
                flex-direction: column;
                align-items: stretch;
            }

            .control-group {
                justify-content: space-between;
            }
        }

        /* 预测图表 */
        .prediction-chart {
            width: 100%;
            max-height: 500px;
            border-radius: 8px;
            margin-top: 20px;
        }

        /* 仓位管理样式 */
        .portfolio-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .portfolio-item {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 16px;
            cursor: pointer;
            transition: all 0.3s;
            border: 2px solid transparent;
        }

        .portfolio-item:hover {
            background: #e9ecef;
        }

        .portfolio-item.active {
            border-color: #667eea;
            background: #f0f4ff;
        }

        .portfolio-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .portfolio-name {
            font-weight: 600;
            font-size: 16px;
        }

        .portfolio-asset {
            background: #667eea;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
        }

        .portfolio-stats {
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: #666;
        }

        .portfolio-return {
            font-weight: 600;
        }

        .portfolio-return.positive {
            color: #27ae60;
        }

        .portfolio-return.negative {
            color: #e74c3c;
        }

        /* 模态框 */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 2000;
            justify-content: center;
            align-items: center;
        }

        .modal.show {
            display: flex;
        }

        .modal-content {
            background: white;
            border-radius: 12px;
            padding: 24px;
            width: 90%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .modal-header h3 {
            font-size: 20px;
        }

        .modal-close {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: #666;
            padding: 0;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .modal-close:hover {
            background: #f0f0f0;
            border-radius: 50%;
        }

        .form-group {
            margin-bottom: 16px;
        }

        .form-group label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            font-size: 14px;
        }

        .form-group input,
        .form-group select {
            width: 100%;
            padding: 10px 12px;
        }

        .form-actions {
            display: flex;
            gap: 12px;
            justify-content: flex-end;
            margin-top: 24px;
        }

        /* 两栏布局 */
        .two-column {
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 20px;
        }

        @media (max-width: 992px) {
            .two-column {
                grid-template-columns: 1fr;
            }
        }

        /* 交易记录 */
        .trade-list {
            max-height: 400px;
            overflow-y: auto;
        }

        .trade-item {
            display: flex;
            justify-content: space-between;
            padding: 12px;
            border-bottom: 1px solid #eee;
            font-size: 14px;
        }

        .trade-item.buy {
            border-left: 3px solid #27ae60;
        }

        .trade-item.sell {
            border-left: 3px solid #e74c3c;
        }

        .trade-info {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .trade-date {
            font-size: 12px;
            color: #666;
        }

        .trade-type {
            font-weight: 600;
        }

        .trade-type.buy {
            color: #27ae60;
        }

        .trade-type.sell {
            color: #e74c3c;
        }

        /* 权益曲线 */
        .equity-chart {
            width: 100%;
            height: 300px;
            background: #f8f9fa;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #666;
        }

        /* 调仓记录样式 */
        .position-timeline {
            margin-top: 20px;
        }

        .timeline-item {
            display: flex;
            gap: 16px;
            padding: 16px;
            border-left: 3px solid #667eea;
            margin-left: 16px;
            position: relative;
        }

        .timeline-item::before {
            content: '';
            position: absolute;
            left: -9px;
            top: 20px;
            width: 14px;
            height: 14px;
            background: #667eea;
            border-radius: 50%;
        }

        .timeline-date {
            font-size: 12px;
            color: #666;
            min-width: 80px;
        }

        .timeline-content {
            flex: 1;
        }

        .timeline-action {
            font-weight: 600;
            margin-bottom: 4px;
        }

        .timeline-reason {
            font-size: 13px;
            color: #666;
        }

        .timeline-pnl {
            text-align: right;
        }

        .timeline-pnl.positive {
            color: #27ae60;
        }

        .timeline-pnl.negative {
            color: #e74c3c;
        }

        /* 分析面板 */
        .analysis-panel {
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
            border-radius: 12px;
            padding: 20px;
            margin-top: 20px;
        }

        .analysis-panel h4 {
            margin-bottom: 12px;
            color: #333;
        }

        .analysis-content {
            line-height: 1.8;
            color: #555;
        }

        .finding-list, .suggestion-list {
            margin: 12px 0;
            padding-left: 20px;
        }

        .finding-list li, .suggestion-list li {
            margin-bottom: 8px;
            line-height: 1.6;
        }

        /* 空状态 */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #666;
        }

        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }

        .empty-state h3 {
            font-size: 18px;
            margin-bottom: 8px;
            color: #333;
        }

        .empty-state p {
            font-size: 14px;
        }
    </style>
</head>
<body>
    <button class="menu-toggle" onclick="toggleSidebar()">☰</button>

    <div class="app-container">
        <!-- 侧边栏 -->
        <aside class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h1>加密货币分析</h1>
                <p>智能交易决策助手</p>
            </div>

            <nav class="sidebar-nav">
                <div class="nav-item active" onclick="switchPanel('market')">
                    <span class="nav-icon">📊</span>
                    <span>市场数据</span>
                </div>
                <div class="nav-item" onclick="switchPanel('prediction')">
                    <span class="nav-icon">🔮</span>
                    <span>价格预测</span>
                </div>
                <div class="nav-item" onclick="switchPanel('backtest')">
                    <span class="nav-icon">📈</span>
                    <span>策略回测</span>
                </div>
                <div class="nav-item" onclick="switchPanel('portfolio')">
                    <span class="nav-icon">💼</span>
                    <span>仓位管理</span>
                </div>
            </nav>

            <div class="sidebar-footer">
                Crypto Analysis Tool v1.0
            </div>
        </aside>

        <!-- 主内容区 -->
        <main class="main-content">
            <!-- ========== 市场数据面板 ========== -->
            <div id="marketPanel" class="panel active">
                <div class="panel-header">
                    <h2>市场数据</h2>
                    <p>实时价格走势与技术分析</p>
                </div>

                <div class="control-panel">
                    <div class="control-row">
                        <div class="control-group">
                            <label>币种:</label>
                            <select id="assetSelect">
                                <option value="BTC">Bitcoin (BTC)</option>
                                <option value="ETH">Ethereum (ETH)</option>
                            </select>
                        </div>

                        <div class="control-group">
                            <label>时间周期:</label>
                            <div class="btn-group">
                                <button id="btnDay" class="active" onclick="setTimeframe('day')">日K</button>
                                <button id="btnWeek" onclick="setTimeframe('week')">周K</button>
                                <button id="btnMonth" onclick="setTimeframe('month')">月K</button>
                            </div>
                        </div>

                        <div class="control-group">
                            <label>坐标轴:</label>
                            <div class="btn-group">
                                <button id="btnLinear" class="active" onclick="setScale('linear')">普通</button>
                                <button id="btnLog" onclick="setScale('log')">对数</button>
                            </div>
                        </div>
                    </div>

                    <div class="control-row">
                        <div class="control-group">
                            <label>均线:</label>
                            <div class="ma-options">
                                <label class="ma-checkbox" id="ma5"><input type="checkbox" value="5"> MA5</label>
                                <label class="ma-checkbox checked" id="ma10"><input type="checkbox" value="10" checked> MA10</label>
                                <label class="ma-checkbox checked" id="ma20"><input type="checkbox" value="20" checked> MA20</label>
                                <label class="ma-checkbox" id="ma60"><input type="checkbox" value="60"> MA60</label>
                                <label class="ma-checkbox" id="ma120"><input type="checkbox" value="120"> MA120</label>
                                <label class="ma-checkbox" id="ma240"><input type="checkbox" value="240"> MA240</label>
                            </div>
                        </div>
                    </div>

                    <div class="control-row">
                        <div class="control-group">
                            <label>自定义均线:</label>
                            <input type="text" id="customMA" placeholder="例如: 30,90,180" style="width: 150px;">
                            <button onclick="addCustomMA()" style="background: #17a2b8;">添加</button>
                        </div>

                        <div class="control-group">
                            <button onclick="refreshData()" id="refreshBtn">刷新数据</button>
                            <button onclick="updateData()" id="updateBtn" style="background: #28a745;">更新数据源</button>
                        </div>
                    </div>
                </div>

                <div class="chart-container">
                    <div id="chart"></div>
                    <div class="loading" id="loading">加载中...</div>
                </div>

                <div class="info-grid" id="infoGrid">
                    <div class="info-item">
                        <h4>当前价格</h4>
                        <p id="currentPrice">-</p>
                    </div>
                    <div class="info-item">
                        <h4>最高价</h4>
                        <p id="highPrice">-</p>
                    </div>
                    <div class="info-item">
                        <h4>最低价</h4>
                        <p id="lowPrice">-</p>
                    </div>
                    <div class="info-item">
                        <h4>涨跌幅</h4>
                        <p id="changePercent">-</p>
                    </div>
                    <div class="info-item">
                        <h4>数据范围</h4>
                        <p id="dateRange">-</p>
                    </div>
                    <div class="info-item">
                        <h4>数据条数</h4>
                        <p id="dataCount">-</p>
                    </div>
                </div>
            </div>

            <!-- ========== 价格预测面板 ========== -->
            <div id="predictionPanel" class="panel">
                <div class="panel-header">
                    <h2>价格预测</h2>
                    <p>基于多种策略的未来价格预测</p>
                </div>

                <div class="card">
                    <div class="control-row">
                        <div class="control-group">
                            <label>币种:</label>
                            <select id="predAssetSelect">
                                <option value="BTC">Bitcoin (BTC)</option>
                                <option value="ETH">Ethereum (ETH)</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>预测天数:</label>
                            <select id="forecastDays">
                                <option value="3">3天</option>
                                <option value="7" selected>7天</option>
                                <option value="14">14天</option>
                                <option value="30">30天</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>模拟次数:</label>
                            <select id="simulationCount">
                                <option value="1000">1,000次</option>
                                <option value="5000" selected>5,000次</option>
                                <option value="10000">10,000次</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>策略:</label>
                            <select id="predictionStrategy">
                                <option value="monte_carlo">蒙特卡洛模拟</option>
                                <option value="trend_following">趋势跟踪</option>
                                <option value="mean_reversion">均值回归</option>
                                <option value="ensemble">策略组合</option>
                                <option value="regime_aware">状态感知</option>
                                <option value="lstm">LSTM深度学习</option>
                            </select>
                        </div>
                        <button onclick="runPrediction()" id="predictBtn" style="background: #e74c3c;">运行预测</button>
                    </div>
                </div>

                <div id="predictionResult" style="display: none;">
                    <div class="prediction-result">
                        <div class="result-card">
                            <h5>当前价格</h5>
                            <div class="value" id="predCurrentPrice">-</div>
                        </div>
                        <div class="result-card" id="predExpectedCard">
                            <h5>预期价格</h5>
                            <div class="value" id="predExpectedPrice">-</div>
                        </div>
                        <div class="result-card" id="predUpProbCard">
                            <h5>上涨概率</h5>
                            <div class="value" id="predUpProb">-</div>
                        </div>
                        <div class="result-card" id="predDownProbCard">
                            <h5>下跌概率</h5>
                            <div class="value" id="predDownProb">-</div>
                        </div>
                        <div class="result-card">
                            <h5>预期收益率</h5>
                            <div class="value" id="predReturn">-</div>
                        </div>
                        <div class="result-card">
                            <h5>90%置信区间</h5>
                            <div class="value" style="font-size: 16px;" id="predConfidence">-</div>
                        </div>
                    </div>
                    <div class="card">
                        <img id="predictionChart" class="prediction-chart" style="display: none;">
                    </div>
                </div>

                <div id="predictionLoading" class="loading">
                    正在进行蒙特卡洛模拟...
                    <div class="progress-bar" style="margin-top: 20px; max-width: 400px; margin-left: auto; margin-right: auto;">
                        <div class="progress-fill" id="predictionProgress" style="width: 0%;"></div>
                    </div>
                </div>
            </div>

            <!-- ========== 策略回测面板 ========== -->
            <div id="backtestPanel" class="panel">
                <div class="panel-header">
                    <h2>策略回测</h2>
                    <p>回测交易策略表现，支持详细调仓记录和AI分析</p>
                </div>

                <!-- 回测配置 -->
                <div class="card">
                    <h3 style="margin-bottom: 16px; font-size: 16px;">回测配置</h3>
                    <div class="control-row">
                        <div class="control-group">
                            <label>币种:</label>
                            <select id="backtestAssetSelect">
                                <option value="BTC">Bitcoin (BTC)</option>
                                <option value="ETH">Ethereum (ETH)</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>开始日期:</label>
                            <input type="date" id="backtestStartDate" value="2024-01-01">
                        </div>
                        <div class="control-group">
                            <label>结束日期:</label>
                            <input type="date" id="backtestEndDate">
                        </div>
                        <div class="control-group">
                            <label>策略:</label>
                            <select id="backtestStrategy">
                                <option value="monte_carlo">蒙特卡洛模拟</option>
                                <option value="trend_following" selected>趋势跟踪</option>
                                <option value="mean_reversion">均值回归</option>
                                <option value="ensemble">策略组合</option>
                                <option value="regime_aware">状态感知</option>
                                <option value="lstm">LSTM深度学习</option>
                            </select>
                        </div>
                    </div>
                    <div class="control-row">
                        <div class="control-group">
                            <label>预测天数:</label>
                            <select id="backtestForecastDays">
                                <option value="3">3天</option>
                                <option value="7" selected>7天</option>
                                <option value="14">14天</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>步进天数:</label>
                            <select id="backtestStepDays">
                                <option value="7" selected>7天</option>
                                <option value="14">14天</option>
                                <option value="30">30天</option>
                            </select>
                        </div>
                        <div class="control-group">
                            <label>初始资金:</label>
                            <input type="number" id="backtestInitialCapital" value="10000" style="width: 100px;">
                            <span>USDT</span>
                        </div>
                        <div class="control-group">
                            <label>仓位比例:</label>
                            <input type="range" id="backtestPositionSize" min="10" max="100" value="80" oninput="document.getElementById('posSizeValue').textContent = this.value + '%'">
                            <span id="posSizeValue">80%</span>
                        </div>
                        <button onclick="runBacktest()" id="backtestBtn" style="background: #9b59b6;">运行回测</button>
                    </div>
                </div>

                <!-- 策略参数 -->
                <div class="card">
                    <h3 style="margin-bottom: 16px; font-size: 16px;">策略参数</h3>
                    <div class="control-row">
                        <div class="control-group">
                            <label>做多阈值 (%):</label>
                            <input type="range" id="longThreshold" min="50" max="80" value="60" oninput="updateThresholdDisplay('long', this.value)">
                            <span id="longThresholdValue" style="font-weight: 600; color: #27ae60;">60%</span>
                        </div>
                        <div class="control-group">
                            <label>做空阈值 (%):</label>
                            <input type="range" id="shortThreshold" min="20" max="50" value="40" oninput="updateThresholdDisplay('short', this.value)">
                            <span id="shortThresholdValue" style="font-weight: 600; color: #e74c3c;">40%</span>
                        </div>
                        <div class="control-group">
                            <label>止损 (%):</label>
                            <input type="number" id="stopLossPct" value="10" style="width: 60px;">
                        </div>
                        <div class="control-group">
                            <label>止盈 (%):</label>
                            <input type="number" id="takeProfitPct" value="20" style="width: 60px;">
                        </div>
                        <div class="control-group">
                            <label>复利:</label>
                            <input type="checkbox" id="useCompound" checked style="width: 20px; height: 20px;">
                        </div>
                    </div>
                </div>

                <!-- 回测加载 -->
                <div id="backtestLoading" class="loading">
                    正在进行回测...
                    <div class="progress-bar" style="margin-top: 20px; max-width: 400px; margin-left: auto; margin-right: auto;">
                        <div class="progress-fill" id="backtestProgress" style="width: 0%;"></div>
                    </div>
                </div>

                <!-- 回测结果 -->
                <div id="backtestResult" style="display: none;">
                    <!-- 收益对比 -->
                    <div class="card">
                        <h3 style="margin-bottom: 16px; font-size: 16px;">策略 vs 买入持有对比</h3>
                        <div class="backtest-metrics">
                            <div class="metric-card" style="border-left: 4px solid #667eea;">
                                <h5>策略年化收益率</h5>
                                <div class="metric-value" id="btAnnualReturn">-</div>
                            </div>
                            <div class="metric-card" style="border-left: 4px solid #95a5a6;">
                                <h5>买入持有年化收益</h5>
                                <div class="metric-value" id="btBuyHoldAnnual">-</div>
                            </div>
                            <div class="metric-card" style="border-left: 4px solid #27ae60;">
                                <h5>超额年化收益</h5>
                                <div class="metric-value" id="btExcessReturn">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>策略总收益率</h5>
                                <div class="metric-value" id="btReturn">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>买入持有总收益</h5>
                                <div class="metric-value" id="btBuyHoldReturn">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>回测期间天数</h5>
                                <div class="metric-value" id="btPeriodDays">-</div>
                            </div>
                        </div>
                    </div>

                    <!-- AI分析面板 -->
                    <div id="analysisPanel" class="analysis-panel" style="display: none;">
                        <h4>🤖 Claude AI 分析</h4>
                        <div id="analysisContent" class="analysis-content"></div>
                        <div id="analysisLoading" style="display: none; padding: 20px; text-align: center;">
                            正在分析中...
                        </div>
                    </div>

                    <div style="text-align: center; margin: 16px 0;">
                        <button id="analyzeBtn" onclick="analyzeBacktest()" style="display: none; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                            🤖 分析策略表现
                        </button>
                    </div>

                    <!-- 风险指标 -->
                    <div class="card">
                        <h3 style="margin-bottom: 16px; font-size: 16px;">风险指标</h3>
                        <div class="backtest-metrics">
                            <div class="metric-card">
                                <h5>策略最大回撤</h5>
                                <div class="metric-value" id="btDrawdown">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>买入持有最大回撤</h5>
                                <div class="metric-value" id="btBuyHoldDrawdown">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>年化波动率</h5>
                                <div class="metric-value" id="btVolatility">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>VaR 95%</h5>
                                <div class="metric-value" id="btVaR">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>夏普比率</h5>
                                <div class="metric-value" id="btSharpe">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>买入持有夏普</h5>
                                <div class="metric-value" id="btBuyHoldSharpe">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>胜率</h5>
                                <div class="metric-value" id="btWinRate">-</div>
                            </div>
                            <div class="metric-card">
                                <h5>盈亏比</h5>
                                <div class="metric-value" id="btPLRatio">-</div>
                            </div>
                        </div>
                    </div>

                    <!-- 图表 -->
                    <div class="card">
                        <img id="backtestChart" class="prediction-chart" style="display: none;">
                    </div>

                    <!-- 调仓记录 -->
                    <div class="card">
                        <h3 style="margin-bottom: 16px; font-size: 16px;">调仓记录</h3>
                        <div id="tradesTable" style="overflow-x: auto;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>入场日期</th>
                                        <th>出场日期</th>
                                        <th>方向</th>
                                        <th>入场价</th>
                                        <th>出场价</th>
                                        <th>盈亏</th>
                                        <th>出场原因</th>
                                    </tr>
                                </thead>
                                <tbody id="tradesTableBody"></tbody>
                            </table>
                        </div>
                    </div>

                    <!-- 仓位时间线 -->
                    <div class="card">
                        <h3 style="margin-bottom: 16px; font-size: 16px;">仓位变化时间线</h3>
                        <div id="positionTimeline" class="position-timeline"></div>
                    </div>
                </div>
            </div>

            <!-- ========== 仓位管理面板 ========== -->
            <div id="portfolioPanel" class="panel">
                <div class="panel-header">
                    <h2>仓位管理</h2>
                    <p>管理模拟仓位，跟踪收益表现</p>
                </div>

                <div class="two-column">
                    <!-- 左侧：仓位列表 -->
                    <div>
                        <div class="card">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                                <h3 style="font-size: 16px;">我的仓位</h3>
                                <button onclick="openPortfolioModal()" style="padding: 6px 12px; font-size: 13px;">+ 新建仓位</button>
                            </div>
                            <div id="portfolioList" class="portfolio-list">
                                <div class="empty-state">
                                    <div class="empty-state-icon">💼</div>
                                    <h3>暂无仓位</h3>
                                    <p>点击上方按钮创建第一个模拟仓位</p>
                                </div>
                            </div>
                        </div>

                        <!-- 仓位对比 -->
                        <div class="card">
                            <h3 style="margin-bottom: 16px; font-size: 16px;">收益对比</h3>
                            <div id="portfolioComparison">
                                <div class="empty-state" style="padding: 30px;">
                                    <p>创建多个仓位后可进行收益对比</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 右侧：仓位详情 -->
                    <div>
                        <div id="portfolioDetail" style="display: none;">
                            <div class="card">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                                    <div>
                                        <h3 id="detailPortfolioName" style="font-size: 18px;">-</h3>
                                        <span id="detailPortfolioAsset" class="portfolio-asset">-</span>
                                    </div>
                                    <div>
                                        <button onclick="openTradeModal()" style="margin-right: 8px;">添加交易</button>
                                        <button onclick="deletePortfolio()" class="danger" style="background: #dc3545;">删除</button>
                                    </div>
                                </div>

                                <div class="backtest-metrics" style="margin-bottom: 20px;">
                                    <div class="metric-card">
                                        <h5>持仓数量</h5>
                                        <div class="metric-value" id="detailQuantity">-</div>
                                    </div>
                                    <div class="metric-card">
                                        <h5>平均成本</h5>
                                        <div class="metric-value" id="detailAvgCost">-</div>
                                    </div>
                                    <div class="metric-card">
                                        <h5>当前价格</h5>
                                        <div class="metric-value" id="detailCurrentPrice">-</div>
                                    </div>
                                    <div class="metric-card">
                                        <h5>浮动盈亏</h5>
                                        <div class="metric-value" id="detailUnrealizedPnl">-</div>
                                    </div>
                                    <div class="metric-card">
                                        <h5>已实现盈亏</h5>
                                        <div class="metric-value" id="detailRealizedPnl">-</div>
                                    </div>
                                    <div class="metric-card">
                                        <h5>总收益率</h5>
                                        <div class="metric-value" id="detailTotalReturn">-</div>
                                    </div>
                                </div>

                                <h4 style="margin-bottom: 12px; font-size: 14px;">权益曲线</h4>
                                <div id="equityChart" class="equity-chart">加载中...</div>
                            </div>

                            <div class="card">
                                <h3 style="margin-bottom: 16px; font-size: 16px;">交易记录</h3>
                                <div id="tradeList" class="trade-list">
                                    <p style="text-align: center; color: #666; padding: 20px;">暂无交易记录</p>
                                </div>
                            </div>
                        </div>

                        <div id="portfolioEmpty" class="card">
                            <div class="empty-state">
                                <div class="empty-state-icon">📊</div>
                                <h3>选择一个仓位</h3>
                                <p>点击左侧仓位查看详情和交易记录</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 全局日志 -->
            <div class="log-container" id="logContainer">
                <div class="log-entry">系统就绪...</div>
            </div>
        </main>
    </div>

    <!-- 新建仓位模态框 -->
    <div id="portfolioModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3>新建仓位</h3>
                <button class="modal-close" onclick="closePortfolioModal()">&times;</button>
            </div>
            <div class="form-group">
                <label>仓位名称</label>
                <input type="text" id="newPortfolioName" placeholder="例如：BTC长期持有">
            </div>
            <div class="form-group">
                <label>币种</label>
                <select id="newPortfolioAsset">
                    <option value="BTC">Bitcoin (BTC)</option>
                    <option value="ETH">Ethereum (ETH)</option>
                </select>
            </div>
            <div class="form-group">
                <label>初始资金 (USDT)</label>
                <input type="number" id="newPortfolioCapital" value="10000">
            </div>
            <div class="form-group">
                <label>模式</label>
                <select id="newPortfolioMode">
                    <option value="manual">手动交易</option>
                    <option value="strategy">策略跟踪</option>
                </select>
            </div>
            <div class="form-group" id="strategySelectGroup" style="display: none;">
                <label>跟踪策略</label>
                <select id="newPortfolioStrategy">
                    <option value="trend_following">趋势跟踪</option>
                    <option value="mean_reversion">均值回归</option>
                    <option value="ensemble">策略组合</option>
                    <option value="lstm">LSTM深度学习</option>
                </select>
            </div>
            <div class="form-actions">
                <button class="secondary" onclick="closePortfolioModal()">取消</button>
                <button onclick="createPortfolio()">创建</button>
            </div>
        </div>
    </div>

    <!-- 添加交易模态框 -->
    <div id="tradeModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3>添加交易</h3>
                <button class="modal-close" onclick="closeTradeModal()">&times;</button>
            </div>
            <div class="form-group">
                <label>交易类型</label>
                <select id="tradeType">
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                </select>
            </div>
            <div class="form-group">
                <label>交易日期</label>
                <input type="date" id="tradeDate">
            </div>
            <div class="form-group">
                <label>价格 (USDT)</label>
                <input type="number" id="tradePrice" step="0.01">
            </div>
            <div class="form-group">
                <label>数量</label>
                <input type="number" id="tradeQuantity" step="0.0001">
            </div>
            <div class="form-group">
                <label>手续费 (USDT)</label>
                <input type="number" id="tradeFee" value="0" step="0.01">
            </div>
            <div class="form-group">
                <label>备注</label>
                <input type="text" id="tradeNote" placeholder="可选">
            </div>
            <div class="form-actions">
                <button class="secondary" onclick="closeTradeModal()">取消</button>
                <button onclick="addTrade()">添加</button>
            </div>
        </div>
    </div>

    <script>
        // ============== 全局状态 ==============
        let chart = null;
        let candleSeries = null;
        let maSeries = {};
        let currentState = {
            asset: 'BTC',
            timeframe: 'day',
            scale: 'linear',
            maPeriods: [10, 20]
        };

        // 当前回测结果缓存（用于AI分析）
        let currentBacktestResult = null;

        // 当前选中的仓位
        let currentPortfolioId = null;

        // ============== 面板切换 ==============
        function switchPanel(panelName) {
            // 更新导航状态
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            event.target.closest('.nav-item').classList.add('active');

            // 切换面板显示
            document.querySelectorAll('.panel').forEach(panel => {
                panel.classList.remove('active');
            });
            document.getElementById(panelName + 'Panel').classList.add('active');

            // 移动端关闭侧边栏
            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
            }

            log(`切换到${panelName}面板`);

            // 面板特定初始化
            if (panelName === 'portfolio') {
                loadPortfolios();
            }
        }

        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
        }

        // ============== 初始化 ==============
        document.addEventListener('DOMContentLoaded', function() {
            initChart();
            loadData();
            setupEventListeners();

            // 同步币种选择器
            document.getElementById('predAssetSelect').value = currentState.asset;
            document.getElementById('backtestAssetSelect').value = currentState.asset;

            // 初始化日期
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('backtestEndDate').value = today;
            document.getElementById('tradeDate').value = today;
        });

        // ============== 图表初始化 ==============
        function initChart() {
            const chartContainer = document.getElementById('chart');

            chart = LightweightCharts.createChart(chartContainer, {
                width: chartContainer.clientWidth,
                height: 600,
                layout: {
                    background: { color: '#ffffff' },
                    textColor: '#333333',
                },
                grid: {
                    vertLines: { color: '#e0e0e0' },
                    horzLines: { color: '#e0e0e0' },
                },
                crosshair: {
                    mode: LightweightCharts.CrosshairMode.Normal,
                },
                rightPriceScale: {
                    borderColor: '#e0e0e0',
                    scaleMargins: {
                        top: 0.1,
                        bottom: 0.1,
                    },
                },
                timeScale: {
                    borderColor: '#e0e0e0',
                    timeVisible: true,
                },
            });

            // 创建K线系列
            candleSeries = chart.addCandlestickSeries({
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderUpColor: '#26a69a',
                borderDownColor: '#ef5350',
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
            });

            // 响应式调整
            window.addEventListener('resize', () => {
                chart.applyOptions({
                    width: chartContainer.clientWidth,
                });
            });

            // 初始化价格轴类型（对数/线性）
            chart.priceScale('right').applyOptions({
                mode: currentState.scale === 'log' ?
                    LightweightCharts.PriceScaleMode.Logarithmic :
                    LightweightCharts.PriceScaleMode.Normal,
            });
        }

        // ============== 数据加载 ==============
        async function loadData() {
            showLoading(true);
            log('正在加载数据...');

            try {
                const maPeriods = getSelectedMAPeriods();
                log(`选中的均线周期: ${maPeriods.join(', ') || '无'}`);

                const url = `/api/data/${currentState.asset}/with_ma?` +
                    `timeframe=${currentState.timeframe}&` +
                    `ma_periods=${maPeriods.join(',')}`;

                const response = await fetch(url);
                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                updateChart(result.data);
                updateInfo(result.data);
                log(`成功加载 ${result.count} 条数据`, 'success');

            } catch (error) {
                log(`加载失败: ${error.message}`, 'error');
            } finally {
                showLoading(false);
            }
        }

        // ============== 更新图表 ==============
        function updateChart(data) {
            if (!data || data.length === 0) return;

            // 转换数据格式
            const chartData = data.map(item => ({
                time: item.time,
                open: item.open,
                high: item.high,
                low: item.low,
                close: item.close,
            }));

            // 设置K线数据
            candleSeries.setData(chartData);

            // 清除旧的均线
            Object.values(maSeries).forEach(series => {
                chart.removeSeries(series);
            });
            maSeries = {};

            // 添加均线
            const colors = {
                'MA5': '#ff6b6b',
                'MA10': '#4ecdc4',
                'MA20': '#45b7d1',
                'MA60': '#96ceb4',
                'MA120': '#ffeaa7',
                'MA240': '#dfe6e9',
            };

            // 获取当前选中的MA周期
            const maPeriods = getSelectedMAPeriods();
            log(`绘制均线: MA${maPeriods.join(', MA') || '无'}`);

            maPeriods.forEach(period => {
                const maKey = `MA${period}`;
                const maData = data
                    .filter(item => item[maKey] !== null && item[maKey] !== undefined)
                    .map(item => ({
                        time: item.time,
                        value: item[maKey],
                    }));

                if (maData.length > 0) {
                    const lineSeries = chart.addLineSeries({
                        color: colors[maKey] || '#999999',
                        lineWidth: 2,
                        title: maKey,
                    });
                    lineSeries.setData(maData);
                    maSeries[maKey] = lineSeries;
                }
            });

            // 设置坐标轴类型（使用右侧价格轴，因为图表配置中使用了 rightPriceScale）
            chart.priceScale('right').applyOptions({
                mode: currentState.scale === 'log' ?
                    LightweightCharts.PriceScaleMode.Logarithmic :
                    LightweightCharts.PriceScaleMode.Normal,
            });

            // 自适应缩放
            chart.timeScale().fitContent();
        }

        // ============== 更新信息面板 ==============
        function updateInfo(data) {
            if (!data || data.length === 0) return;

            const latest = data[data.length - 1];
            const first = data[0];

            // 计算最高价和最低价
            const high = Math.max(...data.map(d => d.high));
            const low = Math.min(...data.map(d => d.low));

            // 计算涨跌幅
            const change = latest.close - first.open;
            const changePercent = (change / first.open) * 100;

            document.getElementById('currentPrice').textContent = `$${latest.close.toLocaleString()}`;
            document.getElementById('highPrice').textContent = `$${high.toLocaleString()}`;
            document.getElementById('lowPrice').textContent = `$${low.toLocaleString()}`;
            document.getElementById('changePercent').textContent =
                `${change >= 0 ? '+' : ''}${changePercent.toFixed(2)}%`;
            document.getElementById('changePercent').style.color = change >= 0 ? '#28a745' : '#dc3545';
            document.getElementById('dateRange').textContent = `${latest.date} ~ ${first.date}`;
            document.getElementById('dataCount').textContent = data.length;
        }

        // ============== 交互控制 ==============
        function setTimeframe(timeframe) {
            currentState.timeframe = timeframe;

            // 更新按钮样式
            ['day', 'week', 'month'].forEach(tf => {
                const btn = document.getElementById(`btn${tf.charAt(0).toUpperCase() + tf.slice(1)}`);
                btn.classList.toggle('active', tf === timeframe);
            });

            loadData();
        }

        function setScale(scale) {
            currentState.scale = scale;

            // 更新按钮样式
            document.getElementById('btnLinear').classList.toggle('active', scale === 'linear');
            document.getElementById('btnLog').classList.toggle('active', scale === 'log');

            // 更新图表（使用右侧价格轴）
            chart.priceScale('right').applyOptions({
                mode: scale === 'log' ?
                    LightweightCharts.PriceScaleMode.Logarithmic :
                    LightweightCharts.PriceScaleMode.Normal,
            });

            log(`切换到${scale === 'log' ? '对数' : '普通'}坐标`);
        }

        // 存储自定义MA周期
        let customMAPeriods = [];

        function getSelectedMAPeriods() {
            const periods = [];
            document.querySelectorAll('.ma-checkbox input:checked').forEach(cb => {
                periods.push(parseInt(cb.value));
            });

            // 添加自定义均线
            customMAPeriods.forEach(val => {
                if (!periods.includes(val)) {
                    periods.push(val);
                }
            });

            return periods.sort((a, b) => a - b);
        }

        function addCustomMA() {
            const input = document.getElementById('customMA');
            const value = input.value.trim();

            if (!value) {
                log('请输入均线周期', 'error');
                return;
            }

            // 解析输入的周期
            const newPeriods = [];
            value.split(',').forEach(p => {
                const val = parseInt(p.trim());
                if (val && val > 0 && !customMAPeriods.includes(val)) {
                    newPeriods.push(val);
                    customMAPeriods.push(val);
                }
            });

            if (newPeriods.length > 0) {
                log(`已添加均线: MA${newPeriods.join(', MA')}`, 'success');
                input.value = '';  // 清空输入框
                loadData();  // 重新加载数据
            } else {
                log('没有有效的均线周期被添加', 'error');
            }
        }

        function setupEventListeners() {
            // 币种选择
            document.getElementById('assetSelect').addEventListener('change', (e) => {
                currentState.asset = e.target.value;
                loadData();
            });

            // 均线复选框事件绑定
            const maCheckboxes = document.querySelectorAll('.ma-checkbox input');
            log(`绑定 ${maCheckboxes.length} 个均线复选框事件`);
            maCheckboxes.forEach(cb => {
                cb.addEventListener('change', function() {
                    log(`均线 MA${this.value} ${this.checked ? '选中' : '取消'}`);
                    this.parentElement.classList.toggle('checked', this.checked);
                    loadData();
                });
            });

        }

        // ============== 数据更新 ==============
        async function updateData() {
            const btn = document.getElementById('updateBtn');
            btn.disabled = true;
            btn.textContent = '更新中...';
            log('正在从Yahoo Finance更新数据...');

            try {
                const response = await fetch(`/api/update/${currentState.asset}`, {
                    method: 'POST',
                });
                const result = await response.json();

                if (result.success) {
                    log(result.message, 'success');
                    loadData();
                } else {
                    throw new Error(result.error);
                }

            } catch (error) {
                log(`更新失败: ${error.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = '更新数据源';
            }
        }

        function refreshData() {
            loadData();
        }

        // ============== 标签页切换 (旧版，保留兼容) ==============
        function switchTab(tab) {
            // 切换按钮状态
            document.querySelectorAll('.tab').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');

            // 切换内容显示
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(tab + 'Tab').classList.add('active');
        }

        // ============== 预测功能 ==============
        async function runPrediction() {
            const asset = document.getElementById('predAssetSelect').value;
            const btn = document.getElementById('predictBtn');
            const resultDiv = document.getElementById('predictionResult');
            const loadingDiv = document.getElementById('predictionLoading');

            const forecastDays = document.getElementById('forecastDays').value;
            const simulations = document.getElementById('simulationCount').value;
            const strategy = document.getElementById('predictionStrategy').value;

            btn.disabled = true;
            resultDiv.style.display = 'none';
            loadingDiv.classList.add('show');

            log(`开始预测 ${asset}，策略: ${strategy}，天数: ${forecastDays}`);

            try {
                // 先获取预测数据
                const response = await fetch(
                    `/api/predict/${asset}?days=${forecastDays}&simulations=${simulations}&strategy=${strategy}`
                );
                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                // 显示结果
                document.getElementById('predCurrentPrice').textContent = `$${result.current_price.toLocaleString()}`;
                document.getElementById('predExpectedPrice').textContent = `$${result.predicted_price_mean.toLocaleString()}`;
                document.getElementById('predUpProb').textContent = `${result.probabilities.up.toFixed(1)}%`;
                document.getElementById('predDownProb').textContent = `${result.probabilities.down.toFixed(1)}%`;
                document.getElementById('predReturn').textContent = `${result.expected_return >= 0 ? '+' : ''}${result.expected_return.toFixed(2)}%`;
                document.getElementById('predConfidence').textContent = `$${result.confidence_interval.low.toLocaleString()} ~ $${result.confidence_interval.high.toLocaleString()}`;

                // 根据涨跌设置卡片颜色
                const expectedCard = document.getElementById('predExpectedCard');
                const upProbCard = document.getElementById('predUpProbCard');
                const downProbCard = document.getElementById('predDownProbCard');

                expectedCard.className = 'result-card ' + (result.expected_return >= 0 ? 'bullish' : 'bearish');
                upProbCard.className = 'result-card ' + (result.probabilities.up > 50 ? 'bullish' : 'neutral');
                downProbCard.className = 'result-card ' + (result.probabilities.down > 50 ? 'bearish' : 'neutral');

                // 获取并显示图表
                log('正在生成预测图表...');
                const chartResponse = await fetch(
                    `/api/predict/${asset}/chart?days=${forecastDays}&simulations=${Math.min(simulations, 5000)}`
                );
                const chartResult = await chartResponse.json();

                if (chartResult.image) {
                    document.getElementById('predictionChart').src = chartResult.image;
                    document.getElementById('predictionChart').style.display = 'block';
                }

                resultDiv.style.display = 'block';
                log('预测完成', 'success');

            } catch (error) {
                log(`预测失败: ${error.message}`, 'error');
            } finally {
                btn.disabled = false;
                loadingDiv.classList.remove('show');
            }
        }

        // ============== 策略参数控制 ==============
        function updateThresholdDisplay(type, value) {
            document.getElementById(type + 'ThresholdValue').textContent = value + '%';
        }

        // ============== 增强回测功能 ==============
        async function runBacktest() {
            const btn = document.getElementById('backtestBtn');
            const resultDiv = document.getElementById('backtestResult');
            const loadingDiv = document.getElementById('backtestLoading');

            const asset = document.getElementById('backtestAssetSelect').value;
            const startDate = document.getElementById('backtestStartDate').value;
            const endDate = document.getElementById('backtestEndDate').value;
            const forecastDays = document.getElementById('backtestForecastDays').value;
            const stepDays = document.getElementById('backtestStepDays').value;
            const strategy = document.getElementById('backtestStrategy').value;
            const initialCapital = document.getElementById('backtestInitialCapital').value;
            const positionSize = document.getElementById('backtestPositionSize').value / 100;
            const stopLoss = document.getElementById('stopLossPct').value;
            const takeProfit = document.getElementById('takeProfitPct').value;
            const useCompound = document.getElementById('useCompound').checked;

            if (!startDate || !endDate) {
                log('请选择开始和结束日期', 'error');
                return;
            }

            btn.disabled = true;
            resultDiv.style.display = 'none';
            loadingDiv.classList.add('show');

            log(`开始回测 ${asset}，策略: ${strategy}，期间: ${startDate} 至 ${endDate}`);

            try {
                // 使用增强回测引擎
                const response = await fetch(`/api/backtest/${asset}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        strategy: strategy,
                        start_date: startDate,
                        end_date: endDate,
                        forecast_days: parseInt(forecastDays),
                        step_days: parseInt(stepDays),
                        engine: 'enhanced',
                        enhanced_params: {
                            initial_capital: parseFloat(initialCapital),
                            position_size: positionSize,
                            use_compound: useCompound,
                            stop_loss_pct: parseFloat(stopLoss),
                            take_profit_pct: parseFloat(takeProfit),
                            rebalance_freq: 'daily'
                        }
                    })
                });

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                // 缓存结果用于AI分析
                currentBacktestResult = result;

                // 显示回测指标
                displayBacktestMetrics(result);

                // 显示调仓记录
                displayTrades(result.trades);

                // 显示仓位时间线
                displayPositionTimeline(result.position_history);

                // 显示图表
                if (result.equity_curve_chart) {
                    document.getElementById('backtestChart').src = result.equity_curve_chart;
                    document.getElementById('backtestChart').style.display = 'block';
                }

                // 如果策略不如买入持有，显示分析按钮
                const analyzeBtn = document.getElementById('analyzeBtn');
                const analysisPanel = document.getElementById('analysisPanel');
                if (result.metrics.trading_return < result.metrics.buy_hold_return) {
                    analyzeBtn.style.display = 'inline-block';
                    analysisPanel.style.display = 'none';
                } else {
                    analyzeBtn.style.display = 'none';
                    analysisPanel.style.display = 'none';
                }

                resultDiv.style.display = 'block';
                log(`回测完成，策略收益: ${result.metrics.trading_return.toFixed(2)}%，买入持有: ${result.metrics.buy_hold_return.toFixed(2)}%`, 'success');

            } catch (error) {
                log(`回测失败: ${error.message}`, 'error');
            } finally {
                btn.disabled = false;
                loadingDiv.classList.remove('show');
            }
        }

        function displayBacktestMetrics(result) {
            const m = result.metrics;

            // 收益对比
            document.getElementById('btAnnualReturn').textContent = `${m.trading_annual_return >= 0 ? '+' : ''}${m.trading_annual_return.toFixed(2)}%`;
            document.getElementById('btBuyHoldAnnual').textContent = `${m.buy_hold_annual_return >= 0 ? '+' : ''}${m.buy_hold_annual_return.toFixed(2)}%`;
            document.getElementById('btExcessReturn').textContent = `${m.trading_annual_return - m.buy_hold_annual_return >= 0 ? '+' : ''}${(m.trading_annual_return - m.buy_hold_annual_return).toFixed(2)}%`;
            document.getElementById('btReturn').textContent = `${m.trading_return >= 0 ? '+' : ''}${m.trading_return.toFixed(2)}%`;
            document.getElementById('btBuyHoldReturn').textContent = `${m.buy_hold_return >= 0 ? '+' : ''}${m.buy_hold_return.toFixed(2)}%`;
            document.getElementById('btPeriodDays').textContent = `${result.period_days}天`;

            // 设置颜色
            document.getElementById('btAnnualReturn').className = 'metric-value ' + (m.trading_annual_return >= 0 ? 'positive' : 'negative');
            document.getElementById('btExcessReturn').className = 'metric-value ' + (m.trading_annual_return - m.buy_hold_annual_return >= 0 ? 'positive' : 'negative');
            document.getElementById('btReturn').className = 'metric-value ' + (m.trading_return >= 0 ? 'positive' : 'negative');

            // 风险指标
            document.getElementById('btDrawdown').textContent = `${m.max_drawdown.toFixed(2)}%`;
            document.getElementById('btBuyHoldDrawdown').textContent = `${m.buy_hold_max_drawdown.toFixed(2)}%`;
            document.getElementById('btVolatility').textContent = `${m.annual_volatility.toFixed(2)}%`;
            document.getElementById('btVaR').textContent = `${m.var_95.toFixed(2)}%`;
            document.getElementById('btSharpe').textContent = m.trading_sharpe.toFixed(2);
            document.getElementById('btBuyHoldSharpe').textContent = m.buy_hold_sharpe.toFixed(2);
            document.getElementById('btWinRate').textContent = `${m.win_rate.toFixed(2)}%`;
            document.getElementById('btPLRatio').textContent = m.profit_loss_ratio.toFixed(2);
        }

        function displayTrades(trades) {
            const tbody = document.getElementById('tradesTableBody');
            if (!trades || trades.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #666;">暂无交易记录</td></tr>';
                return;
            }

            tbody.innerHTML = trades.map(t => `
                <tr>
                    <td>${t.entry_date}</td>
                    <td>${t.exit_date}</td>
                    <td><span style="color: ${t.direction === 'LONG' ? '#27ae60' : '#e74c3c'}">${t.direction}</span></td>
                    <td>$${t.entry_price.toLocaleString()}</td>
                    <td>$${t.exit_price.toLocaleString()}</td>
                    <td style="color: ${t.pnl >= 0 ? '#27ae60' : '#e74c3c'}">${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}%</td>
                    <td>${t.exit_reason}</td>
                </tr>
            `).join('');
        }

        function displayPositionTimeline(history) {
            const container = document.getElementById('positionTimeline');
            if (!history || history.length === 0) {
                container.innerHTML = '<p style="text-align: center; color: #666;">无仓位变化记录</p>';
                return;
            }

            // 只显示关键变化点
            const changes = [];
            let lastPosition = null;
            history.forEach(h => {
                if (h.position !== lastPosition) {
                    changes.push(h);
                    lastPosition = h.position;
                }
            });

            container.innerHTML = changes.map(h => `
                <div class="timeline-item">
                    <div class="timeline-date">${h.date}</div>
                    <div class="timeline-content">
                        <div class="timeline-action">${h.position === 'LONG' ? '做多' : h.position === 'SHORT' ? '做空' : '平仓'}</div>
                        <div class="timeline-reason">${h.reason || '信号变化'}</div>
                    </div>
                    <div class="timeline-pnl ${(h.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative'}">
                        ${h.unrealized_pnl ? (h.unrealized_pnl >= 0 ? '+' : '') + h.unrealized_pnl.toFixed(2) : '0.00'}%
                    </div>
                </div>
            `).join('');
        }

        // ============== AI 分析功能 ==============
        async function analyzeBacktest() {
            if (!currentBacktestResult) {
                log('请先运行回测', 'error');
                return;
            }

            const analysisPanel = document.getElementById('analysisPanel');
            const analysisContent = document.getElementById('analysisContent');
            const analysisLoading = document.getElementById('analysisLoading');
            const analyzeBtn = document.getElementById('analyzeBtn');

            analyzeBtn.disabled = true;
            analysisPanel.style.display = 'block';
            analysisLoading.style.display = 'block';
            analysisContent.style.display = 'none';

            log('正在请求AI分析...');

            try {
                const response = await fetch(`/api/backtest/${currentBacktestResult.asset}/analyze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        strategy: currentBacktestResult.strategy_name,
                        strategy_params: currentBacktestResult.strategy_params || {},
                        metrics: currentBacktestResult.metrics,
                        trades: currentBacktestResult.trades,
                        position_history: currentBacktestResult.position_history,
                        buy_hold_return: currentBacktestResult.metrics.buy_hold_return,
                        strategy_return: currentBacktestResult.metrics.trading_return,
                        start_date: currentBacktestResult.start_date,
                        end_date: currentBacktestResult.end_date
                    })
                });

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                // 显示分析结果
                analysisContent.innerHTML = `
                    <p>${result.analysis}</p>
                    ${result.key_findings && result.key_findings.length > 0 ? `
                        <h5 style="margin-top: 16px;">🔍 关键发现</h5>
                        <ul class="finding-list">
                            ${result.key_findings.map(f => `<li>${f}</li>`).join('')}
                        </ul>
                    ` : ''}
                    ${result.improvement_suggestions && result.improvement_suggestions.length > 0 ? `
                        <h5 style="margin-top: 16px;">💡 改进建议</h5>
                        <ul class="suggestion-list">
                            ${result.improvement_suggestions.map(s => `<li>${s}</li>`).join('')}
                        </ul>
                    ` : ''}
                `;

                analysisContent.style.display = 'block';
                log('AI分析完成', 'success');

            } catch (error) {
                analysisContent.innerHTML = `<p style="color: #e74c3c;">分析失败: ${error.message}</p>`;
                analysisContent.style.display = 'block';
                log(`AI分析失败: ${error.message}`, 'error');
            } finally {
                analysisLoading.style.display = 'none';
                analyzeBtn.disabled = false;
            }
        }

        // ============== 仓位管理功能 ==============
        async function loadPortfolios() {
            try {
                const response = await fetch('/api/portfolios');
                const portfolios = await response.json();

                const container = document.getElementById('portfolioList');
                if (portfolios.length === 0) {
                    container.innerHTML = `
                        <div class="empty-state">
                            <div class="empty-state-icon">💼</div>
                            <h3>暂无仓位</h3>
                            <p>点击上方按钮创建第一个模拟仓位</p>
                        </div>
                    `;
                    return;
                }

                container.innerHTML = portfolios.map(p => `
                    <div class="portfolio-item ${p.id === currentPortfolioId ? 'active' : ''}" onclick="selectPortfolio(${p.id})">
                        <div class="portfolio-header">
                            <span class="portfolio-name">${p.name}</span>
                            <span class="portfolio-asset">${p.asset_code}</span>
                        </div>
                        <div class="portfolio-stats">
                            <span>持仓: ${p.current_quantity.toFixed(4)}</span>
                            <span>成本: $${p.avg_cost.toFixed(2)}</span>
                            <span class="portfolio-return ${p.total_return >= 0 ? 'positive' : 'negative'}">
                                ${p.total_return >= 0 ? '+' : ''}${p.total_return.toFixed(2)}%
                            </span>
                        </div>
                    </div>
                `).join('');

                // 更新对比面板
                updatePortfolioComparison(portfolios);

            } catch (error) {
                log(`加载仓位失败: ${error.message}`, 'error');
            }
        }

        function updatePortfolioComparison(portfolios) {
            const container = document.getElementById('portfolioComparison');
            if (portfolios.length < 2) {
                container.innerHTML = '<div class="empty-state" style="padding: 30px;"><p>创建多个仓位后可进行收益对比</p></div>';
                return;
            }

            const sorted = [...portfolios].sort((a, b) => b.total_return - a.total_return);
            const maxReturn = Math.max(...sorted.map(p => Math.abs(p.total_return)), 1);

            container.innerHTML = sorted.map((p, i) => `
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                    <span style="width: 24px; text-align: center; font-weight: 600; color: ${i === 0 ? '#f1c40f' : i === 1 ? '#95a5a6' : i === 2 ? '#cd7f32' : '#666'};">${i + 1}</span>
                    <span style="flex: 1; font-size: 14px;">${p.name}</span>
                    <div style="flex: 2; background: #e0e0e0; height: 20px; border-radius: 10px; overflow: hidden;">
                        <div style="width: ${(Math.abs(p.total_return) / maxReturn * 100).toFixed(1)}%; height: 100%; background: ${p.total_return >= 0 ? '#27ae60' : '#e74c3c'};"></div>
                    </div>
                    <span style="width: 70px; text-align: right; font-weight: 600; color: ${p.total_return >= 0 ? '#27ae60' : '#e74c3c'};">${p.total_return >= 0 ? '+' : ''}${p.total_return.toFixed(2)}%</span>
                </div>
            `).join('');
        }

        async function selectPortfolio(id) {
            currentPortfolioId = id;
            try {
                const response = await fetch(`/api/portfolios/${id}`);
                const portfolio = await response.json();

                document.getElementById('portfolioEmpty').style.display = 'none';
                document.getElementById('portfolioDetail').style.display = 'block';

                document.getElementById('detailPortfolioName').textContent = portfolio.name;
                document.getElementById('detailPortfolioAsset').textContent = portfolio.asset_code;
                document.getElementById('detailQuantity').textContent = portfolio.current_quantity.toFixed(4);
                document.getElementById('detailAvgCost').textContent = '$' + portfolio.avg_cost.toFixed(2);
                document.getElementById('detailCurrentPrice').textContent = '$' + portfolio.current_price.toFixed(2);
                document.getElementById('detailUnrealizedPnl').textContent = (portfolio.unrealized_pnl >= 0 ? '+' : '') + portfolio.unrealized_pnl.toFixed(2);
                document.getElementById('detailUnrealizedPnl').className = 'metric-value ' + (portfolio.unrealized_pnl >= 0 ? 'positive' : 'negative');
                document.getElementById('detailRealizedPnl').textContent = (portfolio.realized_pnl >= 0 ? '+' : '') + portfolio.realized_pnl.toFixed(2);
                document.getElementById('detailRealizedPnl').className = 'metric-value ' + (portfolio.realized_pnl >= 0 ? 'positive' : 'negative');
                document.getElementById('detailTotalReturn').textContent = (portfolio.total_return >= 0 ? '+' : '') + portfolio.total_return.toFixed(2) + '%';
                document.getElementById('detailTotalReturn').className = 'metric-value ' + (portfolio.total_return >= 0 ? 'positive' : 'negative');

                // 显示交易记录
                const tradeList = document.getElementById('tradeList');
                if (portfolio.trades && portfolio.trades.length > 0) {
                    tradeList.innerHTML = portfolio.trades.map(t => `
                        <div class="trade-item ${t.trade_type}">
                            <div class="trade-info">
                                <span class="trade-type ${t.trade_type}">${t.trade_type === 'buy' ? '买入' : '卖出'}</span>
                                <span class="trade-date">${t.trade_date}</span>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-weight: 600;">$${t.price.toFixed(2)}</div>
                                <div style="font-size: 12px; color: #666;">${t.quantity.toFixed(4)}</div>
                            </div>
                        </div>
                    `).join('');
                } else {
                    tradeList.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">暂无交易记录</p>';
                }

                // 加载权益曲线
                loadEquityCurve(id);

                // 刷新列表选中状态
                loadPortfolios();

            } catch (error) {
                log(`加载仓位详情失败: ${error.message}`, 'error');
            }
        }

        async function loadEquityCurve(portfolioId) {
            try {
                const response = await fetch(`/api/portfolios/${portfolioId}/equity_curve`);
                const result = await response.json();

                const container = document.getElementById('equityChart');
                if (!result.curve || result.curve.length === 0) {
                    container.innerHTML = '<p style="color: #666;">暂无权益数据</p>';
                    return;
                }

                // 简单的SVG权益曲线
                const width = container.clientWidth || 600;
                const height = 300;
                const padding = 40;

                const values = result.curve.map(d => d.equity);
                const minVal = Math.min(...values);
                const maxVal = Math.max(...values);
                const range = maxVal - minVal || 1;

                const points = result.curve.map((d, i) => {
                    const x = padding + (i / (result.curve.length - 1)) * (width - 2 * padding);
                    const y = height - padding - ((d.equity - minVal) / range) * (height - 2 * padding);
                    return `${x},${y}`;
                }).join(' ');

                const startVal = values[0];
                const endVal = values[values.length - 1];
                const color = endVal >= startVal ? '#27ae60' : '#e74c3c';

                container.innerHTML = `
                    <svg width="${width}" height="${height}" style="width: 100%; height: 100%;">
                        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2"/>
                        <text x="${padding}" y="${padding - 10}" font-size="12" fill="#666">$${maxVal.toFixed(0)}</text>
                        <text x="${padding}" y="${height - padding + 20}" font-size="12" fill="#666">$${minVal.toFixed(0)}</text>
                        <text x="${width - padding}" y="${height - padding + 20}" font-size="12" fill="#666" text-anchor="end">${result.curve.length}天</text>
                    </svg>
                `;

            } catch (error) {
                document.getElementById('equityChart').innerHTML = '<p style="color: #e74c3c;">加载失败</p>';
            }
        }

        // ============== 模态框操作 ==============
        function openPortfolioModal() {
            document.getElementById('portfolioModal').classList.add('show');
        }

        function closePortfolioModal() {
            document.getElementById('portfolioModal').classList.remove('show');
        }

        function openTradeModal() {
            document.getElementById('tradeModal').classList.add('show');
        }

        function closeTradeModal() {
            document.getElementById('tradeModal').classList.remove('show');
        }

        async function createPortfolio() {
            const name = document.getElementById('newPortfolioName').value;
            const asset = document.getElementById('newPortfolioAsset').value;
            const capital = document.getElementById('newPortfolioCapital').value;
            const mode = document.getElementById('newPortfolioMode').value;
            const strategy = mode === 'strategy' ? document.getElementById('newPortfolioStrategy').value : null;

            if (!name) {
                alert('请输入仓位名称');
                return;
            }

            try {
                const response = await fetch('/api/portfolios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name,
                        asset_code: asset,
                        initial_capital: parseFloat(capital),
                        mode,
                        strategy
                    })
                });

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                log(`仓位 "${name}" 创建成功`, 'success');
                closePortfolioModal();
                loadPortfolios();

                // 清空表单
                document.getElementById('newPortfolioName').value = '';

            } catch (error) {
                alert('创建失败: ' + error.message);
            }
        }

        async function addTrade() {
            if (!currentPortfolioId) {
                alert('请先选择仓位');
                return;
            }

            const type = document.getElementById('tradeType').value;
            const date = document.getElementById('tradeDate').value;
            const price = document.getElementById('tradePrice').value;
            const quantity = document.getElementById('tradeQuantity').value;
            const fee = document.getElementById('tradeFee').value;
            const note = document.getElementById('tradeNote').value;

            if (!date || !price || !quantity) {
                alert('请填写完整交易信息');
                return;
            }

            try {
                const response = await fetch(`/api/portfolios/${currentPortfolioId}/trades`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        trade_date: date,
                        trade_type: type,
                        price: parseFloat(price),
                        quantity: parseFloat(quantity),
                        fee: parseFloat(fee) || 0,
                        note
                    })
                });

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                log('交易记录已添加', 'success');
                closeTradeModal();
                selectPortfolio(currentPortfolioId); // 刷新详情

                // 清空表单
                document.getElementById('tradePrice').value = '';
                document.getElementById('tradeQuantity').value = '';

            } catch (error) {
                alert('添加失败: ' + error.message);
            }
        }

        async function deletePortfolio() {
            if (!currentPortfolioId) return;

            if (!confirm('确定要删除这个仓位吗？所有交易记录也将被删除。')) {
                return;
            }

            try {
                const response = await fetch(`/api/portfolios/${currentPortfolioId}`, {
                    method: 'DELETE'
                });

                const result = await response.json();

                if (result.error) {
                    throw new Error(result.error);
                }

                log('仓位已删除', 'success');
                currentPortfolioId = null;
                document.getElementById('portfolioDetail').style.display = 'none';
                document.getElementById('portfolioEmpty').style.display = 'block';
                loadPortfolios();

            } catch (error) {
                alert('删除失败: ' + error.message);
            }
        }

        // 策略模式选择变化
        document.getElementById('newPortfolioMode')?.addEventListener('change', function() {
            document.getElementById('strategySelectGroup').style.display = this.value === 'strategy' ? 'block' : 'none';
        });

        // ============== 工具函数 ==============
        function showLoading(show) {
            const loading = document.getElementById('loading');
            if (show) loading.classList.add('show');
            else loading.classList.remove('show');
        }

        function log(message, type = 'info') {
            const container = document.getElementById('logContainer');
            const entry = document.createElement('div');
            entry.className = `log-entry ${type}`;
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            container.insertBefore(entry, container.firstChild);

            // 限制日志条数
            while (container.children.length > 50) {
                container.removeChild(container.lastChild);
            }
        }
    </script>
</body>
</html>
'''

# ============== 自动更新数据 ==============

def auto_update_data():
    """服务启动时自动检查并更新数据到昨天"""
    from datetime import datetime, timedelta

    print("\n" + "="*50)
    print("自动检查数据更新...")
    print("="*50)

    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    all_updated = True

    for asset_code in SUPPORTED_ASSETS.keys():
        try:
            latest_date = db.get_latest_date(asset_code, DEFAULT_CURRENCY)

            if latest_date is None:
                print(f"\n[{asset_code}] 数据库中没有数据，获取全部历史数据...")
            elif latest_date < yesterday:
                print(f"\n[{asset_code}] 数据需要更新: {latest_date} -> {yesterday}")
            else:
                print(f"[{asset_code}] 数据已是最新 (最新: {latest_date})")
                continue

            # 获取增量数据
            new_data = fetcher.fetch_incremental_data(asset_code, latest_date)

            if new_data:
                count = db.save_price_data(asset_code, DEFAULT_CURRENCY, new_data)
                print(f"  ✓ 成功更新 {count} 条记录")
            else:
                print(f"  ✓ 无需更新")

        except Exception as e:
            print(f"  ✗ 更新失败: {e}")
            all_updated = False

    print("\n" + "="*50)
    if all_updated:
        print("数据检查完成")
    else:
        print("数据检查完成，部分币种更新失败")
    print("="*50 + "\n")

# ============== 启动应用 ==============

if __name__ == '__main__':
    import pandas as pd

    # 启动时自动检查并更新数据
    auto_update_data()

    print("启动加密货币追踪工具 Web服务...")
    print("请访问: http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=True)
