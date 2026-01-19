#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫尾盘策略模块
策略：监控即将结束的市场（20分钟内），下单胜率较高的一方
- 止损价格 0.7（低于此价格卖出）
- 市场结束后自动卖出持仓
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
class EndgamePosition:
    """持仓记录"""
    topic_id: int
    title: str
    side: str  # YES 或 NO
    entry_price: float
    shares: float
    entry_time: float
    end_time: float  # 市场结束时间
    current_price: float = 0.0
    status: str = "OPEN"  # OPEN, CLOSED, SETTLED


@dataclass
class EndgameMarketState:
    """扫尾盘市场状态"""
    topic_id: int
    title: str
    yes_price: float = 0.0
    no_price: float = 0.0
    remaining_min: float = 0.0
    end_time: float = 0.0
    volume: float = 0.0
    
    ordered: bool = False
    order_side: str = ""
    order_price: float = 0.0
    order_shares: float = 0.0
    order_time: float = 0.0


class EndgameStrategy:
    """
    扫尾盘策略
    - 筛选还有20分钟内结束的市场
    - 自动下单胜率较高的一方（价格高 = 胜率高）
    - 止损价格 0.7
    - 市场结束后自动卖出
    """
    
    # 扫尾盘时间窗口（分钟）
    MAX_REMAINING_MIN = 20.0
    MIN_REMAINING_MIN = 1.0
    
    # 胜率筛选
    MIN_WIN_PRICE = 0.70
    MAX_WIN_PRICE = 0.95
    
    # 止损价格
    STOP_LOSS_PRICE = 0.70
    
    # 每次下单金额 (USD)
    BET_AMOUNT = 10.0
    
    # 最小交易量筛选
    MIN_VOLUME = 1000
    
    def __init__(self, fetcher, trader, dashboard=None, dry_run: bool = False, proxy: Dict = None, config: Dict = None):
        self.fetcher = fetcher
        self.trader = trader
        self.dashboard = dashboard
        self.dry_run = dry_run
        self.proxy = proxy
        
        # 从配置加载参数
        if config:
            endgame_cfg = config.get("endgame_strategy", {})
            self.MAX_REMAINING_MIN = endgame_cfg.get("max_remaining_min", 20.0)
            self.MIN_REMAINING_MIN = endgame_cfg.get("min_remaining_min", 1.0)
            self.MIN_WIN_PRICE = endgame_cfg.get("min_win_price", 0.70)
            self.MAX_WIN_PRICE = endgame_cfg.get("max_win_price", 0.95)
            self.STOP_LOSS_PRICE = endgame_cfg.get("stop_loss_price", 0.70)
            self.BET_AMOUNT = endgame_cfg.get("bet_amount", 10.0)
            self.MIN_VOLUME = endgame_cfg.get("min_volume", 1000)
        
        self.markets: Dict[int, EndgameMarketState] = {}
        self.positions: Dict[int, EndgamePosition] = {}  # 持仓记录
        self.lock = Lock()
        self.running = False
        
        self.total_trades = 0
        self.total_bet = 0.0
        self.total_profit = 0.0
        self.completed_markets: set = set()
    
    def fetch_ending_markets(self) -> List[Dict]:
        """获取即将结束的市场"""
        try:
            markets = self.fetcher.fetch_markets(limit=50, fetch_all=True)
            ending_markets = []
            
            for m in markets:
                if m.get("isMulti", False):
                    continue
                
                title = m.get("title", "")
                topic_id = m.get("topicId") or m.get("marketId")
                if not topic_id:
                    continue
                
                try:
                    topic_id = int(topic_id)
                except (ValueError, TypeError):
                    continue
                
                if topic_id in self.completed_markets:
                    continue
                
                end_time_str = m.get("endTime", "")
                remaining_min = 0
                end_ts = 0
                if end_time_str:
                    try:
                        end_ts = datetime.fromisoformat(end_time_str.replace("Z", "+00:00")).timestamp()
                        remaining_min = (end_ts - time.time()) / 60
                    except:
                        pass
                
                if remaining_min < self.MIN_REMAINING_MIN or remaining_min > self.MAX_REMAINING_MIN:
                    continue
                
                volume = float(m.get("volume", 0) or 0)
                if volume < self.MIN_VOLUME:
                    continue
                
                yes_price = float(m.get("yesPrice", 0) or 0)
                
                ending_markets.append({
                    "topic_id": topic_id,
                    "title": title,
                    "remaining_min": remaining_min,
                    "end_time": end_ts,
                    "yes_price": yes_price,
                    "volume": volume,
                })
            
            logger.info(f"找到 {len(ending_markets)} 个即将结束的市场")
            return ending_markets
        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return []
    
    def fetch_market_prices(self, topic_id: int) -> tuple:
        """获取市场最新价格"""
        try:
            market = self.fetcher.fetch_market_by_id(topic_id)
            if not market:
                return 0.0, 0.0
            
            yes_price = market.get("yesBuyPrice", 0) or market.get("yesPrice", 0)
            no_price = 1 - yes_price if yes_price > 0 else 0.0
            
            return float(yes_price), float(no_price)
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            return 0.0, 0.0
    
    def get_high_win_side(self, state: EndgameMarketState) -> Optional[tuple]:
        """获取胜率较高的一方"""
        yes_price = state.yes_price
        no_price = state.no_price
        
        if yes_price >= no_price:
            high_side = "YES"
            high_price = yes_price
        else:
            high_side = "NO"
            high_price = no_price
        
        if self.MIN_WIN_PRICE <= high_price <= self.MAX_WIN_PRICE:
            return (high_side, high_price)
        
        return None
    
    def execute_buy(self, state: EndgameMarketState, side: str, price: float) -> bool:
        """执行买入"""
        logger.info(f"[扫尾盘] 买入 {state.title[:30]} {side} @ {price:.4f} 金额=${self.BET_AMOUNT}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=state.topic_id,
                outcome=side,
                amount=self.BET_AMOUNT,
                price=price,
                order_type=2,
                side="BUY",
            )
            success = result is not None
        
        if success:
            shares = self.BET_AMOUNT / price
            
            state.ordered = True
            state.order_side = side
            state.order_price = price
            state.order_shares = shares
            state.order_time = time.time()
            
            # 记录持仓
            self.positions[state.topic_id] = EndgamePosition(
                topic_id=state.topic_id,
                title=state.title,
                side=side,
                entry_price=price,
                shares=shares,
                entry_time=time.time(),
                end_time=state.end_time,
                current_price=price,
                status="OPEN",
            )
            
            self.total_trades += 1
            self.total_bet += self.BET_AMOUNT
            self.completed_markets.add(state.topic_id)
            
            win_rate = price * 100
            potential_profit = (1 - price) * shares
            
            msg = f"""🎯 <b>扫尾盘买入</b>
━━━━━━━━━━━━━━━
📌 市场: {state.title[:40]}
📊 方向: <b>{side}</b>
💰 价格: <code>{price:.4f}</code> (胜率 {win_rate:.1f}%)
💵 金额: <code>${self.BET_AMOUNT:.2f}</code>
📦 份数: <code>{shares:.2f}</code>
⏰ 剩余: <code>{state.remaining_min:.1f}</code> 分钟
📈 潜在利润: <code>${potential_profit:.2f}</code>
━━━━━━━━━━━━━━━"""
            send_tg_notification(msg, self.proxy)
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=state.title,
                    direction="BUY",
                    side=side,
                    price=price,
                    shares=shares,
                    status="成功" if not self.dry_run else "测试",
                )
        
        return success
    
    def execute_sell(self, position: EndgamePosition, current_price: float, reason: str) -> bool:
        """执行卖出"""
        logger.info(f"[{reason}] 卖出 {position.title[:30]} {position.side} @ {current_price:.4f} x {position.shares:.2f}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=position.topic_id,
                outcome=position.side,
                amount=position.shares,
                price=current_price,
                order_type=2,
                side="SELL",
            )
            success = result is not None
        
        if success:
            # 计算盈亏
            pnl = (current_price - position.entry_price) * position.shares
            self.total_profit += pnl
            
            position.status = "CLOSED"
            position.current_price = current_price
            
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            pnl_text = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            
            msg = f"""{pnl_emoji} <b>{reason}</b>
━━━━━━━━━━━━━━━
📌 市场: {position.title[:40]}
📊 方向: <b>{position.side}</b>
💰 买入价: <code>{position.entry_price:.4f}</code>
💰 卖出价: <code>{current_price:.4f}</code>
📦 份数: <code>{position.shares:.2f}</code>
💵 盈亏: <code>{pnl_text}</code>
━━━━━━━━━━━━━━━"""
            send_tg_notification(msg, self.proxy)
            
            if self.dashboard:
                status = "止损" if "止损" in reason else ("结算" if "结算" in reason else "卖出")
                self.dashboard.add_trade(
                    market=position.title,
                    direction="SELL",
                    side=position.side,
                    price=current_price,
                    shares=position.shares,
                    status=status,
                )
        
        return success
    
    def check_stop_loss(self, position: EndgamePosition) -> bool:
        """检查是否触发止损"""
        return position.current_price < self.STOP_LOSS_PRICE
    
    def check_market_ended(self, position: EndgamePosition) -> bool:
        """检查市场是否已结束"""
        return time.time() >= position.end_time
    
    def process_positions(self):
        """处理持仓：止损和市场结束卖出"""
        for topic_id, position in list(self.positions.items()):
            if position.status != "OPEN":
                continue
            
            # 获取最新价格
            yes_price, no_price = self.fetch_market_prices(topic_id)
            if yes_price <= 0:
                continue
            
            current_price = yes_price if position.side == "YES" else no_price
            position.current_price = current_price
            
            # 检查市场是否结束
            if self.check_market_ended(position):
                logger.info(f"市场结束，卖出持仓: {position.title[:30]}")
                self.execute_sell(position, current_price, "市场结算卖出")
                continue
            
            # 检查止损
            if self.check_stop_loss(position):
                logger.warning(f"触发止损: {position.title[:30]} 当前价格={current_price:.4f}")
                self.execute_sell(position, current_price, "止损卖出")
                continue
    
    def process_market(self, state: EndgameMarketState):
        """处理单个市场"""
        if state.ordered:
            return
        
        yes_price, no_price = self.fetch_market_prices(state.topic_id)
        if yes_price <= 0:
            return
        
        state.yes_price = yes_price
        state.no_price = no_price
        
        if state.end_time > 0:
            state.remaining_min = (state.end_time - time.time()) / 60
        
        if state.remaining_min < self.MIN_REMAINING_MIN:
            logger.warning(f"市场 {state.title[:30]} 剩余时间不足，跳过")
            self.completed_markets.add(state.topic_id)
            return
        
        if self.dashboard:
            self.dashboard.update_market(
                topic_id=state.topic_id,
                name=state.title,
                yes_price=yes_price,
                no_price=no_price,
                remaining_min=state.remaining_min,
                volume=state.volume,
            )
        
        result = self.get_high_win_side(state)
        if result:
            side, price = result
            logger.info(f"发现高胜率市场: {state.title[:30]} {side}={price:.4f} 剩余{state.remaining_min:.1f}分钟")
            self.execute_buy(state, side, price)
    
    def update_dashboard(self):
        """更新仪表盘"""
        if not self.dashboard:
            return
        
        # 统计持仓
        open_positions = [p for p in self.positions.values() if p.status == "OPEN"]
        closed_positions = [p for p in self.positions.values() if p.status == "CLOSED"]
        
        # 计算未实现盈亏
        unrealized_pnl = sum((p.current_price - p.entry_price) * p.shares for p in open_positions)
        
        self.dashboard.update_strategy(
            state=f"监控中 | {len(open_positions)}持仓",
            open_count=len(open_positions),
            closed_count=len(closed_positions),
            total_bet=self.total_bet,
            realized_pnl=self.total_profit,
            unrealized_pnl=unrealized_pnl,
        )
        
        # 更新持仓列表
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
        logger.info("启动扫尾盘策略...")
        logger.info(f"参数: 时间={self.MIN_REMAINING_MIN}-{self.MAX_REMAINING_MIN}分钟, 胜率={self.MIN_WIN_PRICE*100:.0f}%-{self.MAX_WIN_PRICE*100:.0f}%, 止损={self.STOP_LOSS_PRICE}, 金额=${self.BET_AMOUNT}")
        
        if self.dashboard:
            self.dashboard.set_dry_run(self.dry_run)
        
        refresh_interval = 1
        last_refresh = 0
        
        try:
            while self.running:
                # 处理持仓（止损和结算）
                self.process_positions()
                
                # 定期刷新市场列表
                if time.time() - last_refresh > refresh_interval * 60:
                    ending_markets = self.fetch_ending_markets()
                    
                    with self.lock:
                        current_ids = set(self.markets.keys())
                        new_ids = set(m["topic_id"] for m in ending_markets)
                        
                        for topic_id in current_ids - new_ids:
                            if topic_id in self.markets and not self.markets[topic_id].ordered:
                                if self.dashboard:
                                    self.dashboard.remove_market(self.markets[topic_id].title)
                                del self.markets[topic_id]
                        
                        for m in ending_markets:
                            if m["topic_id"] not in self.markets:
                                self.markets[m["topic_id"]] = EndgameMarketState(
                                    topic_id=m["topic_id"],
                                    title=m["title"],
                                    remaining_min=m["remaining_min"],
                                    end_time=m["end_time"],
                                    yes_price=m["yes_price"],
                                    no_price=1 - m["yes_price"] if m["yes_price"] > 0 else 0,
                                    volume=m["volume"],
                                )
                    
                    last_refresh = time.time()
                    logger.info(f"监控 {len(self.markets)} 个市场, {len([p for p in self.positions.values() if p.status == 'OPEN'])} 个持仓")
                
                with self.lock:
                    for state in list(self.markets.values()):
                        try:
                            self.process_market(state)
                        except Exception as e:
                            logger.error(f"处理市场失败: {e}")
                
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
