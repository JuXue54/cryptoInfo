#!/usr/bin/env python3
"""
获取加密货币20年历史数据
BTC从2010年开始，ETH从2015年开始

数据来自Yahoo Finance（USD计价），与Binance的USDT序列分开存储，
避免两种数据源在同一序列里混用导致口径不一致。
"""
import time
from datetime import datetime, timedelta, timezone

from src.database import Database
from src.yahoo_fetcher import YahooDataFetcher

# Yahoo Finance数据是USD计价，独立于Binance的USDT序列
YAHOO_CURRENCY = 'USD'


def fetch_full_history():
    """获取完整的20年历史数据"""
    db = Database()
    fetcher = YahooDataFetcher()

    # BTC: 从2010年7月开始（最早的交易数据）
    # ETH: 从2015年8月开始（以太坊上线时间）
    assets_config = {
        'BTC': {
            'start_date': '2010-07-01',
            'description': 'Bitcoin'
        },
        'ETH': {
            'start_date': '2015-08-01',
            'description': 'Ethereum'
        }
    }

    end_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')

    for asset_code, config in assets_config.items():
        print(f"\n{'='*60}")
        print(f"处理 {config['description']} ({asset_code}/{YAHOO_CURRENCY})")
        print(f"{'='*60}")

        # 检查数据库中已有数据
        summary = db.get_data_summary(asset_code, YAHOO_CURRENCY)
        if summary['count'] > 0:
            print(f"数据库中已有 {summary['count']} 条数据")
            print(f"数据范围: {summary['start_date']} ~ {summary['end_date']}")

            # 检查是否需要补充早期数据
            current_start = summary['start_date']
            if current_start > config['start_date']:
                print(f"\n需要补充早期数据 ({config['start_date']} ~ {current_start})")
                early_data = fetcher.fetch_historical_data(
                    asset_code, config['start_date'], current_start
                )
                if early_data:
                    count = db.save_price_data(asset_code, YAHOO_CURRENCY, early_data)
                    print(f"补充了 {count} 条早期数据")

            # 更新最新数据（从已有最新日期的下一天开始）
            latest_date = summary['end_date']
            print(f"\n更新最新数据（自 {latest_date} 之后）")
            new_data = fetcher.fetch_incremental_data(asset_code, latest_date)
            if new_data:
                count = db.save_price_data(asset_code, YAHOO_CURRENCY, new_data)
                print(f"更新了 {count} 条最新数据")
            else:
                print("数据已是最新")

        else:
            print(f"\n数据库为空，获取全部历史数据...")
            all_data = fetcher.fetch_historical_data(
                asset_code, config['start_date'], end_date
            )
            if all_data:
                count = db.save_price_data(asset_code, YAHOO_CURRENCY, all_data)
                print(f"保存了 {count} 条数据")

        # 休息一段时间避免API限制
        time.sleep(2)

    print(f"\n{'='*60}")
    print("数据获取完成!")
    print(f"{'='*60}")

    # 显示最终统计
    print("\n数据库状态:")
    for asset_code in assets_config.keys():
        summary = db.get_data_summary(asset_code, YAHOO_CURRENCY)
        print(f"  {asset_code}/{YAHOO_CURRENCY}: {summary['count']} 条数据")
        print(f"    范围: {summary['start_date']} ~ {summary['end_date']}")
        print(f"    价格: ${summary['min_price']:.2f} ~ ${summary['max_price']:.2f}")


if __name__ == '__main__':
    fetch_full_history()
