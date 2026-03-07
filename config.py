"""配置文件"""
import os

# 数据库配置
DB_PATH = os.path.join(os.path.dirname(__file__), "crypto_data.db")

# 支持的加密货币
SUPPORTED_ASSETS = {
    "BTC": {"name": "Bitcoin", "coingecko_id": "bitcoin"},
    "ETH": {"name": "Ethereum", "coingecko_id": "ethereum"},
}

# 支持的计价货币
SUPPORTED_CURRENCIES = ["USDT"]

# 默认使用USDT
DEFAULT_CURRENCY = "USDT"

# CoinGecko API配置
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"

# 均线周期配置
MA_PERIODS = {
    "MA5": 5,
    "MA10": 10,
    "MA20": 20,
    "MA60": 60,
    "MA120": 120,
    "MA240": 240,
}

# 图表样式
CHART_STYLE = "charles"
CHART_FIGSIZE = (14, 8)
