#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
刷量策略模块
策略：买入高胜率市场，上涨1-2%后卖出，循环刷量
"""

import time
import requests
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from threading import Lock

from loguru import logger

# Telegram 通知配置
TG_BOT_TOKEN = "8249028552:AAHeLHbhBEzFoUIAhqEhnqlf3e2x3TvN-Wo"
TG_CHAT_ID = "2033931889"


def send_tg_notification(message: str, proxy: Dict = None):
    if not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=10, proxies=proxy)
    except Exception as e:
        logger.warning(f"TG通知失败: {e}")


@dataclass
class Position:
    """持仓记录"""
    topic_id: int
    title: str
    side: str
    entry_price: float
    shares: float
    entry_time: float
    current_price: float = 0.0
    status: str = "OPEN"  # OPEN, CLOSED


@dataclass
class MarketState:
    """市场状态"""
    topic_id: int
    title: str
    yes_price: float = 0.0
    no_price: float = 0.0
    volume: float = 0.0
    last_update: float = 0.0


class VolumeStrategy:
    """
    刷量策略
    - 买入高胜率市场（70%-95%）
    - 上涨1-2%后卖出
    - 止损线防止大亏
    """
    
    # 胜率筛选
    MIN_WIN_PRICE = 0.90
    MAX_WIN_PRICE = 0.99
    
    # 最大买卖价差
    MAX_SPREAD = 0.01
    
    # 止盈止损 (相对于入场价)
    TAKE_PROFIT_PCT = 0.01  # 上涨1%卖出
    STOP_LOSS_PCT = 0.03    # 下跌3%止损
    
    # 每次下单金额 (USD)
    BET_AMOUNT = 10.0
    
    # 最大同时持仓数
    MAX_POSITIONS = 5
    
    # 最小交易量筛选
    MIN_VOLUME = 10000
    
    # 持仓超时 (秒) - 超时后强制卖出
    POSITION_TIMEOUT = 3600  # 1小时
    
    def __init__(self, fetcher, trader, dashboard=None, dry_run: bool = False, proxy: Dict = None, config: Dict = None):
        self.fetcher = fetcher
        self.trader = trader
        self.dashboard = dashboard
        self.dry_run = dry_run
        self.proxy = proxy
        
        # 从配置加载参数
        if config:
            vol_cfg = config.get("volume_strategy", {})
            self.MIN_WIN_PRICE = vol_cfg.get("min_win_price", 0.90)
            self.MAX_WIN_PRICE = vol_cfg.get("max_win_price", 0.99)
            self.MAX_SPREAD = vol_cfg.get("max_spread", 0.01)
            self.TAKE_PROFIT_PCT = vol_cfg.get("take_profit_pct", 0.01)
            self.STOP_LOSS_PCT = vol_cfg.get("stop_loss_pct", 0.03)
            self.BET_AMOUNT = vol_cfg.get("bet_amount", 10.0)
            self.MAX_POSITIONS = vol_cfg.get("max_positions", 5)
            self.MIN_VOLUME = vol_cfg.get("min_volume", 10000)
            self.POSITION_TIMEOUT = vol_cfg.get("position_timeout", 3600)
        
        self.markets: Dict[int, MarketState] = {}
        self.positions: Dict[int, Position] = {}
        self.lock = Lock()
        self.running = False
        
        # 统计
        self.total_trades = 0
        self.total_volume = 0.0
        self.total_profit = 0.0
        self.win_count = 0
        self.loss_count = 0
    
    def fetch_markets(self) -> List[Dict]:
        """获取市场列表"""
        try:
            markets = self.fetcher.fetch_markets(limit=50, fetch_all=True)
            filtered = []
            
            for m in markets:
                if m.get("isMulti", False):
                    continue
                
                topic_id = m.get("topicId") or m.get("marketId")
                if not topic_id:
                    continue
                
                try:
                    topic_id = int(topic_id)
                except:
                    continue
                
                volume = float(m.get("volume", 0) or 0)
                if volume < self.MIN_VOLUME:
                    continue
                
                yes_price = float(m.get("yesPrice", 0) or 0)
                no_price = 1 - yes_price if yes_price > 0 else 0
                
                # 筛选高胜率市场
                high_price = max(yes_price, no_price)
                if self.MIN_WIN_PRICE <= high_price <= self.MAX_WIN_PRICE:
                    filtered.append({
                        "topic_id": topic_id,
                        "title": m.get("title", ""),
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "volume": volume,
                        "spread": 0,  # 假设价差为0，因为API获取详情有问题
                    })
            
            logger.info(f"找到 {len(filtered)} 个符合条件的高胜率市场")
            return filtered
        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return []
    
    def fetch_market_prices(self, topic_id: int) -> tuple:
        """获取市场最新价格和价差"""
        try:
            # 先尝试从缓存的市场列表获取
            markets = self.fetcher.fetch_markets(limit=50, fetch_all=True)
            for m in markets:
                tid = m.get("topicId") or m.get("marketId")
                if tid and int(tid) == topic_id:
                    yes_price = float(m.get("yesPrice", 0) or 0)
                    no_price = 1 - yes_price if yes_price > 0 else 0
                    return yes_price, no_price, 0.0
            
            return 0.0, 0.0, 1.0
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            return 0.0, 0.0, 1.0
    
    def get_high_win_side(self, yes_price: float, no_price: float) -> Optional[tuple]:
        """获取高胜率一方"""
        if yes_price >= no_price:
            high_side = "YES"
            high_price = yes_price
        else:
            high_side = "NO"
            high_price = no_price
        
        if self.MIN_WIN_PRICE <= high_price <= self.MAX_WIN_PRICE:
            return (high_side, high_price)
        return None
    
    def execute_buy(self, topic_id: int, title: str, side: str, price: float) -> bool:
        """执行买入"""
        logger.info(f"[买入] {title[:30]} {side} @ {price:.4f} ${self.BET_AMOUNT}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=topic_id,
                outcome=side,
                amount=self.BET_AMOUNT,
                price=price,
                order_type=2,
                side="BUY",
            )
            success = result is not None
        
        if success:
            shares = self.BET_AMOUNT / price
            
            self.positions[topic_id] = Position(
                topic_id=topic_id,
                title=title,
                side=side,
                entry_price=price,
                shares=shares,
                entry_time=time.time(),
                current_price=price,
                status="OPEN",
            )
            
            self.total_trades += 1
            self.total_volume += self.BET_AMOUNT
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=title,
                    direction="BUY",
                    side=side,
                    price=price,
                    shares=shares,
                    status="买入",
                )
        
        return success
    
    def execute_sell(self, position: Position, current_price: float, reason: str) -> bool:
        """执行卖出"""
        # 减少1%避免精度问题
        sell_shares = position.shares * 0.99
        sell_shares = int(sell_shares * 100) / 100
        
        logger.info(f"[{reason}] {position.title[:30]} {position.side} @ {current_price:.4f} x {sell_shares:.2f}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=position.topic_id,
                outcome=position.side,
                amount=sell_shares,
                price=current_price,
                order_type=2,
                side="SELL",
            )
            success = result is not None
        
        if success:
            pnl = (current_price - position.entry_price) * position.shares
            self.total_profit += pnl
            self.total_volume += current_price * position.shares
            self.total_trades += 1
            
            if pnl >= 0:
                self.win_count += 1
            else:
                self.loss_count += 1
            
            position.status = "CLOSED"
            
            pnl_text = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            logger.info(f"  盈亏: {pnl_text}")
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=position.title,
                    direction="SELL",
                    side=position.side,
                    price=current_price,
                    shares=position.shares,
                    status=reason,
                )
            
            # 发送通知
            msg = f"""{'📈' if pnl >= 0 else '📉'} <b>{reason}</b>
{position.title[:40]}
{position.side} | 入:{position.entry_price:.3f} 出:{current_price:.3f}
盈亏: <code>{pnl_text}</code>"""
            send_tg_notification(msg, self.proxy)
        
        return success
    
    def check_take_profit(self, position: Position) -> bool:
        """检查是否触发止盈"""
        target_price = position.entry_price * (1 + self.TAKE_PROFIT_PCT)
        return position.current_price >= target_price
    
    def check_stop_loss(self, position: Position) -> bool:
        """检查是否触发止损"""
        stop_price = position.entry_price * (1 - self.STOP_LOSS_PCT)
        return position.current_price <= stop_price
    
    def check_timeout(self, position: Position) -> bool:
        """检查是否超时"""
        return time.time() - position.entry_time >= self.POSITION_TIMEOUT
    
    def process_positions(self):
        """处理持仓：止盈、止损、超时"""
        for topic_id, position in list(self.positions.items()):
            if position.status != "OPEN":
                continue
            
            # 获取最新价格
            yes_price, no_price, spread = self.fetch_market_prices(topic_id)
            if yes_price <= 0:
                continue
            
            current_price = yes_price if position.side == "YES" else no_price
            position.current_price = current_price
            
            # 检查止盈
            if self.check_take_profit(position):
                logger.info(f"触发止盈: {position.title[:30]}")
                self.execute_sell(position, current_price, "止盈")
                del self.positions[topic_id]
                continue
            
            # 检查止损
            if self.check_stop_loss(position):
                logger.warning(f"触发止损: {position.title[:30]}")
                self.execute_sell(position, current_price, "止损")
                del self.positions[topic_id]
                continue
            
            # 检查超时
            if self.check_timeout(position):
                logger.warning(f"持仓超时: {position.title[:30]}")
                self.execute_sell(position, current_price, "超时")
                del self.positions[topic_id]
                continue
    
    def find_and_buy(self):
        """寻找并买入新市场"""
        # 检查是否达到最大持仓数
        open_count = sum(1 for p in self.positions.values() if p.status == "OPEN")
        if open_count >= self.MAX_POSITIONS:
            return
        
        # 获取市场列表
        markets = self.fetch_markets()
        
        # 输出扫描到的市场到日志
        if markets:
            logger.info("=" * 50)
            logger.info(f"扫描到 {len(markets)} 个高胜率市场:")
            for i, m in enumerate(markets[:10]):
                high_price = max(m["yes_price"], m["no_price"])
                side = "NO" if m["no_price"] > m["yes_price"] else "YES"
                logger.info(f"  [{i+1}] {m['title'][:40]} | {side}={high_price:.3f} | Vol=${m['volume']/1000:.0f}K")
            logger.info("=" * 50)
        
        # 更新仪表盘市场数据
        if self.dashboard:
            self.dashboard.clear_markets()
            for m in markets:
                self.dashboard.update_market(
                    topic_id=m["topic_id"],
                    name=m["title"],
                    yes_price=m["yes_price"],
                    no_price=m["no_price"],
                    remaining_min=0,
                    volume=m["volume"],
                )
        
        for m in markets:
            topic_id = m["topic_id"]
            
            # 跳过已持仓的市场
            if topic_id in self.positions:
                continue
            
            # 获取最新价格和价差
            yes_price, no_price, spread = self.fetch_market_prices(topic_id)
            if yes_price <= 0:
                # 使用列表中的价格
                yes_price = m["yes_price"]
                no_price = m["no_price"]
                spread = 0
            
            # 检查价差是否符合要求
            if spread > self.MAX_SPREAD:
                logger.debug(f"价差过大跳过: {m['title'][:30]} spread={spread:.4f}")
                continue
            
            # 获取高胜率一方
            result = self.get_high_win_side(yes_price, no_price)
            if result:
                side, price = result
                logger.info(f"发现目标: {m['title'][:30]} {side}={price:.3f} spread={spread:.4f}")
                
                if self.execute_buy(topic_id, m["title"], side, price):
                    open_count += 1
                    if open_count >= self.MAX_POSITIONS:
                        break
    
    def update_dashboard(self):
        """更新仪表盘"""
        if not self.dashboard:
            return
        
        open_positions = [p for p in self.positions.values() if p.status == "OPEN"]
        
        # 计算未实现盈亏
        unrealized_pnl = sum((p.current_price - p.entry_price) * p.shares for p in open_positions)
        
        self.dashboard.update_strategy(
            state=f"刷量中 | {len(open_positions)}持仓",
            open_count=len(open_positions),
            closed_count=self.win_count + self.loss_count,
            total_bet=self.total_volume,
            realized_pnl=self.total_profit,
            unrealized_pnl=unrealized_pnl,
        )
        
        self.dashboard.update_positions(open_positions)
        
        self.dashboard.update_account(
            address=self.dashboard.state.wallet_address,
            balance=self.dashboard.state.usdc_balance,
            orders=len(open_positions),
            pnl=self.total_profit + unrealized_pnl,
        )
    
    def run(self):
        """运行策略"""
        self.running = True
        logger.info("启动刷量策略...")
        logger.info(f"参数: 胜率={self.MIN_WIN_PRICE*100:.0f}%-{self.MAX_WIN_PRICE*100:.0f}% 止盈={self.TAKE_PROFIT_PCT*100:.1f}% 止损={self.STOP_LOSS_PCT*100:.1f}% 金额=${self.BET_AMOUNT}")
        
        if self.dashboard:
            self.dashboard.set_dry_run(self.dry_run)
        
        scan_interval = 60  # 每60秒扫描新市场
        last_scan = 0
        
        try:
            while self.running:
                # 处理持仓
                self.process_positions()
                
                # 定期扫描新市场
                if time.time() - last_scan > scan_interval:
                    self.find_and_buy()
                    last_scan = time.time()
                    
                    # 打印统计
                    logger.info(f"统计: 交易={self.total_trades} 交易量=${self.total_volume:.2f} 盈亏=${self.total_profit:.2f} 胜率={self.win_count}/{self.win_count+self.loss_count}")
                
                self.update_dashboard()
                
                if self.dashboard:
                    self.dashboard.update_system_status(api=True, ws=False, proxy=self.proxy is not None)
                
                time.sleep(5)
        
        except KeyboardInterrupt:
            logger.info("策略停止")
        finally:
            self.running = False
    
    def stop(self):
        """停止策略"""
        self.running = False
