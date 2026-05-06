"""配置文件"""
import os

# 数据库配置 - 支持通过环境变量自定义路径
# 解决WSL2中Windows程序(PyCharm)访问Linux文件系统的锁冲突问题
# 使用方法: export CRYPTO_DB_PATH="/mnt/c/Users/你的用户名/data/sqlite/crypto_data.db"
default_db_path = os.path.expanduser("~/data/sqlite/crypto_data.db")
DB_PATH = os.environ.get("CRYPTO_DB_PATH", default_db_path)

# 确保数据库目录存在
data_dir = os.path.dirname(DB_PATH)
if data_dir:
    os.makedirs(data_dir, exist_ok=True)

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
