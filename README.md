# 加密货币价格追踪工具

一个使用Python开发的加密货币价格追踪和分析工具，支持从Yahoo Finance获取BTC、ETH等主流加密货币的历史价格数据，并提供交互式K线图功能。

## 功能特性

- **数据获取**: 从Yahoo Finance自动获取BTC、ETH的历史价格数据（支持10年以上历史数据）
- **CSV导入**: 支持从CSV文件导入历史数据，便于使用第三方数据源
- **数据存储**: 使用SQLite数据库存储，支持自动增量更新
- **交互式Web界面**: 前后端分离架构，实时动态K线图
  - 支持日K、周K、月K三种时间周期
  - 支持自定义均线周期（如MA5、MA10、MA20、MA60等）
  - 支持普通坐标轴和对数坐标轴实时切换
  - 鼠标悬停显示详细信息
- **命令行工具**: 支持CLI操作和数据导入

## 项目结构

```
cryptoInfo/
├── app.py               # Web应用入口 (Flask后端 + 前端)
├── main.py              # 命令行入口
├── gui.py               # 桌面图形界面入口
├── fetch_historical.py  # 获取历史数据脚本
├── config.py            # 配置文件
├── requirements.txt     # Python依赖
├── crypto_data.db       # SQLite数据库（自动生成）
└── src/
    ├── __init__.py
    ├── data_fetcher.py  # 数据获取模块（Yahoo Finance）
    ├── database.py      # 数据库操作模块
    ├── aggregator.py    # 数据聚合模块（日/周/月K转换）
    ├── chart.py         # 图表绘制模块
    └── csv_importer.py  # CSV数据导入模块
```

## 安装

1. 克隆或下载项目
2. 安装依赖:

```bash
pip install -r requirements.txt
```

## 使用方法

### 方式一：Web交互界面（强烈推荐）

启动Web服务:

```bash
python app.py
```

然后在浏览器中访问: **http://localhost:5001**

Web界面功能：
- **实时切换币种**: BTC/ETH
- **实时切换时间周期**: 日K/周K/月K
- **实时切换坐标轴**: 普通坐标/对数坐标
- **动态均线配置**: 勾选/取消均线，实时显示/隐藏
- **自定义均线**: 输入任意周期，如30,90,180
- **实时数据更新**: 点击按钮从Yahoo Finance获取最新数据
- **详细信息面板**: 显示当前价格、最高/最低价、涨跌幅、数据范围等
- **操作日志**: 实时显示系统操作记录

### 方式二：桌面GUI界面

```bash
python gui.py
```

### 方式三：命令行

#### 获取历史数据

```bash
# 获取BTC和ETH的完整历史数据（约20年）
python fetch_historical.py
```

#### 更新数据

```bash
# 更新所有币种数据
python main.py update

# 只更新BTC数据
python main.py update BTC
```

#### 查看数据概览

```bash
python main.py info
```

#### 从CSV导入数据

```bash
# 从CSV文件导入BTC历史数据
python main.py import data/btc_history.csv BTC

# 从CSV文件导入ETH历史数据
python main.py import data/eth_history.csv ETH
```

CSV文件格式要求：
- 必须包含以下列：date, open, high, low, close
- 日期格式：YYYY-MM-DD
- 列名不区分大小写

#### 绘制静态K线图

```bash
# 绘制BTC日K图
python main.py chart BTC

# 使用对数坐标轴
python main.py chart BTC --log

# 显示指定均线（如20日和60日均线）
python main.py chart BTC --ma 20 60

# 保存图表到指定文件
python main.py chart BTC --save my_btc_chart.png
```

## API接口

Web应用提供以下REST API：

- `GET /` - 返回前端页面
- `GET /api/assets` - 获取支持的币种列表
- `GET /api/data/<asset_code>` - 获取指定币种的历史数据
  - 参数: `timeframe` (day/week/month), `start`, `end`
- `GET /api/data/<asset_code>/with_ma` - 获取带均线的数据
  - 参数: `timeframe`, `ma_periods` (如 "20,60,120")
- `POST /api/update/<asset_code>` - 更新指定币种数据
- `POST /api/update_all` - 更新所有币种数据

## 数据库表结构

**prices表**:
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| asset_code | TEXT | 加密货币代码（如BTC） |
| currency_code | TEXT | 计价货币（如USD） |
| date | TEXT | 日期（YYYY-MM-DD） |
| open_price | REAL | 开盘价 |
| close_price | REAL | 收盘价 |
| max_price | REAL | 最高价 |
| min_price | REAL | 最低价 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

## 技术栈

**后端**:
- Python Flask: Web框架
- SQLite: 数据存储
- yfinance: 从Yahoo Finance获取数据
- pandas: 数据处理

**前端**:
- HTML5 + CSS3 + JavaScript
- [Lightweight Charts](https://tradingview.github.io/lightweight-charts/): 专业金融图表库

## 注意事项

1. **数据源**: 使用Yahoo Finance API获取数据，免费且支持长期历史数据
2. **API限制**: Yahoo Finance可能有请求频率限制，如遇限制请稍后再试
3. **数据补充**: 如果API受限，可以使用CSV导入功能从其他来源获取数据
4. **Web服务**: 默认运行在5001端口（5000端口在macOS上可能被AirPlay占用）

## 依赖库

- flask: Web框架
- flask-cors: 跨域支持
- pandas: 数据处理
- yfinance: 从Yahoo Finance获取数据
- mplfinance: 静态K线图绘制
- matplotlib: 图表绘制
- numpy: 数值计算

## License

MIT License
