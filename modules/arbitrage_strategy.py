#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二元市场套利策略模块
"""

import time
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque
from threading import Thread, Lock

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
class PriceHistory:
    prices: deque = field(default_factory=lambda: deque(maxlen=120))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=120))
    
    def add(self, price: float, ts: float = None):
        self.prices.append(price)
        self.timestamps.append(ts or time.time())
    
    def get_drop_percent(self, window_sec: float = 30) -> float:
        if len(self.prices) < 2:
            return 0.0
        now = time.time()
        max_price = 0.0
        current_price = self.prices[-1]
        for p, ts in zip(self.prices, self.timestamps):
            if now - ts <= window_sec:
                max_price = max(max_price, p)
        if max_price <= 0:
            return 0.0
        return (max_price - current_price) / max_price


@dataclass
class MarketState:
    topic_id: int
    title: str
    yes_price: float = 0.0
    no_price: float = 0.0
    remaining_min: float = 0.0
    
    high_price_start: float = 0.0
    high_price_side: str = ""
    
    yes_history: PriceHistory = field(default_factory=PriceHistory)
    no_history: PriceHistory = field(default_factory=PriceHistory)
    
    leg1_done: bool = False
    leg1_side: str = ""
    leg1_price: float = 0.0
    leg1_shares: float = 0.0
    leg1_time: float = 0.0
    
    leg2_done: bool = False
    leg2_side: str = ""
    leg2_price: float = 0.0
    leg2_shares: float = 0.0
    
    def price_sum(self) -> float:
        return self.yes_price + self.no_price
    
    def get_countdown_sec(self) -> float:
        if self.high_price_start > 0:
            return time.time() - self.high_price_start
        return -1
    
    def get_leg2_wait_sec(self) -> float:
        if self.leg1_done and not self.leg2_done and self.leg1_time > 0:
            return time.time() - self.leg1_time
        return -1


class ArbitrageStrategy:
    """二元市场套利策略"""
    
    PRICE_SUM_THRESHOLD = 0.95
    HIGH_PRICE_MIN = 0.56
    HIGH_PRICE_MAX = 0.65
    HIGH_PRICE_COUNTDOWN = 60
    DROP_THRESHOLD = 0.08
    DROP_WINDOW = 30
    
    STOP_LOSS_PRICE = 0.35
    LEG2_TIMEOUT = 180
    
    MIN_REMAINING_MIN = 10
    MAX_REMAINING_MIN = 600
    
    SHARES_PER_LEG = 20
    
    def __init__(self, fetcher, trader, dashboard=None, dry_run: bool = False, proxy: Dict = None):
        self.fetcher = fetcher
        self.trader = trader
        self.dashboard = dashboard
        self.dry_run = dry_run
        self.proxy = proxy
        
        self.markets: Dict[int, MarketState] = {}
        self.lock = Lock()
        self.running = False
        
        self.total_trades = 0
        self.total_profit = 0.0
    
    def fetch_binary_markets(self) -> List[Dict]:
        try:
            markets = self.fetcher.fetch_markets(limit=50, fetch_all=True)
            binary_markets = []
            
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
                
                end_time_str = m.get("endTime", "")
                remaining_min = 0
                if end_time_str:
                    try:
                        end_ts = datetime.fromisoformat(end_time_str.replace("Z", "+00:00")).timestamp()
                        remaining_min = (end_ts - time.time()) / 60
                    except:
                        pass
                
                if remaining_min < self.MIN_REMAINING_MIN or remaining_min > self.MAX_REMAINING_MIN:
                    continue
                
                yes_price = float(m.get("yesPrice", 0) or 0)
                
                binary_markets.append({
                    "topic_id": topic_id,
                    "title": title,
                    "remaining_min": remaining_min,
                    "yes_price": yes_price,
                })
            
            logger.info(f"找到 {len(binary_markets)} 个二元市场")
            return binary_markets
        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return []
    
    def fetch_market_prices(self, topic_id: int) -> Tuple[float, float]:
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
    
    def check_price_sum_trigger(self, state: MarketState) -> bool:
        return state.price_sum() <= self.PRICE_SUM_THRESHOLD and state.price_sum() > 0
    
    def check_high_price_countdown_trigger(self, state: MarketState) -> Optional[str]:
        yes_in_range = self.HIGH_PRICE_MIN <= state.yes_price <= self.HIGH_PRICE_MAX
        no_in_range = self.HIGH_PRICE_MIN <= state.no_price <= self.HIGH_PRICE_MAX
        
        current_high_side = ""
        if yes_in_range and not no_in_range:
            current_high_side = "YES"
        elif no_in_range and not yes_in_range:
            current_high_side = "NO"
        
        if current_high_side:
            if state.high_price_side != current_high_side:
                state.high_price_start = time.time()
                state.high_price_side = current_high_side
            elif state.get_countdown_sec() >= self.HIGH_PRICE_COUNTDOWN:
                return "NO" if current_high_side == "YES" else "YES"
        else:
            state.high_price_start = 0
            state.high_price_side = ""
        
        return None
    
    def check_drop_trigger(self, state: MarketState) -> Optional[str]:
        yes_drop = state.yes_history.get_drop_percent(self.DROP_WINDOW)
        no_drop = state.no_history.get_drop_percent(self.DROP_WINDOW)
        
        if yes_drop >= self.DROP_THRESHOLD:
            return "YES"
        if no_drop >= self.DROP_THRESHOLD:
            return "NO"
        
        return None
    
    def check_stop_loss(self, state: MarketState) -> bool:
        if not state.leg1_done or state.leg2_done:
            return False
        
        if state.leg1_side == "YES":
            return state.yes_price < self.STOP_LOSS_PRICE
        else:
            return state.no_price < self.STOP_LOSS_PRICE
    
    def check_leg2_timeout(self, state: MarketState) -> bool:
        if not state.leg1_done or state.leg2_done:
            return False
        return state.get_leg2_wait_sec() >= self.LEG2_TIMEOUT
    
    def execute_buy(self, state: MarketState, side: str, is_leg2: bool = False) -> bool:
        price = state.yes_price if side == "YES" else state.no_price
        
        leg_name = "第二腿" if is_leg2 else "第一腿"
        logger.info(f"[{leg_name}] 买入 {state.title[:30]} {side} @ {price:.4f} x {self.SHARES_PER_LEG}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=state.topic_id,
                outcome=side,
                amount=self.SHARES_PER_LEG * price,
                price=price,
                order_type=2,
                side="BUY",
            )
            success = result is not None
        
        if success:
            if is_leg2:
                state.leg2_done = True
                state.leg2_side = side
                state.leg2_price = price
                state.leg2_shares = self.SHARES_PER_LEG
            else:
                state.leg1_done = True
                state.leg1_side = side
                state.leg1_price = price
                state.leg1_shares = self.SHARES_PER_LEG
                state.leg1_time = time.time()
            
            self.total_trades += 1
            
            emoji = "🟢" if side == "YES" else "🔴"
            msg = f"""{emoji} <b>{leg_name}买入</b>
━━━━━━━━━━━━━━━
📌 市场: {state.title[:40]}
📊 方向: <b>{side}</b>
💰 价格: <code>{price:.4f}</code>
📦 数量: <code>{self.SHARES_PER_LEG}</code>
━━━━━━━━━━━━━━━"""
            send_tg_notification(msg, self.proxy)
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=state.title,
                    direction="BUY",
                    side=side,
                    price=price,
                    shares=self.SHARES_PER_LEG,
                    status="成功" if not self.dry_run else "测试",
                )
        
        return success
    
    def execute_sell(self, state: MarketState, side: str, shares: float, reason: str) -> bool:
        price = state.yes_price if side == "YES" else state.no_price
        
        logger.info(f"[{reason}] 卖出 {state.title[:30]} {side} @ {price:.4f} x {shares}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            success = True
        else:
            result = self.trader.place_order(
                topic_id=state.topic_id,
                outcome=side,
                amount=shares,
                price=price,
                order_type=2,
                side="SELL",
            )
            success = result is not None
        
        if success:
            loss = (state.leg1_price - price) * shares
            self.total_profit -= loss
            
            state.leg1_done = False
            state.leg1_side = ""
            state.leg1_price = 0.0
            state.leg1_shares = 0.0
            state.leg1_time = 0.0
            
            msg = f"""⚠️ <b>{reason}</b>
━━━━━━━━━━━━━━━
📌 市场: {state.title[:40]}
📊 方向: <b>{side}</b>
💰 价格: <code>{price:.4f}</code>
📉 亏损: <code>${loss:.2f}</code>
━━━━━━━━━━━━━━━"""
            send_tg_notification(msg, self.proxy)
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=state.title,
                    direction="SELL",
                    side=side,
                    price=price,
                    shares=shares,
                    status="止损" if "止损" in reason else "超时",
                )
        
        return success
    
    def process_market(self, state: MarketState):
        yes_price, no_price = self.fetch_market_prices(state.topic_id)
        if yes_price <= 0:
            return
        
        state.yes_price = yes_price
        state.no_price = no_price
        state.yes_history.add(yes_price)
        state.no_history.add(no_price)
        
        if self.dashboard:
            self.dashboard.update_market(
                name=state.title,
                up_price=yes_price,
                down_price=no_price,
                remaining_min=state.remaining_min,
                countdown_sec=state.get_countdown_sec(),
                countdown_direction=state.high_price_side,
                leg2_wait_sec=state.get_leg2_wait_sec(),
            )
        
        if state.leg1_done and not state.leg2_done:
            if self.check_stop_loss(state):
                logger.warning(f"触发止损: {state.title[:30]}")
                self.execute_sell(state, state.leg1_side, state.leg1_shares, "止损卖出")
                return
            
            if self.check_leg2_timeout(state):
                logger.warning(f"第二腿超时: {state.title[:30]}")
                self.execute_sell(state, state.leg1_side, state.leg1_shares, "超时卖出")
                return
        
        if state.leg1_done and state.leg2_done:
            profit = 1.0 - state.leg1_price - state.leg2_price
            self.total_profit += profit * self.SHARES_PER_LEG
            
            logger.success(f"套利完成: {state.title[:30]} 利润={profit:.4f}/份")
            
            msg = f"""✅ <b>套利完成</b>
━━━━━━━━━━━━━━━
📌 市场: {state.title[:40]}
🟢 YES: <code>{state.leg1_price if state.leg1_side == 'YES' else state.leg2_price:.4f}</code>
🔴 NO: <code>{state.leg1_price if state.leg1_side == 'NO' else state.leg2_price:.4f}</code>
💰 利润: <code>${profit * self.SHARES_PER_LEG:.2f}</code>
━━━━━━━━━━━━━━━"""
            send_tg_notification(msg, self.proxy)
            
            if self.dashboard:
                self.dashboard.update_account(
                    address=self.dashboard.state.wallet_address,
                    balance=self.dashboard.state.usdc_balance,
                    orders=self.dashboard.state.active_orders,
                    pnl=self.total_profit,
                )
            
            state.leg1_done = False
            state.leg2_done = False
            state.leg1_side = ""
            state.leg2_side = ""
            state.leg1_price = 0.0
            state.leg2_price = 0.0
            state.leg1_shares = 0.0
            state.leg2_shares = 0.0
            state.leg1_time = 0.0
            return
        
        if not state.leg1_done:
            if self.check_price_sum_trigger(state):
                logger.info(f"价格和触发: {state.title[:30]} sum={state.price_sum():.4f}")
                if self.execute_buy(state, "YES", is_leg2=False):
                    self.execute_buy(state, "NO", is_leg2=True)
                return
            
            low_side = self.check_high_price_countdown_trigger(state)
            if low_side:
                logger.info(f"高价倒计时触发: {state.title[:30]} 买入 {low_side}")
                self.execute_buy(state, low_side, is_leg2=False)
                return
            
            drop_side = self.check_drop_trigger(state)
            if drop_side:
                logger.info(f"暴跌触发: {state.title[:30]} 买入 {drop_side}")
                self.execute_buy(state, drop_side, is_leg2=False)
                return
        
        elif not state.leg2_done:
            other_side = "NO" if state.leg1_side == "YES" else "YES"
            other_price = state.no_price if state.leg1_side == "YES" else state.yes_price
            
            if state.leg1_price + other_price <= self.PRICE_SUM_THRESHOLD:
                logger.info(f"第二腿触发: {state.title[:30]} 买入 {other_side}")
                self.execute_buy(state, other_side, is_leg2=True)
    
    def update_dashboard_status(self):
        if not self.dashboard:
            return
        
        leg1_count = sum(1 for s in self.markets.values() if s.leg1_done and not s.leg2_done)
        leg2_count = sum(1 for s in self.markets.values() if s.leg1_done and s.leg2_done)
        
        if leg2_count > 0:
            strategy_state = "双腿完成"
        elif leg1_count > 0:
            strategy_state = "第一腿完成"
        else:
            strategy_state = "监控中"
        
        leg1_price = 0.0
        leg2_price = 0.0
        for s in self.markets.values():
            if s.leg1_done:
                leg1_price = s.leg1_price
                if s.leg2_done:
                    leg2_price = s.leg2_price
                break
        
        self.dashboard.update_strategy(
            state=strategy_state,
            leg1=leg1_count > 0 or leg2_count > 0,
            leg2=leg2_count > 0,
            leg1_price=leg1_price,
            leg2_price=leg2_price,
        )
        
        self.dashboard.update_account(
            address=self.dashboard.state.wallet_address,
            balance=self.dashboard.state.usdc_balance,
            orders=self.dashboard.state.active_orders,
            pnl=self.total_profit,
        )
    
    def run(self):
        self.running = True
        logger.info("启动二元市场套利策略...")
        logger.info(f"参数: 每腿{self.SHARES_PER_LEG}份, 价格和阈值{self.PRICE_SUM_THRESHOLD}, 止损{self.STOP_LOSS_PRICE}")
        
        if self.dashboard:
            self.dashboard.set_dry_run(self.dry_run)
        
        refresh_interval = 5
        last_refresh = 0
        
        try:
            while self.running:
                if time.time() - last_refresh > refresh_interval * 60:
                    binary_markets = self.fetch_binary_markets()
                    
                    with self.lock:
                        current_ids = set(self.markets.keys())
                        new_ids = set(m["topic_id"] for m in binary_markets)
                        
                        for topic_id in current_ids - new_ids:
                            if topic_id in self.markets and not self.markets[topic_id].leg1_done:
                                if self.dashboard:
                                    self.dashboard.remove_market(self.markets[topic_id].title)
                                del self.markets[topic_id]
                        
                        for m in binary_markets:
                            if m["topic_id"] not in self.markets:
                                self.markets[m["topic_id"]] = MarketState(
                                    topic_id=m["topic_id"],
                                    title=m["title"],
                                    remaining_min=m["remaining_min"],
                                    yes_price=m["yes_price"],
                                    no_price=1 - m["yes_price"] if m["yes_price"] > 0 else 0,
                                )
                    
                    last_refresh = time.time()
                    logger.info(f"监控 {len(self.markets)} 个市场")
                
                with self.lock:
                    for state in list(self.markets.values()):
                        try:
                            self.process_market(state)
                        except Exception as e:
                            logger.error(f"处理市场失败: {e}")
                
                self.update_dashboard_status()
                
                if self.dashboard:
                    self.dashboard.update_system_status(api=True, ws=False, proxy=self.proxy is not None)
                
                time.sleep(2)
        
        except KeyboardInterrupt:
            logger.info("策略停止")
        finally:
            self.running = False
    
    def stop(self):
        self.running = False
