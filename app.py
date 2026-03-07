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

# ============== 前端HTML模板 ==============

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>加密货币价格追踪工具</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f5f5f5;
            min-height: 100vh;
        }

        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }

        .header h1 {
            font-size: 28px;
            margin-bottom: 10px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        .control-panel {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        .control-row {
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            align-items: center;
            margin-bottom: 15px;
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
            padding: 5px 10px;
            background: #f0f0f0;
            border-radius: 4px;
            transition: all 0.3s;
        }

        .ma-checkbox:hover {
            background: #e0e0e0;
        }

        .ma-checkbox input {
            cursor: pointer;
        }

        .ma-checkbox.checked {
            background: #667eea;
            color: white;
        }

        .chart-container {
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            position: relative;
        }

        #chart {
            width: 100%;
            height: 600px;
        }

        .info-panel {
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-top: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }

        .info-item {
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
        }

        .info-item h4 {
            color: #666;
            font-size: 12px;
            margin-bottom: 5px;
            text-transform: uppercase;
        }

        .info-item p {
            color: #333;
            font-size: 18px;
            font-weight: 600;
        }

        .loading {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 18px;
            color: #667eea;
            display: none;
        }

        .loading.show {
            display: block;
        }

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

        .btn-group {
            display: flex;
            gap: 5px;
        }

        .btn-group button {
            padding: 8px 16px;
        }

        .tooltip {
            position: absolute;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            pointer-events: none;
            z-index: 1000;
            display: none;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>加密货币价格追踪工具</h1>
        <p>支持日K/周K/月K，多均线，对数坐标</p>
    </div>

    <div class="container">
        <!-- 控制面板 -->
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
                    <button onclick="addCustomMA()" style="margin-left: 5px; background: #17a2b8;">添加</button>
                </div>

                <div class="control-group">
                    <button onclick="refreshData()" id="refreshBtn">刷新数据</button>
                    <button onclick="updateData()" id="updateBtn" style="background: #28a745;">更新数据源</button>
                </div>
            </div>
        </div>

        <!-- 图表区域 -->
        <div class="chart-container">
            <div id="chart"></div>
            <div class="loading" id="loading">加载中...</div>
        </div>

        <!-- 信息面板 -->
        <div class="info-panel">
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

        <!-- 日志区域 -->
        <div class="log-container" id="logContainer">
            <div class="log-entry">系统就绪...</div>
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

        // ============== 初始化 ==============
        document.addEventListener('DOMContentLoaded', function() {
            initChart();
            loadData();
            setupEventListeners();
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

        // ============== 工具函数 ==============
        function showLoading(show) {
            document.getElementById('loading').classList.toggle('show', show);
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
