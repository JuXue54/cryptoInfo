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
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            # WAL模式：读写不互相阻塞，缓解并发访问时的 database is locked
            cursor.execute("PRAGMA journal_mode=WAL")
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
                    volume REAL NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(asset_code, currency_code, date)
                )
            """)
            # UNIQUE 约束已自带 (asset_code, currency_code, date) 的隐式索引，
            # 显式索引完全重复，只会放大写入开销，删除之
            cursor.execute("DROP INDEX IF EXISTS idx_prices_asset_date")

            # ======= 模拟持仓表 =======
            # 模拟仓位主表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    asset_code TEXT NOT NULL DEFAULT 'BTC',
                    mode TEXT NOT NULL DEFAULT 'manual',
                    strategy TEXT,
                    initial_capital REAL NOT NULL DEFAULT 10000,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 交易记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id INTEGER NOT NULL,
                    trade_date TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    amount REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_portfolio_trades_pid
                ON portfolio_trades(portfolio_id, trade_date)
            """)
            conn.commit()

    def get_latest_price(self, asset_code: str,
                         currency_code: str = 'USDT') -> Optional[float]:
        """获取某币种最新收盘价（避免为读一个价格而全量加载历史数据）"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close_price FROM prices
                WHERE asset_code = ? AND currency_code = ?
                ORDER BY date DESC LIMIT 1
            """, (asset_code.upper(), currency_code.upper()))
            row = cursor.fetchone()
            return row[0] if row else None

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
        now = datetime.now().isoformat()
        rows = [(
            asset_code.upper(),
            currency_code.upper(),
            item['date'],
            item['open_price'],
            item['close_price'],
            item['max_price'],
            item['min_price'],
            item.get('volume', 0),
            now,
        ) for item in data]

        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            # ON CONFLICT DO UPDATE 只更新冲突行，不像 INSERT OR REPLACE 那样
            # 删除再插入（保留 created_at，也少维护一次索引）
            cursor.executemany("""
                INSERT INTO prices
                    (asset_code, currency_code, date, open_price, close_price,
                     max_price, min_price, volume, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_code, currency_code, date) DO UPDATE SET
                    open_price = excluded.open_price,
                    close_price = excluded.close_price,
                    max_price = excluded.max_price,
                    min_price = excluded.min_price,
                    volume = excluded.volume,
                    updated_at = excluded.updated_at
            """, rows)
            conn.commit()
            return len(rows)

    def get_latest_date(self, asset_code: str, currency_code: str) -> Optional[str]:
        """
        获取某币种最新的数据日期

        Returns:
            最新日期字符串(YYYY-MM-DD)，如果没有数据返回None
        """
        with sqlite3.connect(self.db_path, timeout=30) as conn:
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
        with sqlite3.connect(self.db_path, timeout=30) as conn:
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
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            query = """
                SELECT date, open_price, close_price, max_price, min_price, volume
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
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT asset_code, currency_code FROM prices
            """)
            return cursor.fetchall()

    def get_data_summary(self, asset_code: str, currency_code: str) -> Dict:
        """获取数据摘要信息"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
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

    # ============== 模拟持仓操作 ==============

    def create_portfolio(self, name: str, asset_code: str, mode: str,
                         initial_capital: float, strategy: str = None,
                         description: str = None) -> int:
        """创建模拟仓位，返回仓位ID"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO portfolios (name, asset_code, mode, strategy, initial_capital, description)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, asset_code.upper(), mode, strategy, initial_capital, description))
            conn.commit()
            return cursor.lastrowid

    def get_all_portfolios(self) -> List[Dict]:
        """获取所有模拟仓位"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM portfolios ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict]:
        """获取单个仓位信息"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_portfolio(self, portfolio_id: int):
        """删除仓位及所有交易记录"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio_trades WHERE portfolio_id = ?", (portfolio_id,))
            cursor.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
            conn.commit()

    def add_trade(self, portfolio_id: int, trade_date: str, trade_type: str,
                  price: float, quantity: float, fee: float = 0,
                  note: str = None) -> int:
        """添加交易记录（buy/sell），返回记录ID"""
        amount = price * quantity
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO portfolio_trades (portfolio_id, trade_date, trade_type, price, quantity, amount, fee, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (portfolio_id, trade_date, trade_type, price, quantity, amount, fee, note))
            # 更新持仓更新时间
            cursor.execute("UPDATE portfolios SET updated_at = ? WHERE id = ?",
                           (datetime.now().isoformat(), portfolio_id))
            conn.commit()
            return cursor.lastrowid

    def get_trades(self, portfolio_id: int) -> List[Dict]:
        """获取仓位的所有交易记录"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM portfolio_trades
                WHERE portfolio_id = ?
                ORDER BY trade_date ASC, id ASC
            """, (portfolio_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_trade(self, trade_id: int):
        """删除单条交易记录"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio_trades WHERE id = ?", (trade_id,))
            conn.commit()

    def calculate_portfolio_stats(self, portfolio_id: int, current_price: float) -> Dict:
        """
        计算仓位统计信息：持仓量、平均成本、浮盈亏、已实现盈亏、总收益率
        使用先进先出（FIFO）计算已实现盈亏
        """
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return {}

        trades = self.get_trades(portfolio_id)
        initial_capital = portfolio['initial_capital']

        # FIFO队列计算
        buy_queue = []  # [(price, qty), ...]
        realized_pnl = 0.0
        total_bought_amount = 0.0
        total_sold_amount = 0.0
        total_bought_qty = 0.0
        total_sold_qty = 0.0
        total_fees = 0.0

        for t in trades:
            qty = float(t['quantity'])
            price = float(t['price'])
            fee = float(t['fee'])
            total_fees += fee

            if t['trade_type'] == 'buy':
                buy_queue.append({'price': price, 'qty': qty})
                total_bought_amount += price * qty
                total_bought_qty += qty
            elif t['trade_type'] == 'sell':
                remaining_sell = qty
                total_sold_amount += price * qty
                total_sold_qty += qty
                while remaining_sell > 0 and buy_queue:
                    head = buy_queue[0]
                    if head['qty'] <= remaining_sell:
                        realized_pnl += (price - head['price']) * head['qty']
                        remaining_sell -= head['qty']
                        buy_queue.pop(0)
                    else:
                        realized_pnl += (price - head['price']) * remaining_sell
                        head['qty'] -= remaining_sell
                        remaining_sell = 0

        # 当前持仓
        current_qty = sum(b['qty'] for b in buy_queue)
        if current_qty > 0:
            avg_cost = sum(b['price'] * b['qty'] for b in buy_queue) / current_qty
        else:
            avg_cost = 0.0

        unrealized_pnl = (current_price - avg_cost) * current_qty if current_qty > 0 else 0.0
        total_pnl = realized_pnl + unrealized_pnl - total_fees
        total_return_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
        market_value = current_price * current_qty

        return {
            'portfolio_id': portfolio_id,
            'name': portfolio['name'],
            'asset_code': portfolio['asset_code'],
            'mode': portfolio['mode'],
            'strategy': portfolio['strategy'],
            'initial_capital': initial_capital,
            'current_quantity': round(current_qty, 8),
            'avg_cost': round(avg_cost, 2),
            'market_value': round(market_value, 2),
            'current_price': current_price,
            'unrealized_pnl': round(unrealized_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'total_fees': round(total_fees, 2),
            'total_pnl': round(total_pnl, 2),
            'total_return': round(total_return_pct, 4),
            'total_bought_qty': round(total_bought_qty, 8),
            'total_sold_qty': round(total_sold_qty, 8),
            'total_trades': len(trades),
        }
