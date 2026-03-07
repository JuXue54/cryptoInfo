#!/usr/bin/env python3
"""
获取加密货币20年历史数据
BTC从2010年开始，ETH从2015年开始
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import time
import os

from src.database import Database
from config import SUPPORTED_ASSETS, DEFAULT_CURRENCY


def fetch_yahoo_data(ticker, start_date, end_date, asset_name):
    """从Yahoo Finance获取数据"""
    print(f"\n获取 {asset_name} ({ticker}) 从 {start_date} 到 {end_date} 的数据...")

    try:
        df = yf.download(
            ticker,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=False
        )

        if df.empty:
            print(f"  未获取到数据")
            return []

        # 处理多级列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 转换为标准格式
        result = []
        for date, row in df.iterrows():
            result.append({
                'date': date.strftime('%Y-%m-%d'),
                'open_price': float(row['Open']),
                'close_price': float(row['Close']),
                'max_price': float(row['High']),
                'min_price': float(row['Low']),
            })

        print(f"  成功获取 {len(result)} 条数据")
        return result

    except Exception as e:
        print(f"  获取失败: {e}")
        return []


def fetch_full_history():
    """获取完整的20年历史数据"""
    db = Database()

    # BTC: 从2010年7月开始（最早的交易数据）
    # ETH: 从2015年8月开始（以太坊上线时间）
    assets_config = {
        'BTC': {
            'ticker': 'BTC-USD',
            'start_date': '2010-07-01',
            'description': 'Bitcoin'
        },
        'ETH': {
            'ticker': 'ETH-USD',
            'start_date': '2015-08-01',
            'description': 'Ethereum'
        }
    }

    end_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    for asset_code, config in assets_config.items():
        print(f"\n{'='*60}")
        print(f"处理 {config['description']} ({asset_code})")
        print(f"{'='*60}")

        # 检查数据库中已有数据
        summary = db.get_data_summary(asset_code, DEFAULT_CURRENCY)
        if summary['count'] > 0:
            print(f"数据库中已有 {summary['count']} 条数据")
            print(f"数据范围: {summary['start_date']} ~ {summary['end_date']}")

            # 检查是否需要补充早期数据
            current_start = summary['start_date']
            if current_start > config['start_date']:
                print(f"\n需要补充早期数据 ({config['start_date']} ~ {current_start})")
                early_data = fetch_yahoo_data(
                    config['ticker'],
                    config['start_date'],
                    current_start,
                    asset_code
                )
                if early_data:
                    count = db.save_price_data(asset_code, DEFAULT_CURRENCY, early_data)
                    print(f"补充了 {count} 条早期数据")

            # 更新最新数据
            latest_date = summary['end_date']
            print(f"\n更新最新数据 ({latest_date} ~ {end_date})")
            new_data = fetch_yahoo_data(
                config['ticker'],
                latest_date,
                end_date,
                asset_code
            )
            if new_data:
                count = db.save_price_data(asset_code, DEFAULT_CURRENCY, new_data)
                print(f"更新了 {count} 条最新数据")

        else:
            print(f"\n数据库为空，获取全部历史数据...")
            all_data = fetch_yahoo_data(
                config['ticker'],
                config['start_date'],
                end_date,
                asset_code
            )
            if all_data:
                count = db.save_price_data(asset_code, DEFAULT_CURRENCY, all_data)
                print(f"保存了 {count} 条数据")

        # 休息一段时间避免API限制
        time.sleep(2)

    print(f"\n{'='*60}")
    print("数据获取完成!")
    print(f"{'='*60}")

    # 显示最终统计
    print("\n数据库状态:")
    for asset_code in assets_config.keys():
        summary = db.get_data_summary(asset_code, DEFAULT_CURRENCY)
        print(f"  {asset_code}: {summary['count']} 条数据")
        print(f"    范围: {summary['start_date']} ~ {summary['end_date']}")
        print(f"    价格: ${summary['min_price']:.2f} ~ ${summary['max_price']:.2f}")


if __name__ == '__main__':
    fetch_full_history()
