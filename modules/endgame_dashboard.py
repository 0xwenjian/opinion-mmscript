#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫尾盘策略控制台仪表盘
模仿 Polymarket 风格
"""

import time
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.columns import Columns
from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.progress import Progress, BarColumn, TextColumn
from rich.style import Style


@dataclass
class TradeRecord:
    time: str
    market: str
    direction: str
    side: str
    price: float
    shares: float
    status: str


@dataclass
class MarketInfo:
    topic_id: int
    name: str
    yes_price: float = 0.0
    no_price: float = 0.0
    remaining_min: float = 0.0
    volume: float = 0.0


@dataclass
class PositionInfo:
    topic_id: int
    title: str
    side: str
    entry_price: float
    current_price: float
    shares: float
    pnl: float
    remaining_min: float


@dataclass
class DashboardState:
    wallet_address: str = ""
    usdc_balance: float = 0.0
    active_orders: int = 0
    today_pnl: float = 0.0
    total_trades: int = 0
    
    api_status: bool = True
    ws_status: bool = False
    proxy_status: bool = True
    last_update: str = ""
    
    strategy_state: str = "监控中"
    open_count: int = 0
    closed_count: int = 0
    total_bet: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    
    markets: List[MarketInfo] = field(default_factory=list)
    positions: List[PositionInfo] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    dry_run: bool = False
    
    min_win_price: float = 0.70
    max_win_price: float = 0.95
    stop_loss_price: float = 0.70
    bet_amount: float = 10.0


class EndgameDashboard:
    def __init__(self):
        self.console = Console()
        self.state = DashboardState()
        self._start_time = time.time()
    
    def update_account(self, address: str, balance: float, orders: int, pnl: float):
        self.state.wallet_address = address
        self.state.usdc_balance = balance
        self.state.active_orders = orders
        self.state.today_pnl = pnl
    
    def update_system_status(self, api: bool, ws: bool, proxy: bool):
        self.state.api_status = api
        self.state.ws_status = ws
        self.state.proxy_status = proxy
        self.state.last_update = datetime.now().strftime("%H:%M:%S")
    
    def update_strategy(self, state: str, open_count: int = 0, closed_count: int = 0,
                       total_bet: float = 0.0, realized_pnl: float = 0.0, unrealized_pnl: float = 0.0):
        self.state.strategy_state = state
        self.state.open_count = open_count
        self.state.closed_count = closed_count
        self.state.total_bet = total_bet
        self.state.realized_pnl = realized_pnl
        self.state.unrealized_pnl = unrealized_pnl
    
    def clear_markets(self):
        self.state.markets = []
    
    def remove_market(self, name: str):
        self.state.markets = [m for m in self.state.markets if m.name != name]
    
    def update_market(self, topic_id: int, name: str, yes_price: float, no_price: float,
                     remaining_min: float = 0.0, volume: float = 0.0):
        for m in self.state.markets:
            if m.topic_id == topic_id:
                m.yes_price = yes_price
                m.no_price = no_price
                m.remaining_min = remaining_min
                m.volume = volume
                return
        
        self.state.markets.append(MarketInfo(
            topic_id=topic_id, name=name, yes_price=yes_price, no_price=no_price,
            remaining_min=remaining_min, volume=volume,
        ))
    
    def update_positions(self, positions: list):
        self.state.positions = []
        for p in positions:
            remaining_min = (p.end_time - time.time()) / 60 if p.end_time > 0 else 0
            pnl = (p.current_price - p.entry_price) * p.shares
            self.state.positions.append(PositionInfo(
                topic_id=p.topic_id,
                title=p.title,
                side=p.side,
                entry_price=p.entry_price,
                current_price=p.current_price,
                shares=p.shares,
                pnl=pnl,
                remaining_min=remaining_min,
            ))
    
    def add_trade(self, market: str, direction: str, side: str, 
                  price: float, shares: float, status: str):
        trade = TradeRecord(
            time=datetime.now().strftime("%H:%M:%S"),
            market=market, direction=direction, side=side,
            price=price, shares=shares, status=status,
        )
        self.state.trades.insert(0, trade)
        self.state.total_trades += 1
        if len(self.state.trades) > 20:
            self.state.trades = self.state.trades[:20]
    
    def set_dry_run(self, dry_run: bool):
        self.state.dry_run = dry_run

    def _make_header(self) -> Panel:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode = "[yellow]TEST[/]" if self.state.dry_run else "[green]LIVE[/]"
        
        elapsed = int(time.time() - self._start_time)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        runtime = f"{h:02d}:{m:02d}:{s:02d}"
        
        # Polymarket 风格标题
        title_text = Text()
        title_text.append("  ENDGAME  ", style="bold white on blue")
        title_text.append("  ", style="")
        title_text.append("Sweep Strategy", style="bold cyan")
        
        status_text = Text()
        status_text.append(f" {mode} ", style="")
        status_text.append(f" | Runtime: {runtime} | {now}", style="dim white")
        
        return Panel(
            Group(Align.center(title_text), Align.center(status_text)),
            box=ROUNDED, style="blue", padding=(0, 1),
        )

    def _make_portfolio_panel(self) -> Panel:
        """Portfolio 面板 - 类似 Polymarket"""
        total_pnl = self.state.realized_pnl + self.state.unrealized_pnl
        pnl_color = "green" if total_pnl >= 0 else "red"
        pnl_sign = "+" if total_pnl >= 0 else ""
        
        table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        table.add_column("", width=20)
        table.add_column("", justify="right")
        
        # 钱包地址
        addr = self.state.wallet_address
        if len(addr) > 10:
            addr = f"{addr[:6]}...{addr[-4:]}"
        table.add_row(
            Text("Wallet", style="dim"),
            Text(addr or "N/A", style="cyan")
        )
        
        # 余额
        table.add_row(
            Text("Balance", style="dim"),
            Text(f"${self.state.usdc_balance:.2f}", style="bold green")
        )
        
        # 总投注
        table.add_row(
            Text("Total Bet", style="dim"),
            Text(f"${self.state.total_bet:.2f}", style="yellow")
        )
        
        # 已实现盈亏
        r_color = "green" if self.state.realized_pnl >= 0 else "red"
        r_sign = "+" if self.state.realized_pnl >= 0 else ""
        table.add_row(
            Text("Realized P&L", style="dim"),
            Text(f"{r_sign}${self.state.realized_pnl:.2f}", style=r_color)
        )
        
        # 未实现盈亏
        u_color = "green" if self.state.unrealized_pnl >= 0 else "red"
        u_sign = "+" if self.state.unrealized_pnl >= 0 else ""
        table.add_row(
            Text("Unrealized P&L", style="dim"),
            Text(f"{u_sign}${self.state.unrealized_pnl:.2f}", style=u_color)
        )
        
        # 总盈亏
        table.add_row(
            Text("Total P&L", style="bold white"),
            Text(f"{pnl_sign}${total_pnl:.2f}", style=f"bold {pnl_color}")
        )
        
        return Panel(table, title="[bold white]Portfolio[/]", box=ROUNDED, style="white", padding=(0, 1))

    def _make_config_panel(self) -> Panel:
        """配置面板"""
        table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        table.add_column("", width=16)
        table.add_column("", justify="right")
        
        table.add_row(
            Text("Win Rate", style="dim"),
            Text(f"{self.state.min_win_price*100:.0f}% - {self.state.max_win_price*100:.0f}%", style="cyan")
        )
        table.add_row(
            Text("Stop Loss", style="dim"),
            Text(f"{self.state.stop_loss_price:.2f}", style="red")
        )
        table.add_row(
            Text("Bet Amount", style="dim"),
            Text(f"${self.state.bet_amount:.2f}", style="yellow")
        )
        table.add_row(
            Text("API Status", style="dim"),
            Text("Online" if self.state.api_status else "Offline", 
                 style="green" if self.state.api_status else "red")
        )
        table.add_row(
            Text("Proxy", style="dim"),
            Text("ON" if self.state.proxy_status else "OFF", 
                 style="green" if self.state.proxy_status else "dim")
        )
        table.add_row(
            Text("Updated", style="dim"),
            Text(self.state.last_update or "-", style="dim")
        )
        
        return Panel(table, title="[bold white]Config[/]", box=ROUNDED, style="white", padding=(0, 1))

    def _make_positions_panel(self) -> Panel:
        """持仓面板 - Polymarket 风格"""
        table = Table(show_header=True, header_style="bold white", box=None, expand=True, padding=(0, 1))
        table.add_column("Market", width=30, no_wrap=True)
        table.add_column("Side", justify="center", width=5)
        table.add_column("Qty", justify="right", width=6)
        table.add_column("Avg", justify="right", width=7)
        table.add_column("Current", justify="right", width=7)
        table.add_column("P&L", justify="right", width=10)
        table.add_column("Time", justify="right", width=6)
        
        if self.state.positions:
            for p in self.state.positions[:8]:
                name = p.title[:28] + ".." if len(p.title) > 30 else p.title
                
                # Side 颜色
                side_style = "bold green" if p.side == "YES" else "bold red"
                
                # P&L 颜色和格式
                pnl_style = "green" if p.pnl >= 0 else "red"
                pnl_text = f"+${p.pnl:.2f}" if p.pnl >= 0 else f"-${abs(p.pnl):.2f}"
                
                # 当前价格预警
                price_style = "white"
                if p.current_price < self.state.stop_loss_price:
                    price_style = "bold red blink"
                elif p.current_price >= p.entry_price:
                    price_style = "green"
                
                # 剩余时间
                if p.remaining_min <= 0:
                    time_text = "END"
                    time_style = "bold red"
                elif p.remaining_min <= 2:
                    time_text = f"{p.remaining_min:.0f}m"
                    time_style = "bold yellow"
                else:
                    time_text = f"{p.remaining_min:.0f}m"
                    time_style = "cyan"
                
                table.add_row(
                    Text(name, style="white"),
                    Text(p.side, style=side_style),
                    f"{p.shares:.1f}",
                    f"{p.entry_price:.3f}",
                    Text(f"{p.current_price:.3f}", style=price_style),
                    Text(pnl_text, style=pnl_style),
                    Text(time_text, style=time_style),
                )
        else:
            table.add_row(
                Text("No open positions", style="dim"),
                "-", "-", "-", "-", "-", "-"
            )
        
        title = f"[bold white]Positions[/] [dim]({self.state.open_count} open)[/]"
        return Panel(table, title=title, box=ROUNDED, style="cyan", padding=(0, 0))

    def _make_markets_panel(self) -> Panel:
        """市场面板 - Polymarket 风格"""
        table = Table(show_header=True, header_style="bold white", box=None, expand=True, padding=(0, 1))
        table.add_column("Market", width=32, no_wrap=True)
        table.add_column("Yes", justify="center", width=6)
        table.add_column("No", justify="center", width=6)
        table.add_column("Win%", justify="center", width=6)
        table.add_column("Vol", justify="right", width=8)
        table.add_column("Time", justify="right", width=5)
        
        if self.state.markets:
            sorted_markets = sorted(self.state.markets, key=lambda x: x.remaining_min)
            for m in sorted_markets[:10]:
                name = m.name[:30] + ".." if len(m.name) > 32 else m.name
                
                # 高胜率一方
                high_price = max(m.yes_price, m.no_price)
                win_pct = high_price * 100
                
                # 是否符合下单条件
                in_range = self.state.min_win_price <= high_price <= self.state.max_win_price
                
                # Yes/No 价格条
                yes_style = "green" if m.yes_price >= m.no_price else "dim green"
                no_style = "red" if m.no_price > m.yes_price else "dim red"
                
                # Win% 样式
                if in_range:
                    win_style = "bold green"
                elif win_pct > 95:
                    win_style = "dim yellow"
                else:
                    win_style = "dim"
                
                # 交易量格式化
                if m.volume >= 1000000:
                    vol_text = f"${m.volume/1000000:.1f}M"
                elif m.volume >= 1000:
                    vol_text = f"${m.volume/1000:.0f}K"
                else:
                    vol_text = f"${m.volume:.0f}"
                
                # 剩余时间
                time_text = f"{m.remaining_min:.0f}m" if m.remaining_min > 0 else "-"
                time_style = "yellow" if m.remaining_min <= 5 else "cyan"
                
                table.add_row(
                    Text(name, style="white"),
                    Text(f"{m.yes_price*100:.0f}c", style=yes_style),
                    Text(f"{m.no_price*100:.0f}c", style=no_style),
                    Text(f"{win_pct:.0f}%", style=win_style),
                    Text(vol_text, style="dim"),
                    Text(time_text, style=time_style),
                )
        else:
            table.add_row(
                Text("Scanning markets...", style="dim yellow"),
                "-", "-", "-", "-", "-"
            )
        
        title = f"[bold white]Markets[/] [dim]({len(self.state.markets)} ending soon)[/]"
        return Panel(table, title=title, box=ROUNDED, style="green", padding=(0, 0))

    def _make_trades_panel(self) -> Panel:
        """交易记录面板"""
        table = Table(show_header=True, header_style="bold white", box=None, expand=True, padding=(0, 1))
        table.add_column("Time", width=8)
        table.add_column("Action", width=10)
        table.add_column("Price", justify="right", width=7)
        table.add_column("Qty", justify="right", width=6)
        table.add_column("Status", justify="center", width=6)
        
        if self.state.trades:
            for t in self.state.trades[:10]:
                # Action 样式
                if t.direction == "BUY":
                    action_text = f"BUY {t.side}"
                    action_style = "green"
                else:
                    action_text = f"SELL {t.side}"
                    action_style = "red"
                
                # Status 样式
                status_styles = {
                    "成功": ("OK", "green"),
                    "测试": ("TEST", "cyan"),
                    "止损": ("STOP", "red"),
                    "结算": ("SETTLE", "yellow"),
                    "卖出": ("SOLD", "yellow"),
                }
                status_text, status_style = status_styles.get(t.status, (t.status, "white"))
                
                table.add_row(
                    Text(t.time, style="dim"),
                    Text(action_text, style=action_style),
                    f"{t.price:.4f}",
                    f"{t.shares:.1f}",
                    Text(status_text, style=status_style),
                )
        else:
            table.add_row(
                Text("No trades yet", style="dim"),
                "-", "-", "-", "-"
            )
        
        title = f"[bold white]Trades[/] [dim]({self.state.total_trades} total)[/]"
        return Panel(table, title=title, box=ROUNDED, style="yellow", padding=(0, 0))

    def make_layout(self) -> Layout:
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="top", size=12),
            Layout(name="middle", size=12),
            Layout(name="bottom", size=14),
        )
        
        layout["top"].split_row(
            Layout(name="portfolio", ratio=1),
            Layout(name="config", ratio=1),
        )
        
        layout["middle"].update(self._make_positions_panel())
        
        layout["bottom"].split_row(
            Layout(name="markets", ratio=3),
            Layout(name="trades", ratio=2),
        )
        
        layout["header"].update(self._make_header())
        layout["portfolio"].update(self._make_portfolio_panel())
        layout["config"].update(self._make_config_panel())
        layout["markets"].update(self._make_markets_panel())
        layout["trades"].update(self._make_trades_panel())
        
        return layout
    
    def run_live(self, refresh_rate: float = 0.5):
        with Live(
            self.make_layout(), 
            console=self.console, 
            refresh_per_second=int(1/refresh_rate), 
            screen=True,
        ) as live:
            try:
                while True:
                    live.update(self.make_layout())
                    time.sleep(refresh_rate)
            except KeyboardInterrupt:
                pass
