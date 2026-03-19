#!/usr/bin/env python3
"""
加密货币价格追踪工具

功能:
1. 从CoinGecko获取BTC、ETH历史价格数据
2. 将数据存储在SQLite数据库中
3. 支持自动增量更新
4. 绘制K线图（日K、周K、月K），支持均线和对数坐标轴
5. BTC价格走势预测（蒙特卡洛模拟）

使用方法:
    python main.py update              # 更新数据
    python main.py chart BTC           # 绘制BTC日K图
    python main.py chart BTC --week    # 绘制BTC周K图
    python main.py chart BTC --month   # 绘制BTC月K图
    python main.py chart BTC --log     # 使用对数坐标轴
    python main.py chart BTC --ma 20 60 # 显示20日和60日均线
    python main.py info                # 查看数据概览
    python main.py predict             # BTC走势预测
    python main.py predict --days 14   # 预测未来14天
"""
import argparse
import sys
from datetime import datetime

from config import SUPPORTED_ASSETS, DEFAULT_CURRENCY, MA_PERIODS
from src.database import Database
from src.data_fetcher import DataFetcher
from src.chart import ChartPlotter
from src.csv_importer import CSVImporter


def update_data(asset_code: str = None, currency_code: str = DEFAULT_CURRENCY):
    """更新数据"""
    db = Database()
    fetcher = DataFetcher()

    assets = [asset_code.upper()] if asset_code else list(SUPPORTED_ASSETS.keys())

    for asset in assets:
        if asset not in SUPPORTED_ASSETS:
            print(f"不支持的加密货币: {asset}")
            continue

        print(f"\n正在更新 {asset} 数据...")

        # 获取数据库中最新的日期
        latest_date = db.get_latest_date(asset, currency_code)

        try:
            # 获取增量数据
            new_data = fetcher.fetch_incremental_data(asset, currency_code, latest_date)

            if new_data:
                # 保存到数据库
                count = db.save_price_data(asset, currency_code, new_data)
                print(f"成功保存 {count} 条 {asset} 数据")
            else:
                print(f"{asset} 数据已是最新")

        except Exception as e:
            print(f"更新 {asset} 数据失败: {e}")


def import_csv_data(file_path: str, asset_code: str, date_format: str = None):
    """从CSV文件导入数据"""
    db = Database()

    try:
        print(f"正在从 {file_path} 导入 {asset_code} 数据...")
        data = CSVImporter.import_from_csv(file_path, asset_code, date_format)

        if data:
            count = db.save_price_data(asset_code, DEFAULT_CURRENCY, data)
            print(f"成功导入 {count} 条 {asset_code} 数据")
        else:
            print("没有数据被导入")

    except Exception as e:
        print(f"导入失败: {e}")
        print("\nCSV格式帮助:")
        print(CSVImporter.get_sample_csv_format())


def show_info():
    """显示数据概览"""
    db = Database()

    print("\n=== 加密货币数据概览 ===\n")

    assets = db.get_all_assets()

    if not assets:
        print("数据库中没有数据，请先运行更新命令:")
        print("  python main.py update")
        return

    for asset_code, currency_code in assets:
        summary = db.get_data_summary(asset_code, currency_code)
        print(f"币种: {asset_code}/{currency_code}")
        print(f"  数据条数: {summary['count']}")
        print(f"  数据范围: {summary['start_date']} ~ {summary['end_date']}")
        print(f"  价格范围: ${summary['min_price']:.2f} ~ ${summary['max_price']:.2f}")
        print()


def plot_chart(asset_code: str,
               currency_code: str = DEFAULT_CURRENCY,
               timeframe: str = 'day',
               log_scale: bool = False,
               ma_periods: list = None,
               start_date: str = None,
               end_date: str = None,
               save_path: str = None):
    """
    绘制K线图

    Args:
        asset_code: 加密货币代码，如BTC
        currency_code: 计价货币代码
        timeframe: 时间周期，'day'日线, 'week'周线, 'month'月线
        log_scale: 是否使用对数坐标轴
        ma_periods: 均线周期列表
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        save_path: 保存图片路径，None则显示图表
    """
    if asset_code.upper() not in SUPPORTED_ASSETS:
        print(f"不支持的加密货币: {asset_code}")
        print(f"支持的币种: {', '.join(SUPPORTED_ASSETS.keys())}")
        return

    db = Database()

    # 检查数据库中是否有数据
    summary = db.get_data_summary(asset_code.upper(), currency_code)
    if summary['count'] == 0:
        print(f"数据库中没有 {asset_code} 数据，请先运行更新命令:")
        print(f"  python main.py update {asset_code}")
        return

    # 获取数据
    df = db.get_price_data(asset_code.upper(), currency_code, start_date, end_date)

    if df.empty:
        print("没有数据可以绘制")
        return

    # 获取币种名称
    asset_name = SUPPORTED_ASSETS[asset_code.upper()]["name"]

    # 绘制图表
    plotter = ChartPlotter()
    title = f"{asset_name} ({asset_code.upper()})"

    # 如果没有指定保存路径，默认保存到当前目录
    if save_path is None:
        timeframe_names = {'day': 'daily', 'week': 'weekly', 'month': 'monthly'}
        tf_suffix = timeframe_names.get(timeframe, 'chart')
        log_suffix = '_log' if log_scale else ''
        save_path = f"{asset_code.lower()}_{tf_suffix}{log_suffix}.png"

    plotter.plot(
        df,
        title=title,
        timeframe=timeframe,
        ma_periods=ma_periods,
        log_scale=log_scale,
        save_path=save_path,
        show=False
    )
    print(f"图表已保存到: {save_path}")


def predict_btc(days: int = 7, simulations: int = 10000, save_path: str = None, no_chart: bool = False):
    """
    BTC价格预测

    Args:
        days: 预测天数
        simulations: 蒙特卡洛模拟次数
        save_path: 图表保存路径
        no_chart: 是否不生成图表
    """
    from src.btc_predictor import BTCPredictor, print_prediction_report
    from src.prediction_chart import plot_prediction

    print(f"\n正在进行BTC价格预测（未来{days}天）...")
    print(f"使用 {simulations} 次蒙特卡洛模拟\n")

    try:
        predictor = BTCPredictor(forecast_days=days, n_simulations=simulations)
        result = predictor.predict(use_trend=True)

        # 打印报告
        print_prediction_report(result)

        # 绘制图表
        if not no_chart:
            if save_path is None:
                save_path = f"btc_prediction_{days}d.png"
            plot_prediction(result, save_path=save_path, show=False)

    except Exception as e:
        print(f"预测失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description="加密货币价格追踪工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 数据更新
  python main.py update                 # 更新所有币种数据
  python main.py update BTC             # 只更新BTC数据

  # CSV导入（当API受限时使用）
  python main.py import data/btc.csv BTC   # 从CSV导入BTC数据

  # 绘制图表
  python main.py chart BTC              # 绘制BTC日K图
  python main.py chart BTC --week       # 绘制BTC周K图
  python main.py chart BTC --month      # 绘制BTC月K图
  python main.py chart BTC --log        # 使用对数坐标轴
  python main.py chart BTC --ma 20 60   # 显示20日和60日均线
  python main.py chart BTC --save btc.png  # 保存到指定文件
  python main.py chart BTC --start 2025-01-01  # 指定开始日期

  # 查看信息
  python main.py info                   # 查看数据概览

  # BTC走势预测
  python main.py predict                # 预测未来7天
  python main.py predict --days 14      # 预测未来14天
  python main.py predict --days 7 --simulations 5000  # 使用5000次模拟
  python main.py predict --save pred.png --no-chart   # 保存图表
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # update 命令
    update_parser = subparsers.add_parser('update', help='更新价格数据')
    update_parser.add_argument('asset', nargs='?', help='币种代码 (BTC/ETH)，不指定则更新所有')

    # chart 命令
    chart_parser = subparsers.add_parser('chart', help='绘制K线图')
    chart_parser.add_argument('asset', help='币种代码 (BTC/ETH)')
    chart_parser.add_argument('--day', action='store_true', help='绘制日K图 (默认)')
    chart_parser.add_argument('--week', action='store_true', help='绘制周K图')
    chart_parser.add_argument('--month', action='store_true', help='绘制月K图')
    chart_parser.add_argument('--log', action='store_true', help='使用对数坐标轴')
    chart_parser.add_argument('--ma', nargs='+', type=int, metavar='N',
                             help='显示N日均线，可指定多个，如 --ma 20 60')
    chart_parser.add_argument('--start', help='开始日期 (YYYY-MM-DD)')
    chart_parser.add_argument('--end', help='结束日期 (YYYY-MM-DD)')
    chart_parser.add_argument('--save', dest='save_path', help='保存图表到指定文件路径')

    # info 命令
    subparsers.add_parser('info', help='查看数据概览')

    # import 命令
    import_parser = subparsers.add_parser('import', help='从CSV文件导入数据')
    import_parser.add_argument('file', help='CSV文件路径')
    import_parser.add_argument('asset', help='币种代码 (BTC/ETH)')
    import_parser.add_argument('--date-format', help='日期格式，如 %%Y-%%m-%%d')

    # predict 命令
    predict_parser = subparsers.add_parser('predict', help='BTC价格走势预测')
    predict_parser.add_argument('--days', type=int, default=7,
                               help='预测天数，默认7天')
    predict_parser.add_argument('--simulations', type=int, default=10000,
                               help='蒙特卡洛模拟次数，默认10000次')
    predict_parser.add_argument('--save', dest='save_path',
                               help='保存预测图表路径')
    predict_parser.add_argument('--no-chart', action='store_true',
                               help='不生成图表')

    args = parser.parse_args()

    if args.command == 'update':
        update_data(args.asset)

    elif args.command == 'chart':
        # 确定时间周期
        timeframe = 'day'
        if args.week:
            timeframe = 'week'
        elif args.month:
            timeframe = 'month'

        # 确定均线周期
        ma_periods = args.ma if args.ma else None

        plot_chart(
            asset_code=args.asset,
            timeframe=timeframe,
            log_scale=args.log,
            ma_periods=ma_periods,
            start_date=args.start,
            end_date=args.end,
            save_path=args.save_path
        )

    elif args.command == 'info':
        show_info()

    elif args.command == 'import':
        import_csv_data(args.file, args.asset, args.date_format)

    elif args.command == 'predict':
        predict_btc(
            days=args.days,
            simulations=args.simulations,
            save_path=args.save_path,
            no_chart=args.no_chart
        )

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
