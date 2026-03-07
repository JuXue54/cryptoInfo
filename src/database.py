"""数据库操作模块"""
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import pandas as pd

from config import DB_PATH


class Database:
    """SQLite数据库操作类"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code TEXT NOT NULL,
                    currency_code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    close_price REAL NOT NULL,
                    max_price REAL NOT NULL,
                    min_price REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(asset_code, currency_code, date)
                )
            """)
            # 创建索引以加速查询
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_prices_asset_date
                ON prices(asset_code, currency_code, date)
            """)
            conn.commit()

    def save_price_data(self, asset_code: str, currency_code: str,
                        data: List[Dict]) -> int:
        """
        保存价格数据

        Args:
            asset_code: 加密货币代码，如BTC
            currency_code: 计价货币代码，如USDT
            data: 价格数据列表，每个元素包含date, open_price, close_price, max_price, min_price

        Returns:
            插入的记录数
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            inserted = 0
            for item in data:
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO prices
                        (asset_code, currency_code, date, open_price, close_price, max_price, min_price, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        asset_code.upper(),
                        currency_code.upper(),
                        item['date'],
                        item['open_price'],
                        item['close_price'],
                        item['max_price'],
                        item['min_price'],
                        datetime.now().isoformat()
                    ))
                    inserted += 1
                except sqlite3.Error as e:
                    print(f"插入数据失败: {e}, 数据: {item}")
            conn.commit()
            return inserted

    def get_latest_date(self, asset_code: str, currency_code: str) -> Optional[str]:
        """
        获取某币种最新的数据日期

        Returns:
            最新日期字符串(YYYY-MM-DD)，如果没有数据返回None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(date) FROM prices
                WHERE asset_code = ? AND currency_code = ?
            """, (asset_code.upper(), currency_code.upper()))
            result = cursor.fetchone()
            return result[0] if result[0] else None

    def get_earliest_date(self, asset_code: str, currency_code: str) -> Optional[str]:
        """
        获取某币种最早的数据日期

        Returns:
            最早日期字符串(YYYY-MM-DD)，如果没有数据返回None
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MIN(date) FROM prices
                WHERE asset_code = ? AND currency_code = ?
            """, (asset_code.upper(), currency_code.upper()))
            result = cursor.fetchone()
            return result[0] if result[0] else None

    def get_price_data(self, asset_code: str, currency_code: str,
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取价格数据

        Args:
            asset_code: 加密货币代码
            currency_code: 计价货币代码
            start_date: 开始日期(YYYY-MM-DD)，可选
            end_date: 结束日期(YYYY-MM-DD)，可选

        Returns:
            pandas DataFrame，包含价格数据
        """
        with sqlite3.connect(self.db_path) as conn:
            query = """
                SELECT date, open_price, close_price, max_price, min_price
                FROM prices
                WHERE asset_code = ? AND currency_code = ?
            """
            params = [asset_code.upper(), currency_code.upper()]

            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)

            query += " ORDER BY date ASC"

            df = pd.read_sql_query(query, conn, params=params)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            return df

    def get_all_assets(self) -> List[Tuple[str, str]]:
        """获取所有已存储的币种和计价货币组合"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT asset_code, currency_code FROM prices
            """)
            return cursor.fetchall()

    def get_data_summary(self, asset_code: str, currency_code: str) -> Dict:
        """获取数据摘要信息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*), MIN(date), MAX(date), MIN(min_price), MAX(max_price)
                FROM prices
                WHERE asset_code = ? AND currency_code = ?
            """, (asset_code.upper(), currency_code.upper()))
            result = cursor.fetchone()
            return {
                'count': result[0],
                'start_date': result[1],
                'end_date': result[2],
                'min_price': result[3],
                'max_price': result[4]
            }
