#!/usr/bin/env python3
"""
加密货币价格追踪工具 - 图形界面版

使用方式:
    python gui.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import os

from config import SUPPORTED_ASSETS, DEFAULT_CURRENCY, MA_PERIODS
from src.database import Database
from src.data_fetcher import DataFetcher
from src.chart import ChartPlotter


class CryptoTrackerGUI:
    """加密货币追踪工具图形界面"""

    def __init__(self, root):
        self.root = root
        self.root.title("加密货币价格追踪工具")
        self.root.geometry("750x650")
        self.root.minsize(700, 550)

        # 设置白色背景
        self.root.configure(bg='white')

        # macOS 主题修复
        self.style = ttk.Style()
        self.style.theme_use('clam')  # 使用clam主题，在所有平台上表现一致

        # 初始化组件
        self.db = Database()
        self.fetcher = DataFetcher()
        self.plotter = ChartPlotter()

        # 创建界面
        self._create_widgets()
        self._update_data_info()

        print("GUI初始化完成")

    def _create_widgets(self):
        """创建界面组件"""
        # 创建Canvas和滚动条（以防窗口太小）
        canvas = tk.Canvas(self.root, bg='white', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)

        # 主框架
        main_frame = tk.Frame(canvas, bg='white', padx=20, pady=20)

        # 配置Canvas
        canvas.configure(yscrollcommand=scrollbar.set)

        # 布局
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas.create_window((0, 0), window=main_frame, anchor="nw", width=730)

        # 更新Canvas滚动区域
        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        main_frame.bind("<Configure>", on_frame_configure)

        # 绑定鼠标滚轮
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel)

        # ===== 标题 =====
        title_label = tk.Label(
            main_frame,
            text="加密货币价格追踪工具",
            font=('Arial', 18, 'bold'),
            bg='white',
            fg='#333333'
        )
        title_label.pack(pady=(0, 20))

        # ===== 数据管理区域 =====
        data_frame = tk.LabelFrame(
            main_frame,
            text=" 数据管理 ",
            font=('Arial', 12, 'bold'),
            bg='white',
            fg='#333333',
            padx=15,
            pady=15
        )
        data_frame.pack(fill=tk.X, pady=10)

        # 数据信息标签
        self.data_info_label = tk.Label(
            data_frame,
            text="正在加载数据信息...",
            font=('Arial', 10),
            bg='white',
            fg='#555555',
            wraplength=650,
            justify=tk.LEFT
        )
        self.data_info_label.pack(anchor=tk.W, pady=5)

        # 按钮区域
        btn_frame = tk.Frame(data_frame, bg='white')
        btn_frame.pack(fill=tk.X, pady=10)

        tk.Button(
            btn_frame,
            text="更新BTC数据",
            command=lambda: self._update_data("BTC"),
            font=('Arial', 10),
            bg='#4CAF50',
            fg='white',
            activebackground='#45a049',
            padx=15,
            pady=5
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame,
            text="更新ETH数据",
            command=lambda: self._update_data("ETH"),
            font=('Arial', 10),
            bg='#4CAF50',
            fg='white',
            activebackground='#45a049',
            padx=15,
            pady=5
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame,
            text="更新全部",
            command=lambda: self._update_data(None),
            font=('Arial', 10),
            bg='#2196F3',
            fg='white',
            activebackground='#0b7dda',
            padx=15,
            pady=5
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame,
            text="刷新信息",
            command=self._update_data_info,
            font=('Arial', 10),
            bg='#FF9800',
            fg='white',
            activebackground='#e68900',
            padx=15,
            pady=5
        ).pack(side=tk.LEFT, padx=5)

        # ===== 图表配置区域 =====
        chart_frame = tk.LabelFrame(
            main_frame,
            text=" 图表配置 ",
            font=('Arial', 12, 'bold'),
            bg='white',
            fg='#333333',
            padx=15,
            pady=15
        )
        chart_frame.pack(fill=tk.X, pady=10)

        # 币种选择
        row1 = tk.Frame(chart_frame, bg='white')
        row1.pack(fill=tk.X, pady=8)

        tk.Label(row1, text="选择币种:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.asset_var = tk.StringVar(value="BTC")
        asset_combo = ttk.Combobox(row1, textvariable=self.asset_var, values=["BTC", "ETH"], state="readonly", width=15)
        asset_combo.pack(side=tk.LEFT, padx=5)

        # 时间周期选择
        row2 = tk.Frame(chart_frame, bg='white')
        row2.pack(fill=tk.X, pady=8)

        tk.Label(row2, text="时间周期:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.timeframe_var = tk.StringVar(value="day")

        timeframe_frame = tk.Frame(row2, bg='white')
        timeframe_frame.pack(side=tk.LEFT)

        tk.Radiobutton(timeframe_frame, text="日K", variable=self.timeframe_var, value="day",
                       bg='white', font=('Arial', 10)).pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(timeframe_frame, text="周K", variable=self.timeframe_var, value="week",
                       bg='white', font=('Arial', 10)).pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(timeframe_frame, text="月K", variable=self.timeframe_var, value="month",
                       bg='white', font=('Arial', 10)).pack(side=tk.LEFT, padx=10)

        # 坐标轴选择
        row3 = tk.Frame(chart_frame, bg='white')
        row3.pack(fill=tk.X, pady=8)

        tk.Label(row3, text="坐标轴类型:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.scale_var = tk.StringVar(value="linear")

        scale_frame = tk.Frame(row3, bg='white')
        scale_frame.pack(side=tk.LEFT)

        tk.Radiobutton(scale_frame, text="普通坐标", variable=self.scale_var, value="linear",
                       bg='white', font=('Arial', 10)).pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(scale_frame, text="对数坐标", variable=self.scale_var, value="log",
                       bg='white', font=('Arial', 10)).pack(side=tk.LEFT, padx=10)

        # 均线配置
        row4 = tk.Frame(chart_frame, bg='white')
        row4.pack(fill=tk.X, pady=8)

        tk.Label(row4, text="均线配置:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)

        ma_frame = tk.Frame(row4, bg='white')
        ma_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.ma_vars = {}
        for name, period in MA_PERIODS.items():
            var = tk.BooleanVar(value=(period in [20, 60]))
            self.ma_vars[name] = var
            tk.Checkbutton(ma_frame, text=name, variable=var, bg='white', font=('Arial', 9)).pack(side=tk.LEFT, padx=5)

        # 自定义均线输入
        row5 = tk.Frame(chart_frame, bg='white')
        row5.pack(fill=tk.X, pady=8)

        tk.Label(row5, text="自定义均线:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)

        self.custom_ma_var = tk.StringVar()
        custom_ma_entry = tk.Entry(row5, textvariable=self.custom_ma_var, font=('Arial', 10), width=15)
        custom_ma_entry.pack(side=tk.LEFT, padx=5)

        # 添加按钮
        tk.Button(
            row5,
            text="添加",
            command=self._add_custom_ma,
            font=('Arial', 9),
            bg='#2196F3',
            fg='white',
            padx=10,
            pady=2
        ).pack(side=tk.LEFT, padx=5)

        tk.Label(row5, text="(输入数字如200,125)", font=('Arial', 9), bg='white', fg='#666666').pack(side=tk.LEFT, padx=5)

        # 已添加的自定义均线显示区域
        row5b = tk.Frame(chart_frame, bg='white')
        row5b.pack(fill=tk.X, pady=2)

        tk.Label(row5b, text="", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.custom_ma_frame = tk.Frame(row5b, bg='white')
        self.custom_ma_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 存储自定义均线变量
        self.custom_ma_vars = {}

        # 日期范围
        row6 = tk.Frame(chart_frame, bg='white')
        row6.pack(fill=tk.X, pady=8)

        tk.Label(row6, text="日期范围:", font=('Arial', 10), bg='white', width=12, anchor=tk.W).pack(side=tk.LEFT)

        date_frame = tk.Frame(row6, bg='white')
        date_frame.pack(side=tk.LEFT)

        tk.Label(date_frame, text="从:", font=('Arial', 10), bg='white').pack(side=tk.LEFT)
        self.start_date_var = tk.StringVar()
        tk.Entry(date_frame, textvariable=self.start_date_var, font=('Arial', 10), width=12).pack(side=tk.LEFT, padx=3)

        tk.Label(date_frame, text="到:", font=('Arial', 10), bg='white').pack(side=tk.LEFT, padx=(15, 0))
        self.end_date_var = tk.StringVar()
        tk.Entry(date_frame, textvariable=self.end_date_var, font=('Arial', 10), width=12).pack(side=tk.LEFT, padx=3)

        tk.Label(date_frame, text="(YYYY-MM-DD, 留空表示全部)", font=('Arial', 9), bg='white', fg='#666666').pack(side=tk.LEFT, padx=10)

        # ===== 操作按钮 =====
        action_frame = tk.Frame(main_frame, bg='white')
        action_frame.pack(fill=tk.X, pady=15)

        tk.Button(
            action_frame,
            text="生成图表",
            command=self._generate_chart,
            font=('Arial', 12, 'bold'),
            bg='#673AB7',
            fg='white',
            activebackground='#5e35b1',
            padx=30,
            pady=10
        ).pack(side=tk.LEFT, padx=10)

        tk.Button(
            action_frame,
            text="保存图表",
            command=self._save_chart,
            font=('Arial', 12, 'bold'),
            bg='#009688',
            fg='white',
            activebackground='#00897b',
            padx=30,
            pady=10
        ).pack(side=tk.LEFT, padx=10)

        # ===== 日志区域 =====
        log_frame = tk.LabelFrame(
            main_frame,
            text=" 日志 ",
            font=('Arial', 12, 'bold'),
            bg='white',
            fg='#333333',
            padx=10,
            pady=10
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        # 创建带滚动条的文本框
        log_container = tk.Frame(log_frame, bg='white')
        log_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_container,
            height=10,
            wrap=tk.WORD,
            font=('Courier', 10),
            bg='#f5f5f5',
            fg='#333333',
            padx=5,
            pady=5
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scrollbar = ttk.Scrollbar(log_container, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text['yscrollcommand'] = log_scrollbar.set

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = tk.Label(
            main_frame,
            textvariable=self.status_var,
            font=('Arial', 10),
            bg='#e0e0e0',
            fg='#333333',
            anchor=tk.W,
            padx=10,
            pady=5
        )
        status_bar.pack(fill=tk.X, pady=(10, 0))

    def _log(self, message):
        """添加日志"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _update_data_info(self):
        """更新数据信息显示"""
        try:
            assets = self.db.get_all_assets()
            if not assets:
                self.data_info_label.config(text="数据库中暂无数据，请先更新数据")
                self._log("数据库中暂无数据")
                return

            info_text = ""
            for asset_code, currency_code in assets:
                summary = self.db.get_data_summary(asset_code, currency_code)
                info_text += f"{asset_code}: {summary['count']}条 ({summary['start_date']} ~ {summary['end_date']})  "
            self.data_info_label.config(text=info_text)
            self._log(f"数据信息已更新: {info_text}")
        except Exception as e:
            self.data_info_label.config(text=f"加载数据信息失败: {e}")
            self._log(f"错误: 加载数据信息失败 - {e}")

    def _update_data(self, asset_code):
        """更新数据"""
        def do_update():
            try:
                self.status_var.set("正在更新数据...")
                self._log("=" * 50)

                assets = [asset_code] if asset_code else list(SUPPORTED_ASSETS.keys())

                for asset in assets:
                    self._log(f"正在更新 {asset} 数据...")
                    latest_date = self.db.get_latest_date(asset, DEFAULT_CURRENCY)

                    if latest_date:
                        self._log(f"数据库最新日期: {latest_date}")
                    else:
                        self._log("数据库中没有数据，将获取全部历史数据...")

                    new_data = self.fetcher.fetch_incremental_data(asset, latest_date)

                    if new_data:
                        count = self.db.save_price_data(asset, DEFAULT_CURRENCY, new_data)
                        self._log(f"✓ 成功保存 {count} 条 {asset} 数据")
                    else:
                        self._log(f"{asset} 数据已是最新")

                self._log("=" * 50)
                self._log("更新完成")
                self._update_data_info()
                self.status_var.set("数据更新完成")

            except Exception as e:
                error_msg = str(e)
                self._log(f"✗ 更新失败: {error_msg}")
                self.status_var.set("更新失败")
                messagebox.showerror("错误", f"更新数据失败:\n{error_msg}")

        # 在后台线程中运行
        self._log("启动数据更新线程...")
        thread = threading.Thread(target=do_update)
        thread.daemon = True
        thread.start()

    def _add_custom_ma(self):
        """添加自定义均线"""
        custom_text = self.custom_ma_var.get().strip()
        if not custom_text:
            return

        try:
            periods = [int(p.strip()) for p in custom_text.split(',') if p.strip()]
            for period in periods:
                if period <= 0:
                    self._log(f"警告: 均线周期必须大于0")
                    continue
                if period in self.custom_ma_vars:
                    self._log(f"MA{period} 已存在")
                    continue

                # 创建复选框
                var = tk.BooleanVar(value=True)
                self.custom_ma_vars[period] = var

                cb = tk.Checkbutton(
                    self.custom_ma_frame,
                    text=f"MA{period}",
                    variable=var,
                    bg='white',
                    font=('Arial', 9),
                    selectcolor='#2196F3',
                    fg='#2196F3'
                )
                cb.pack(side=tk.LEFT, padx=5)

                self._log(f"已添加 MA{period}")

            # 清空输入框
            self.custom_ma_var.set("")

        except ValueError:
            self._log("错误: 请输入数字，用逗号分隔", "error")
            messagebox.showerror("错误", "请输入有效的数字，如: 200,125")

    def _get_selected_ma_periods(self):
        """获取选中的均线周期"""
        periods = []

        # 获取预设均线
        for name, var in self.ma_vars.items():
            if var.get():
                periods.append(MA_PERIODS[name])

        # 获取自定义均线（从复选框）
        for period, var in self.custom_ma_vars.items():
            if var.get():
                periods.append(period)

        return sorted(list(set(periods))) if periods else None

    def _generate_chart(self):
        """生成并显示图表"""
        try:
            asset = self.asset_var.get()
            timeframe = self.timeframe_var.get()
            log_scale = self.scale_var.get() == "log"
            ma_periods = self._get_selected_ma_periods()
            start_date = self.start_date_var.get().strip() or None
            end_date = self.end_date_var.get().strip() or None

            # 检查数据
            summary = self.db.get_data_summary(asset, DEFAULT_CURRENCY)
            if summary['count'] == 0:
                messagebox.showwarning("警告", f"数据库中没有 {asset} 数据，请先更新数据")
                return

            self.status_var.set("正在生成图表...")
            self._log("=" * 50)
            self._log(f"生成 {asset} {timeframe}K 图...")
            self._log(f"均线: {ma_periods if ma_periods else '无'}")
            self._log(f"坐标轴: {'对数' if log_scale else '普通'}")

            # 获取数据
            df = self.db.get_price_data(asset, DEFAULT_CURRENCY, start_date, end_date)

            if df.empty:
                messagebox.showwarning("警告", "没有数据可以绘制")
                return

            # 生成图表
            asset_name = SUPPORTED_ASSETS[asset]["name"]
            title = f"{asset_name} ({asset})"

            temp_path = f".temp_chart_{asset.lower()}.png"
            self.plotter.plot(
                df,
                title=title,
                timeframe=timeframe,
                ma_periods=ma_periods,
                log_scale=log_scale,
                save_path=temp_path,
                show=False
            )

            # 显示图表
            import subprocess
            import platform

            system = platform.system()
            try:
                if system == "Darwin":  # macOS
                    subprocess.run(["open", temp_path])
                elif system == "Windows":
                    os.startfile(temp_path)
                else:  # Linux
                    subprocess.run(["xdg-open", temp_path])
                self._log(f"✓ 图表已打开: {temp_path}")
            except Exception as e:
                self._log(f"无法自动打开图表: {e}")
                self._log(f"图表已保存到: {os.path.abspath(temp_path)}")

            self.status_var.set("图表生成完成")

        except Exception as e:
            self._log(f"✗ 生成图表失败: {e}")
            self.status_var.set("生成图表失败")
            messagebox.showerror("错误", f"生成图表失败: {e}")

    def _save_chart(self):
        """保存图表到指定位置"""
        try:
            asset = self.asset_var.get()
            timeframe = self.timeframe_var.get()
            log_scale = self.scale_var.get() == "log"
            ma_periods = self._get_selected_ma_periods()
            start_date = self.start_date_var.get().strip() or None
            end_date = self.end_date_var.get().strip() or None

            # 选择保存路径
            timeframe_names = {"day": "daily", "week": "weekly", "month": "monthly"}
            default_name = f"{asset.lower()}_{timeframe_names.get(timeframe, 'chart')}"
            if log_scale:
                default_name += "_log"
            default_name += ".png"

            file_path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG图片", "*.png"), ("所有文件", "*.*")],
                initialfile=default_name
            )

            if not file_path:
                return

            # 检查数据
            summary = self.db.get_data_summary(asset, DEFAULT_CURRENCY)
            if summary['count'] == 0:
                messagebox.showwarning("警告", f"数据库中没有 {asset} 数据，请先更新数据")
                return

            self.status_var.set("正在保存图表...")

            # 获取数据并生成图表
            df = self.db.get_price_data(asset, DEFAULT_CURRENCY, start_date, end_date)
            asset_name = SUPPORTED_ASSETS[asset]["name"]
            title = f"{asset_name} ({asset})"

            self.plotter.plot(
                df,
                title=title,
                timeframe=timeframe,
                ma_periods=ma_periods,
                log_scale=log_scale,
                save_path=file_path,
                show=False
            )

            self._log(f"✓ 图表已保存到: {file_path}")
            self.status_var.set("图表保存完成")
            messagebox.showinfo("成功", f"图表已保存到:\n{file_path}")

        except Exception as e:
            self._log(f"✗ 保存图表失败: {e}")
            self.status_var.set("保存图表失败")
            messagebox.showerror("错误", f"保存图表失败: {e}")


def main():
    print("启动加密货币价格追踪工具 GUI...")

    root = tk.Tk()
    root.title("加密货币价格追踪工具")

    # macOS 特殊处理
    import platform
    if platform.system() == 'Darwin':
        root.lift()  # 将窗口提到最前
        root.attributes('-topmost', True)  # 临时置顶
        root.after_idle(root.attributes, '-topmost', False)  # 然后取消置顶

    print("Tk根窗口已创建")
    app = CryptoTrackerGUI(root)
    print("进入主循环")
    root.mainloop()
    print("GUI已关闭")


if __name__ == "__main__":
    main()
