#!/usr/bin/env python3
"""
加密货币追踪工具 - Web后端API
使用Flask提供REST API服务
"""
from flask import Flask, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
import os
import json
import glob
import uuid
import threading
import queue
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd

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

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

db = Database()
fetcher = DataFetcher()

# ============== 模型训练状态管理 ==============
training_jobs = {}
training_lock = threading.Lock()

# 最大保留历史任务数
MAX_HISTORY_JOBS = 10

# 模型元数据缓存: path -> (mtime, {'training_info', 'config'})
_model_meta_cache = {}

# 价格数据缓存: (asset, currency, latest_date) -> 全量日线DataFrame
# latest_date 随数据更新变化，因此数据一更新缓存自动失效
_price_cache = {}
_price_cache_lock = threading.Lock()
_PRICE_CACHE_MAX = 8


def error_response(e, status=500):
    """统一的错误响应：完整堆栈只写服务端日志，不返回给客户端"""
    app.logger.error("请求处理失败: %s\n%s", e, traceback.format_exc())
    return jsonify({'error': str(e)}), status


def bounded(value, default, lo, hi, cast=float):
    """把请求数值限制在安全范围内，非法值回退默认"""
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _get_price_frame(asset_code, currency_code=DEFAULT_CURRENCY, start_date=None, end_date=None):
    """带缓存的行情读取

    全量日线按 (asset, currency, latest_date) 缓存，日期过滤在内存切片完成，
    避免每次图表刷新都全表查询。
    """
    latest_date = db.get_latest_date(asset_code, currency_code)
    key = (asset_code, currency_code, latest_date)

    with _price_cache_lock:
        df = _price_cache.get(key)

    if df is None:
        df = db.get_price_data(asset_code, currency_code)
        with _price_cache_lock:
            if len(_price_cache) >= _PRICE_CACHE_MAX:
                _price_cache.clear()
            _price_cache[key] = df

    if start_date is not None:
        df = df.loc[start_date:]
    if end_date is not None:
        df = df.loc[:end_date]
    return df


def _current_price(asset_code):
    """取某资产最新收盘价（单行查询，避免为了一个数加载全部历史），无数据返回0"""
    try:
        return db.get_latest_price(asset_code, DEFAULT_CURRENCY) or 0
    except Exception:
        return 0


def _serialize_ohlcv(df, ma_periods=()):
    """向量化序列化行情数据（替代逐行iterrows，长序列上快一个数量级）"""
    opens = df['open_price'].tolist()
    highs = df['max_price'].tolist()
    lows = df['min_price'].tolist()
    closes = df['close_price'].tolist()
    dates = df.index.strftime('%Y-%m-%d').tolist()
    ma_cols = {
        f'MA{p}': [None if pd.isna(v) else float(v) for v in df[f'MA{p}']]
        for p in ma_periods if f'MA{p}' in df.columns
    }

    data = []
    for i, ts in enumerate(df.index):
        item = {
            'time': int(ts.timestamp()),
            'date': dates[i],
            'open': opens[i],
            'high': highs[i],
            'low': lows[i],
            'close': closes[i]
        }
        for col, values in ma_cols.items():
            item[col] = values[i]
        data.append(item)
    return data


def _load_model_metadata(best_path):
    """读取checkpoint元数据，按 (路径, mtime) 缓存，避免每次请求反序列化所有 .pth"""
    mtime = os.path.getmtime(best_path)
    cached = _model_meta_cache.get(best_path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        import torch
        checkpoint = torch.load(best_path, map_location='cpu')
        meta = {
            'training_info': checkpoint.get('training_info', {}),
            'config': checkpoint.get('config', {})
        }
    except Exception as e:
        app.logger.warning("读取模型元数据失败 %s: %s", best_path, e)
        meta = {'training_info': {}, 'config': {}}

    _model_meta_cache[best_path] = (mtime, meta)
    return meta


def get_available_models():
    """获取已训练的模型列表（支持多模型）"""
    models = []
    models_dir = 'models'
    if not os.path.exists(models_dir):
        return models

    # 新模式：支持 {asset}_{model_id}_lstm_best.pth 格式
    pattern = f"{models_dir}/*_lstm_best.pth"
    best_models = glob.glob(pattern)

    # 按asset分组
    models_by_asset = {}
    for best_path in best_models:
        filename = os.path.basename(best_path)
        # 解析文件名: {asset}_{model_id}_lstm_best.pth 或 {asset}_lstm_best.pth
        parts = filename.replace('_lstm_best.pth', '').split('_')

        if len(parts) >= 2:
            asset = parts[0].upper()
            model_id = '_'.join(parts[1:]) if len(parts) > 1 else 'default'
        else:
            asset = parts[0].upper() if parts else 'UNKNOWN'
            model_id = 'default'

        if asset not in models_by_asset:
            models_by_asset[asset] = []

        # 获取对应的final模型和历史文件
        final_path = best_path.replace('_best.pth', '.pth')
        history_path = best_path.replace('_best.pth', '_history.json')

        model_info = {
            'asset': asset,
            'model_id': model_id,
            'best_model': {
                'path': best_path,
                'mtime': datetime.fromtimestamp(os.path.getmtime(best_path)).isoformat(),
                'type': 'best'
            },
            'final_model': None,
            'history': None
        }

        # 加载training_info获取详细配置（带缓存）
        meta = _load_model_metadata(best_path)
        model_info['training_info'] = meta['training_info']
        model_info['config'] = meta['config']

        if os.path.exists(final_path):
            model_info['final_model'] = {
                'path': final_path,
                'mtime': datetime.fromtimestamp(os.path.getmtime(final_path)).isoformat(),
                'type': 'final'
            }
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r') as f:
                    model_info['history'] = json.load(f)
            except Exception:
                pass

        models_by_asset[asset].append(model_info)

    # 转换为列表格式
    for asset, model_list in models_by_asset.items():
        models.append({
            'asset': asset,
            'models': model_list
        })

    return models


def cleanup_old_jobs(locked=False):
    """清理过多的已完成历史任务

    Args:
        locked: 如果为True，表示调用者已经持有training_lock
    """
    def _do_cleanup():
        completed = [(jid, job) for jid, job in training_jobs.items()
                     if job.get('status') in ('completed', 'failed', 'stopped')]
        if len(completed) > MAX_HISTORY_JOBS:
            completed.sort(key=lambda x: x[1].get('finished_at', ''), reverse=True)
            for jid, _ in completed[MAX_HISTORY_JOBS:]:
                training_jobs.pop(jid, None)

    if locked:
        _do_cleanup()
    else:
        with training_lock:
            _do_cleanup()


def start_training_job(asset_code, epochs, lr, resume=False, model_id='default',
                       hidden_size=64, num_layers=1, dropout=0.4,
                       batch_size=128, seq_len=60, forecast_horizon=7, patience=10):
    """在后台线程启动训练任务

    同一 asset+model_id 已有运行中的任务时返回 None。
    """
    with training_lock:
        # 检查是否已有同资产+同模型正在运行的任务（与注册新任务在同一临界区，避免竞态）
        for existing in training_jobs.values():
            if (existing['asset'] == asset_code
                    and existing.get('model_id') == model_id
                    and existing['status'] == 'running'):
                return None

        job_id = str(uuid.uuid4())
        progress_queue = queue.Queue()

        job = {
            'id': job_id,
            'asset': asset_code,
            'model_id': model_id,
            'epochs': epochs,
            'lr': lr,
            'hidden_size': hidden_size,
            'num_layers': num_layers,
            'dropout': dropout,
            'batch_size': batch_size,
            'seq_len': seq_len,
            'forecast_horizon': forecast_horizon,
            'patience': patience,
            'resume': resume,
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'finished_at': None,
            'progress': {
                'epoch': 0,
                'total_epochs': epochs,
                'train_loss': None,
                'val_loss': None,
                'train_acc': None,
                'val_acc': None,
                'best_val_loss': None,
                'message': '准备训练数据...'
            },
            'history': [],
            'result': None,
            'error': None,
            'queue': progress_queue
        }
        training_jobs[job_id] = job
        cleanup_old_jobs(locked=True)

    def progress_callback(data):
        # 生产者侧维护任务状态：即使没有SSE客户端在消费，进度也持续更新
        with training_lock:
            job['progress'].update(data)
            if 'epoch' in data and data['epoch'] > 0:
                job['history'].append(data)
        progress_queue.put(data)

    def run_training():
        try:
            import sys
            sys.path.insert(0, os.path.dirname(__file__))
            from scripts.train_model import train_model

            # 更新状态：数据准备中
            progress_queue.put({'message': '正在准备训练数据...', 'epoch': 0, 'total_epochs': epochs})

            device = 'cuda' if torch.cuda.is_available() else 'cpu'

            final_model_path, best_model_path, history = train_model(
                asset_code=asset_code,
                model_id=model_id,
                epochs=epochs,
                lr=lr,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout,
                batch_size=batch_size,
                seq_len=seq_len,
                forecast_horizon=forecast_horizon,
                patience=patience,
                device=device,
                resume=resume,
                checkpoint_interval=1,
                progress_callback=progress_callback
            )

            job['result'] = {
                'final_model': final_model_path,
                'best_model': best_model_path,
                'total_epochs': history.get('train_loss', []).__len__()
            }
            job['status'] = 'completed'
        except Exception as e:
            import traceback
            job['error'] = str(e)
            job['traceback'] = traceback.format_exc()
            job['status'] = 'failed'
            progress_queue.put({'message': f'训练失败: {e}', 'epoch': 0, 'total_epochs': epochs, 'error': True})
        finally:
            job['finished_at'] = datetime.now().isoformat()
            progress_queue.put({'done': True})

    thread = threading.Thread(target=run_training, daemon=True)
    thread.start()

    return job_id


# 导入 torch 用于设备检测
import torch

# ============== API 路由 ==============

@app.route('/')
def index():
    """主页 - 返回前端HTML（模板在 templates/index.html，随Flask自动缓存）"""
    return send_file(os.path.join(app.root_path, 'templates', 'index.html'))


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
        # 获取原始日线数据（带缓存）
        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if df.empty:
            return jsonify({'error': 'No data available'}), 404

        # 根据时间周期聚合
        if timeframe == 'week':
            df = DataAggregator.resample_to_weekly(df)
        elif timeframe == 'month':
            df = DataAggregator.resample_to_monthly(df)

        return jsonify({
            'asset': asset_code,
            'timeframe': timeframe,
            'count': len(df),
            'data': _serialize_ohlcv(df)
        })

    except Exception as e:
        return error_response(e)

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

        # 获取数据（带缓存）
        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if df.empty:
            return jsonify({'error': 'No data available'}), 404

        # 复制一份：计算均线要写入新列，不能污染缓存里的DataFrame
        df = df.copy()

        # 根据时间周期聚合（聚合后再计算MA，因为日线MA不适用于周线/月线）
        if timeframe == 'week':
            df = DataAggregator.resample_to_weekly(df)
        elif timeframe == 'month':
            df = DataAggregator.resample_to_monthly(df)

        # 计算均线（在聚合后计算）
        for period in ma_periods:
            df[f'MA{period}'] = df['close_price'].rolling(window=period, min_periods=1).mean()

        return jsonify({
            'asset': asset_code,
            'timeframe': timeframe,
            'ma_periods': ma_periods,
            'count': len(df),
            'data': _serialize_ohlcv(df, ma_periods)
        })

    except Exception as e:
        return error_response(e)

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
        forecast_days = bounded(request.args.get('days', 7), 7, 1, 365, int)
        simulations = bounded(request.args.get('simulations', 10000), 10000, 100, 100000, int)
        strategy_name = request.args.get('strategy', 'monte_carlo')
        strategy_params = request.args.get('params', {})
        if isinstance(strategy_params, str):
            strategy_params = json.loads(strategy_params)
        if not isinstance(strategy_params, dict):
            return jsonify({'error': 'params 必须是JSON对象'}), 400

        model_path = request.args.get('model_path')
        if model_path:
            strategy_params['model_path'] = model_path

        # 告诉策略当前资产（LSTM按 {asset}_{model_id} 约定找模型）
        strategy_params['asset_code'] = asset_code
        strategy_params.setdefault('n_simulations', simulations)

        # 获取历史数据
        df = _get_price_frame(asset_code, DEFAULT_CURRENCY)

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
        return error_response(e)


@app.route('/api/predict/<asset_code>/chart')
def predict_asset_chart(asset_code):
    """生成预测图表"""
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    try:
        forecast_days = bounded(request.args.get('days', 7), 7, 1, 365, int)
        simulations = bounded(request.args.get('simulations', 5000), 5000, 100, 100000, int)

        from src.btc_predictor import BTCPredictor
        from src.prediction_chart import plot_prediction
        import io
        import base64

        # 复用全局db实例，并告知预测器资产代码（支持ETH）
        predictor = BTCPredictor(forecast_days=forecast_days, n_simulations=simulations,
                                 asset_code=asset_code, db=db,
                                 currency_code=DEFAULT_CURRENCY)
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
        return error_response(e)


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
        # 模型解析优先级：显式 model_path > (asset_code, model_id) 自动查找最佳
        return LSTMStrategy(
            model_path=params.get('model_path'),
            asset_code=params.get('asset_code', 'BTC'),
            model_id=params.get('model_id'),
            seq_len=params.get('seq_len', 60),
            hidden_size=params.get('hidden_size', 128),
            num_layers=params.get('num_layers', 2)
        )
    else:
        raise ValueError(f'Unknown strategy: {strategy_name}')


def run_enhanced_backtest(strategy, df, start_date, end_date, strategy_params,
                          enhanced_params, forecast_days=7):
    """运行增强引擎回测（连续仓位），统一各路由的调用参数"""
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
        forecast_days=forecast_days,
        strategy_params=strategy_params
    )
    return engine, result


def run_standard_backtest(strategy, df, start_date, end_date, forecast_days,
                          step_days, strategy_params):
    """运行标准引擎回测（离散交易），注入默认交易参数"""
    from src.backtest import BacktestEngine

    strategy_params.setdefault('long_threshold', 60)
    strategy_params.setdefault('short_threshold', 40)
    strategy_params.setdefault('use_position_sizing', False)
    strategy_params.setdefault('trend_filter', 'none')

    engine = BacktestEngine(strategy)
    result = engine.run_backtest(
        df=df,
        start_date=start_date,
        end_date=end_date,
        forecast_days=forecast_days,
        step_days=step_days,
        strategy_params=strategy_params
    )
    return engine, result


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
        forecast_days = bounded(data.get('forecast_days', 7), 7, 1, 90, int)
        step_days = bounded(data.get('step_days', 7), 7, 1, 90, int)
        strategy_params = data.get('strategy_params', {})
        model_path = data.get('model_path')
        if model_path:
            strategy_params['model_path'] = model_path
        engine_type = data.get('engine', 'standard')  # 'standard' or 'enhanced'

        # 增强引擎参数
        enhanced_params = data.get('enhanced_params', {})

        # 获取历史数据
        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if len(df) < 120:
            return jsonify({'error': f'Insufficient data. Need at least 120 days, got {len(df)}'}), 400

        # 创建策略（告知资产代码，LSTM按资产找模型）
        strategy_params['asset_code'] = asset_code
        strategy = create_strategy(strategy_name, strategy_params)

        # 运行回测
        if engine_type == 'enhanced':
            engine, result = run_enhanced_backtest(
                strategy, df, start_date, end_date, strategy_params,
                enhanced_params, forecast_days=forecast_days
            )

            # 获取交易记录
            trade_summary = engine.get_trade_summary()
            trades_data = trade_summary.to_dict('records') if not trade_summary.empty else []
        else:
            engine, result = run_standard_backtest(
                strategy, df, start_date, end_date,
                forecast_days, step_days, strategy_params
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
        return error_response(e)


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
        forecast_days = bounded(data.get('forecast_days', 7), 7, 1, 90, int)
        step_days = bounded(data.get('step_days', 7), 7, 1, 90, int)
        strategy_params = data.get('strategy_params', {})
        engine_type = data.get('engine', 'standard')
        enhanced_params = data.get('enhanced_params', {})

        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        # 创建策略（告知资产代码，LSTM按资产找模型）
        strategy_params['asset_code'] = asset_code
        strategy = create_strategy(strategy_name, strategy_params)

        from src.backtest.visualization import plot_backtest_result, plot_strategy_comparison
        import io
        import base64

        if engine_type == 'enhanced':
            _, result = run_enhanced_backtest(
                strategy, df, start_date, end_date, strategy_params,
                enhanced_params, forecast_days=forecast_days
            )
        else:
            _, result = run_standard_backtest(
                strategy, df, start_date, end_date,
                forecast_days, step_days, strategy_params
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
        return error_response(e)


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

        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        if len(df) < 120:
            return jsonify({'error': f'Insufficient data. Need at least 120 days, got {len(df)}'}), 400

        results = []

        for strategy_name in strategy_names:
            try:
                sp = dict(strategy_params.get(strategy_name, {}))
                sp.setdefault('asset_code', asset_code)
                strategy = create_strategy(strategy_name, sp)

                if engine_type == 'enhanced':
                    _, result = run_enhanced_backtest(
                        strategy, df, start_date, end_date, sp, enhanced_params
                    )
                else:
                    _, result = run_standard_backtest(
                        strategy, df, start_date, end_date, 7, 7, sp
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
        return error_response(e)


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

        df = _get_price_frame(asset_code, DEFAULT_CURRENCY, start_date, end_date)

        # 收集所有回测结果
        backtest_results = []

        for strategy_name in strategy_names:
            try:
                strategy = create_strategy(strategy_name, {'asset_code': asset_code})

                if engine_type == 'enhanced':
                    _, result = run_enhanced_backtest(
                        strategy, df, start_date, end_date,
                        {'asset_code': asset_code}, enhanced_params
                    )
                else:
                    _, result = run_standard_backtest(
                        strategy, df, start_date, end_date, 7, 7,
                        {'asset_code': asset_code}
                    )

                backtest_results.append(result)

            except Exception as e:
                app.logger.warning("策略 %s 回测失败: %s", strategy_name, e)

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
        return error_response(e)


# ============== 模型训练 API ==============

@app.route('/api/train/models')
def list_models():
    """获取已训练的模型列表"""
    return jsonify({'models': get_available_models()})


@app.route('/api/train/jobs')
def list_training_jobs():
    """获取训练任务列表"""
    with training_lock:
        jobs = []
        for job in training_jobs.values():
            job_summary = {
                'id': job['id'],
                'asset': job['asset'],
                'epochs': job['epochs'],
                'lr': job['lr'],
                'model_id': job.get('model_id'),
                'status': job['status'],
                'started_at': job['started_at'],
                'finished_at': job['finished_at'],
                'progress': job['progress'],
                'result': job.get('result'),
                'error': job.get('error')
            }
            jobs.append(job_summary)
    return jsonify({'jobs': jobs})


@app.route('/api/train/start', methods=['POST'])
def start_train():
    """启动模型训练任务"""
    data = request.get_json() or {}
    asset_code = data.get('asset', 'BTC').upper()
    model_id = data.get('model_id', 'default')
    epochs = bounded(data.get('epochs', 50), 50, 1, 1000, int)
    lr = bounded(data.get('lr', 0.001), 0.001, 1e-6, 1.0)
    hidden_size = bounded(data.get('hidden_size', 128), 128, 8, 1024, int)
    num_layers = bounded(data.get('num_layers', 2), 2, 1, 8, int)
    dropout = bounded(data.get('dropout', 0.2), 0.2, 0.0, 0.9)
    batch_size = bounded(data.get('batch_size', 32), 32, 1, 1024, int)
    seq_len = bounded(data.get('seq_len', 60), 60, 5, 500, int)
    forecast_horizon = bounded(data.get('forecast_horizon', 7), 7, 1, 90, int)
    patience = bounded(data.get('patience', 10), 10, 1, 100, int)
    resume = bool(data.get('resume', False))

    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400

    # 验证模型ID格式（只允许字母数字下划线）
    if not model_id or not all(c.isalnum() or c == '_' for c in model_id):
        return jsonify({'error': '模型ID只能包含字母、数字和下划线'}), 400

    # 重复任务检查在 start_training_job 的临界区内完成
    job_id = start_training_job(
        asset_code=asset_code,
        model_id=model_id,
        epochs=epochs,
        lr=lr,
        hidden_size=hidden_size,
        num_layers=num_layers,
        dropout=dropout,
        batch_size=batch_size,
        seq_len=seq_len,
        forecast_horizon=forecast_horizon,
        patience=patience,
        resume=resume
    )
    if job_id is None:
        return jsonify({'error': f'已有 {asset_code}/{model_id} 的训练任务正在运行'}), 409

    return jsonify({
        'job_id': job_id,
        'message': f'{asset_code}/{model_id} 模型训练已启动',
        'asset': asset_code,
        'model_id': model_id,
        'epochs': epochs
    })


@app.route('/api/train/history/<asset_code>/<model_id>')
def get_training_history(asset_code, model_id):
    """获取指定模型的训练历史"""
    # 校验路径参数，防止路径穿越
    asset_code = asset_code.upper()
    if asset_code not in SUPPORTED_ASSETS:
        return jsonify({'error': 'Unsupported asset'}), 400
    if not model_id or not all(c.isalnum() or c == '_' for c in model_id):
        return jsonify({'error': '模型ID只能包含字母、数字和下划线'}), 400

    history_path = f'models/{asset_code.lower()}_{model_id}_lstm_history.json'

    if not os.path.exists(history_path):
        return jsonify({'error': '训练历史不存在'}), 404

    try:
        with open(history_path, 'r') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/train/progress/<job_id>')
def train_progress(job_id):
    """SSE 流式返回训练进度"""
    def event_stream():
        job = None
        with training_lock:
            job = training_jobs.get(job_id)

        if not job:
            yield 'event: error\ndata: {"message": "任务不存在"}\n\n'
            return

        q = job['queue']

        while True:
            try:
                data = q.get(timeout=30)

                if data.get('done'):
                    with training_lock:
                        j = training_jobs.get(job_id, {})
                        result = {
                            'status': j.get('status'),
                            'result': j.get('result'),
                            'error': j.get('error')
                        }
                    yield f'event: complete\ndata: {json.dumps(result)}\n\n'
                    break

                if data.get('error'):
                    yield f'event: error\ndata: {json.dumps(data)}\n\n'
                    break

                # 任务状态由生产者(progress_callback)维护，这里只负责推送给客户端
                yield f'data: {json.dumps(data)}\n\n'

            except queue.Empty:
                # 发送心跳保持连接
                yield ':heartbeat\n\n'
                continue
            except Exception as e:
                yield f'event: error\ndata: {{"message": "{str(e)}"}}\n\n'
                break

    return stream_with_context(event_stream()), {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    }


@app.route('/api/train/jobs/<job_id>', methods=['DELETE'])
def delete_training_job(job_id):
    """删除训练任务记录（不会停止正在运行的线程，仅移除记录）"""
    with training_lock:
        if job_id in training_jobs:
            training_jobs.pop(job_id, None)
            return jsonify({'message': '任务记录已删除'})
    return jsonify({'error': '任务不存在'}), 404


# ============== 回测分析 API ==============

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
        return error_response(e)


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


# ============== 模拟持仓 API ==============

@app.route('/api/portfolios', methods=['GET'])
def list_portfolios():
    """获取所有模拟仓位列表"""
    portfolios = db.get_all_portfolios()
    # 获取每个仓位的最新价格计算收益
    result = []
    for p in portfolios:
        current_price = _current_price(p['asset_code'])
        stats = db.calculate_portfolio_stats(p['id'], current_price)
        stats['id'] = p['id']
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

    current_price = _current_price(portfolio['asset_code'])

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
        current_price = _current_price(p['asset_code'])
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


# ============== 模拟持仓页面 ==============

@app.route('/portfolio')
def portfolio_page():
    """模拟持仓页面（独立页面已下线，功能在主页的持仓标签页中）"""
    from flask import redirect
    return redirect('/#portfolio')


# ============== 自动更新数据 ==============

def auto_update_data():
    """服务启动时自动检查并更新数据到昨天"""
    print("\n" + "="*50)
    print("自动检查数据更新...")
    print("="*50)

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
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
    # 启动时自动检查并更新数据
    auto_update_data()

    # 默认只监听本机、关闭debug；需要对外暴露/调试时用环境变量开启
    host = os.environ.get('CRYPTO_HOST', '127.0.0.1')
    debug = os.environ.get('CRYPTO_DEBUG', '0') == '1'

    print("启动加密货币追踪工具 Web服务...")
    print(f"请访问: http://localhost:5001")
    app.run(host=host, port=5001, debug=debug)
