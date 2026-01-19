#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
套利策略监控仪表盘模块
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
from rich.box import ROUNDED


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
    name: str
    up_price: float = 0.0
    down_price: float = 0.0
    remaining_min: float = 0.0
    countdown_sec: float = -1
    countdown_direction: str = ""
    leg2_wait_sec: float = -1


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
    leg1_done: bool = False
    leg2_done: bool = False
    leg1_price: float = 0.0
    leg2_price: float = 0.0
    
    markets: List[MarketInfo] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    dry_run: bool = False
    
    price_sum_threshold: float = 0.95
    shares_per_leg: int = 20


class ArbitrageDashboard:
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
    
    def update_strategy(self, state: str, leg1: bool = False, leg2: bool = False, 
                       leg1_price: float = 0.0, leg2_price: float = 0.0):
        self.state.strategy_state = state
        self.state.leg1_done = leg1
        self.state.leg2_done = leg2
        self.state.leg1_price = leg1_price
        self.state.leg2_price = leg2_price
    
    def clear_markets(self):
        self.state.markets = []
    
    def remove_market(self, name: str):
        self.state.markets = [m for m in self.state.markets if m.name != name]
    
    def update_market(self, name: str, up_price: float, down_price: float,
                     remaining_min: float = 0.0, countdown_sec: float = -1,
                     countdown_direction: str = "", leg2_wait_sec: float = -1):
        for m in self.state.markets:
            if m.name == name:
                m.up_price = up_price
                m.down_price = down_price
                m.remaining_min = remaining_min
                m.countdown_sec = countdown_sec
                m.countdown_direction = countdown_direction
                m.leg2_wait_sec = leg2_wait_sec
                return
        
        self.state.markets.append(MarketInfo(
            name=name, up_price=up_price, down_price=down_price,
            remaining_min=remaining_min, countdown_sec=countdown_sec,
            countdown_direction=countdown_direction, leg2_wait_sec=leg2_wait_sec,
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
        if len(self.state.trades) > 10:
            self.state.trades = self.state.trades[:10]
    
    def set_dry_run(self, dry_run: bool):
        self.state.dry_run = dry_run

    def _make_header(self) -> Panel:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode = "[yellow]TEST[/]" if self.state.dry_run else "[green]LIVE[/]"
        
        elapsed = int(time.time() - self._start_time)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        runtime = f"{h:02d}:{m:02d}:{s:02d}"
        
        title = Text("ARBITRAGE STRATEGY", style="bold white")
        info = Text(f"[{mode}] | Runtime: {runtime} | {now}", style="dim")
        
        return Panel(
            Group(Align.center(title), Align.center(info)),
            box=ROUNDED, style="cyan", padding=(0, 0),
        )

    def _make_stat_box(self, title: str, value: str, style: str = "white") -> Panel:
        content = Text()
        content.append(f"{value}\n", style=f"bold {style}")
        content.append(f"{title}", style="dim")
        return Panel(Align.center(content), box=ROUNDED, style=style, padding=(0, 1))

    def _make_stats_row(self) -> Columns:
        addr = self.state.wallet_address
        if len(addr) > 10:
            addr = f"{addr[:6]}...{addr[-4:]}"
        
        pnl_style = "green" if self.state.today_pnl >= 0 else "red"
        pnl_sign = "+" if self.state.today_pnl >= 0 else ""
        
        boxes = [
            self._make_stat_box("Wallet", addr or "N/A", "cyan"),
            self._make_stat_box("USDC", f"${self.state.usdc_balance:.2f}", "green"),
            self._make_stat_box("Trades", str(self.state.total_trades), "yellow"),
            self._make_stat_box("PnL", f"{pnl_sign}${self.state.today_pnl:.2f}", pnl_style),
        ]
        return Columns(boxes, equal=True, expand=True)

    def _make_strategy_panel(self) -> Panel:
        content = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        content.add_column("", width=12)
        content.add_column("")
        
        state_style = "yellow" if self.state.strategy_state == "监控中" else "green"
        content.add_row(Text("Status", style="dim"), Text(self.state.strategy_state, style=f"bold {state_style}"))
        
        leg1_icon = "[x]" if self.state.leg1_done else "[ ]"
        leg1_price = f" @ {self.state.leg1_price:.4f}" if self.state.leg1_price > 0 else ""
        content.add_row(Text("Leg 1", style="dim"), Text(f"{leg1_icon}{leg1_price}", style="green" if self.state.leg1_done else "dim"))
        
        leg2_icon = "[x]" if self.state.leg2_done else "[ ]"
        leg2_price = f" @ {self.state.leg2_price:.4f}" if self.state.leg2_price > 0 else ""
        content.add_row(Text("Leg 2", style="dim"), Text(f"{leg2_icon}{leg2_price}", style="green" if self.state.leg2_done else "dim"))
        
        if self.state.leg1_price > 0 and self.state.leg2_price > 0:
            profit = 1.0 - self.state.leg1_price - self.state.leg2_price
            content.add_row(Text("Profit", style="dim"), Text(f"{profit:.4f}/share", style="bold green" if profit > 0 else "red"))
        
        return Panel(content, title="[magenta]Strategy[/]", box=ROUNDED, style="magenta", padding=(0, 1))

    def _make_system_panel(self) -> Panel:
        content = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        content.add_column("", width=10)
        content.add_column("")
        
        api_style = "green" if self.state.api_status else "red"
        content.add_row(Text("API", style="dim"), Text("OK" if self.state.api_status else "ERR", style=api_style))
        
        proxy_style = "green" if self.state.proxy_status else "dim"
        content.add_row(Text("Proxy", style="dim"), Text("ON" if self.state.proxy_status else "OFF", style=proxy_style))
        
        content.add_row(Text("Updated", style="dim"), Text(self.state.last_update or "-", style="cyan"))
        
        return Panel(content, title="[blue]System[/]", box=ROUNDED, style="blue", padding=(0, 1))

    def _make_markets_panel(self) -> Panel:
        table = Table(show_header=True, header_style="bold", box=None, expand=True)
        table.add_column("Market", width=28)
        table.add_column("YES", justify="center", width=7)
        table.add_column("NO", justify="center", width=7)
        table.add_column("SUM", justify="center", width=9)
        table.add_column("Time", justify="center", width=6)
        
        if self.state.markets:
            for m in self.state.markets[:8]:
                name = m.name[:26] + ".." if len(m.name) > 28 else m.name
                total = m.up_price + m.down_price
                
                if total <= 0.95:
                    sum_style = "bold green"
                elif total <= 1.0:
                    sum_style = "yellow"
                else:
                    sum_style = "red"
                
                remaining = f"{m.remaining_min:.0f}m" if m.remaining_min > 0 else "-"
                
                table.add_row(
                    name,
                    f"{m.up_price:.3f}",
                    f"{m.down_price:.3f}",
                    Text(f"{total:.4f}", style=sum_style),
                    Text(remaining, style="cyan"),
                )
        else:
            table.add_row(Text("Waiting...", style="dim"), "-", "-", "-", "-")
        
        return Panel(table, title="[green]Markets[/]", box=ROUNDED, style="green", padding=(0, 0))

    def _make_trades_panel(self) -> Panel:
        table = Table(show_header=True, header_style="bold", box=None, expand=True)
        table.add_column("Time", width=8)
        table.add_column("Side", width=10)
        table.add_column("Price", width=7)
        table.add_column("Status", width=6)
        
        if self.state.trades:
            for t in self.state.trades[:6]:
                dir_text = f"{'B' if t.direction == 'BUY' else 'S'} {t.side}"
                status_style = {"成功": "green", "测试": "cyan", "止损": "red", "超时": "yellow"}.get(t.status, "white")
                
                table.add_row(
                    Text(t.time, style="dim"),
                    dir_text,
                    f"{t.price:.4f}",
                    Text(t.status, style=status_style),
                )
        else:
            table.add_row(Text("No trades", style="dim"), "-", "-", "-")
        
        return Panel(table, title=f"[yellow]Trades ({self.state.total_trades})[/]", box=ROUNDED, style="yellow", padding=(0, 0))

    def make_layout(self) -> Layout:
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="stats", size=5),
            Layout(name="middle", size=10),
            Layout(name="bottom", size=12),
        )
        
        layout["middle"].split_row(
            Layout(name="strategy", ratio=1),
            Layout(name="system", ratio=1),
        )
        
        layout["bottom"].split_row(
            Layout(name="markets", ratio=3),
            Layout(name="trades", ratio=2),
        )
        
        layout["header"].update(self._make_header())
        layout["stats"].update(self._make_stats_row())
        layout["strategy"].update(self._make_strategy_panel())
        layout["system"].update(self._make_system_panel())
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
