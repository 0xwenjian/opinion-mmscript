#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
做市刷积分策略模块

核心逻辑：
- 目标是获取积分而不是交易盈利
- 只在安全位置提供流动性吃平台做市补贴
- 刻意避免成交，不承担方向和价格风险
"""

import time
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from threading import Lock
from enum import Enum

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


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass
class OrderBookLevel:
    """订单簿价位"""
    price: float
    size: float
    total: float  # 累计金额


@dataclass
class OrderBook:
    """订单簿"""
    bids: List[OrderBookLevel] = field(default_factory=list)  # 买单 (价格从高到低)
    asks: List[OrderBookLevel] = field(default_factory=list)  # 卖单 (价格从低到高)
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    timestamp: float = 0.0
    
    def get_protection_amount(self, side: str, price: float) -> float:
        """计算目标价位前方的累计挂单金额（保护厚度）"""
        total = 0.0
        if side == "BUY":
            # 买单：计算比目标价格更高的买单总额
            for level in self.bids:
                if level.price > price:
                    total += level.size * level.price
                else:
                    break
        else:
            # 卖单：计算比目标价格更低的卖单总额
            for level in self.asks:
                if level.price < price:
                    total += level.size * level.price
                else:
                    break
        return total


@dataclass
class MakerOrder:
    """挂单记录"""
    order_id: str
    topic_id: int
    title: str
    side: str  # BUY 或 SELL
    outcome: str  # YES 或 NO
    price: float
    amount: float
    shares: float
    create_time: float
    status: OrderStatus = OrderStatus.PENDING
    last_update: float = 0.0
    filled_shares: float = 0.0


@dataclass
class MarketState:
    """市场状态"""
    topic_id: int
    title: str
    yes_price: float = 0.0
    no_price: float = 0.0
    volume: float = 0.0
    order_book: Optional[OrderBook] = None
    last_update: float = 0.0
    
    # 当前挂单
    active_order: Optional[MakerOrder] = None
    last_order_price: float = 0.0
    
    # 缓存的初始价格（从 fetch_markets 获取）
    initial_yes_price: float = 0.0
    
    # 代币 ID（用于获取订单簿）
    yes_token_id: str = ""


class MakerStrategy:
    """
    做市刷积分策略
    
    核心原则：
    1. 只做被动挂单 (maker)，不主动吃单
    2. 在安全位置挂单，确保前方有足够保护
    3. 微幅变动时不撤单，保持挂单时间权重
    4. 监控成交，标记非预期成交事件
    """
    
    # 最小前方保护金额 (USD) - 模拟订单簿时设置较低
    MIN_PROTECTION_AMOUNT = 100.0
    
    # 距离 best bid/ask 的最小距离 (避免被扫)
    MIN_PRICE_DISTANCE = 0.005
    
    # 距离 best bid/ask 的最大距离 (保持竞争力)
    MAX_PRICE_DISTANCE = 0.02
    
    # 价格变化阈值 (小于此值不撤单)
    PRICE_CHANGE_THRESHOLD = 0.003
    
    # 每次挂单金额 (USD)
    ORDER_AMOUNT = 100.0
    
    # 最大同时挂单数
    MAX_ORDERS = 3
    
    # 最小交易量筛选
    MIN_VOLUME = 50000
    
    # 挂单超时时间 (秒) - 超时后检查是否需要调整
    ORDER_CHECK_INTERVAL = 30
    
    # 订单簿刷新间隔 (秒)
    ORDERBOOK_REFRESH_INTERVAL = 5
    
    def __init__(self, fetcher, trader, dashboard=None, dry_run: bool = False, 
                 proxy: Dict = None, config: Dict = None):
        self.fetcher = fetcher
        self.trader = trader
        self.dashboard = dashboard
        self.dry_run = dry_run
        self.proxy = proxy
        
        # 从配置加载参数
        if config:
            maker_cfg = config.get("maker_strategy", {})
            self.MIN_PROTECTION_AMOUNT = maker_cfg.get("min_protection_amount", 500.0)
            self.MIN_PRICE_DISTANCE = maker_cfg.get("min_price_distance", 0.005)
            self.MAX_PRICE_DISTANCE = maker_cfg.get("max_price_distance", 0.02)
            self.PRICE_CHANGE_THRESHOLD = maker_cfg.get("price_change_threshold", 0.003)
            self.ORDER_AMOUNT = maker_cfg.get("order_amount", 50.0)
            self.MAX_ORDERS = maker_cfg.get("max_orders", 3)
            self.MIN_VOLUME = maker_cfg.get("min_volume", 50000)
        
        self.markets: Dict[int, MarketState] = {}
        self.orders: Dict[str, MakerOrder] = {}
        self.lock = Lock()
        self.running = False
        
        # 统计
        self.total_orders = 0
        self.total_order_time = 0.0  # 累计挂单时间 (秒)
        self.unexpected_fills = 0  # 非预期成交次数
        self.total_volume = 0.0  # 累计挂单金额
        self.insufficient_balance = False  # 余额不足标记
    
    def fetch_orderbook(self, topic_id: int, cached_price: float = 0.0, token_id: str = None) -> Optional[OrderBook]:
        """
        获取订单簿
        优先使用 SDK，失败时使用 API
        
        Args:
            topic_id: 市场 ID
            cached_price: 缓存的价格（用于模拟订单簿）
            token_id: 代币 ID（用于 SDK 获取订单簿）
        """
        try:
            # 优先使用 SDK 获取真实订单簿（需要 token_id）
            if token_id and hasattr(self.trader, 'client') and hasattr(self.trader.client, 'get_orderbook'):
                try:
                    ob_result = self.trader.client.get_orderbook(str(token_id))
                    if ob_result and hasattr(ob_result, 'result'):
                        result = ob_result.result
                        if hasattr(result, 'data'):
                            data = result.data
                        else:
                            data = result
                        
                        # 解析 SDK 返回的订单簿
                        bids = []
                        asks = []
                        
                        bid_list = getattr(data, 'bids', []) or []
                        ask_list = getattr(data, 'asks', []) or []
                        
                        for bid in bid_list:
                            price = float(getattr(bid, 'price', 0) or 0)
                            size = float(getattr(bid, 'size', 0) or getattr(bid, 'amount', 0) or 0)
                            if price > 0:
                                bids.append(OrderBookLevel(price=price, size=size, total=price * size))
                        
                        for ask in ask_list:
                            price = float(getattr(ask, 'price', 0) or 0)
                            size = float(getattr(ask, 'size', 0) or getattr(ask, 'amount', 0) or 0)
                            if price > 0:
                                asks.append(OrderBookLevel(price=price, size=size, total=price * size))
                        
                        # 按价格排序
                        bids.sort(key=lambda x: x.price, reverse=True)
                        asks.sort(key=lambda x: x.price)
                        
                        best_bid = bids[0].price if bids else 0.0
                        best_ask = asks[0].price if asks else 1.0
                        
                        if best_bid > 0:
                            logger.debug(f"SDK 订单簿: 市场 {topic_id} best_bid={best_bid:.4f} best_ask={best_ask:.4f} bids={len(bids)} asks={len(asks)}")
                            return OrderBook(
                                bids=bids,
                                asks=asks,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                spread=best_ask - best_bid,
                                timestamp=time.time(),
                            )
                except Exception as e:
                    logger.debug(f"SDK 获取订单簿失败: {e}")
            
            # 尝试使用 fetcher 的订单簿 API
            ob_data = self.fetcher.fetch_orderbook(topic_id)
            
            if ob_data:
                # 解析订单簿
                bids = []
                asks = []
                
                for bid in ob_data.get("bids", []):
                    price = float(bid.get("price", 0))
                    size = float(bid.get("size", 0))
                    bids.append(OrderBookLevel(
                        price=price,
                        size=size,
                        total=price * size,
                    ))
                
                for ask in ob_data.get("asks", []):
                    price = float(ask.get("price", 0))
                    size = float(ask.get("size", 0))
                    asks.append(OrderBookLevel(
                        price=price,
                        size=size,
                        total=price * size,
                    ))
                
                best_bid = ob_data.get("best_bid", 0.0)
                best_ask = ob_data.get("best_ask", 1.0)
                
                if best_bid > 0:
                    return OrderBook(
                        bids=bids,
                        asks=asks,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        spread=best_ask - best_bid,
                        timestamp=time.time(),
                    )
            
            # API 不可用，不使用模拟数据
            logger.debug(f"无法获取市场 {topic_id} 的真实订单簿")
            return None
            
        except Exception as e:
            logger.debug(f"获取订单簿异常: {e}")
            return None
    
    def _simulate_orderbook(self, topic_id: int, cached_price: float = 0.0) -> Optional[OrderBook]:
        """
        模拟订单簿（当 API 不可用时）
        基于市场价格生成合理的订单簿
        
        Args:
            topic_id: 市场 ID
            cached_price: 缓存的价格（优先使用）
        """
        try:
            yes_price = cached_price
            
            # 如果没有缓存价格，尝试从 API 获取
            if yes_price <= 0:
                market = self.fetcher.fetch_market_by_id(topic_id)
                if market:
                    yes_price = market.get("yesBuyPrice", 0) or market.get("yesPrice", 0)
            
            if yes_price <= 0:
                logger.debug(f"模拟订单簿失败: 市场 {topic_id} 价格无效 {yes_price}")
                return None
            
            logger.debug(f"模拟订单簿: 市场 {topic_id} 价格 {yes_price}")
            
            # 模拟订单簿：假设有一定深度
            spread = 0.01
            best_bid = yes_price - spread / 2
            best_ask = yes_price + spread / 2
            
            # 生成模拟的买卖盘
            bids = []
            asks = []
            
            for i in range(5):
                bid_price = best_bid - i * 0.005
                ask_price = best_ask + i * 0.005
                
                # 假设每档有 100-500 USD 的挂单
                bid_size = 200 + i * 100
                ask_size = 200 + i * 100
                
                bids.append(OrderBookLevel(price=bid_price, size=bid_size/bid_price, total=bid_size))
                asks.append(OrderBookLevel(price=ask_price, size=ask_size/ask_price, total=ask_size))
            
            return OrderBook(
                bids=bids,
                asks=asks,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                timestamp=time.time(),
            )
            
        except Exception as e:
            logger.error(f"模拟订单簿失败: {e}")
            return None
    
    def fetch_high_volume_markets(self) -> List[Dict]:
        """获取高流动性二元市场（过滤多选市场）"""
        try:
            markets = self.fetcher.fetch_markets(limit=50, fetch_all=True)
            filtered = []
            
            for m in markets:
                # 只要二元市场，过滤掉多选市场
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
                
                # 筛选价格在合理范围内的市场 (避免极端价格)
                if yes_price < 0.1 or yes_price > 0.9:
                    continue
                
                # 通过 SDK 获取真正的 yes_token_id
                yes_token_id = ""
                try:
                    if hasattr(self.trader, 'client'):
                        market_info = self.trader.get_market_by_topic_id(topic_id)
                        if market_info:
                            yes_token_id = str(market_info.get("yes_token_id", ""))
                            logger.debug(f"市场 {topic_id} yes_token_id: {yes_token_id[:20]}...")
                except Exception as e:
                    logger.debug(f"获取市场 {topic_id} token_id 失败: {e}")
                
                if not yes_token_id:
                    logger.debug(f"市场 {topic_id} 无法获取 yes_token_id，跳过")
                    continue
                
                filtered.append({
                    "topic_id": topic_id,
                    "title": m.get("title", ""),
                    "yes_price": yes_price,
                    "volume": volume,
                    "yes_token_id": yes_token_id,
                })
            
            # 按交易量排序
            filtered.sort(key=lambda x: x["volume"], reverse=True)
            
            logger.info(f"找到 {len(filtered)} 个高流动性二元市场（已过滤多选市场）")
            return filtered[:10]  # 只取前10个
            
        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return []
    
    def calculate_safe_price(self, order_book: OrderBook, side: str) -> Optional[float]:
        """
        计算安全挂单价格
        
        原则：
        1. 在 best bid/ask 附近，但保持一定距离避免被扫
        2. 确保前方有足够的保护金额
        3. 不要挂在太远的位置失去竞争力
        """
        if not order_book or order_book.best_bid <= 0:
            return None
        
        if side == "BUY":
            # 买单：在 best_bid 下方找安全位置
            base_price = order_book.best_bid
            
            # 从 best_bid 开始，逐步降低价格直到找到安全位置
            for offset in [0.005, 0.008, 0.01, 0.012, 0.015, 0.02]:
                target_price = base_price - offset
                protection = order_book.get_protection_amount("BUY", target_price)
                
                if protection >= self.MIN_PROTECTION_AMOUNT:
                    # 确保不超过最大距离
                    if offset <= self.MAX_PRICE_DISTANCE:
                        return round(target_price, 4)
            
            # 如果找不到安全位置，使用最大距离
            return round(base_price - self.MAX_PRICE_DISTANCE, 4)
        
        else:
            # 卖单：在 best_ask 上方找安全位置
            base_price = order_book.best_ask
            
            for offset in [0.005, 0.008, 0.01, 0.012, 0.015, 0.02]:
                target_price = base_price + offset
                protection = order_book.get_protection_amount("SELL", target_price)
                
                if protection >= self.MIN_PROTECTION_AMOUNT:
                    if offset <= self.MAX_PRICE_DISTANCE:
                        return round(target_price, 4)
            
            return round(base_price + self.MAX_PRICE_DISTANCE, 4)
    
    def should_adjust_order(self, order: MakerOrder, new_price: float) -> bool:
        """
        判断是否需要调整挂单
        
        微幅变动过滤：价格变化小于阈值时不撤单
        """
        if order.status != OrderStatus.OPEN:
            return False
        
        price_change = abs(new_price - order.price) / order.price
        
        if price_change < self.PRICE_CHANGE_THRESHOLD:
            logger.debug(f"价格变化 {price_change:.4f} < 阈值 {self.PRICE_CHANGE_THRESHOLD}，保持挂单")
            return False
        
        return True
    
    def place_maker_order(self, state: MarketState, side: str, price: float) -> Optional[MakerOrder]:
        """
        下被动挂单
        """
        outcome = "YES"  # 默认做 YES 方向
        
        logger.info(f"[挂单] {state.title[:30]} {side} {outcome} @ {price:.4f} ${self.ORDER_AMOUNT}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际下单")
            order_id = f"dry_{int(time.time())}"
            success = True
        else:
            result = self.trader.place_order(
                topic_id=state.topic_id,
                outcome=outcome,
                amount=self.ORDER_AMOUNT,
                price=price,
                order_type=2,  # 限价单
                side=side,
            )
            
            # 检查是否余额不足
            if result == "INSUFFICIENT_BALANCE":
                logger.warning(f"余额不足，停止下单，进入监控模式")
                self.insufficient_balance = True
                return None
            
            success = result is not None and result != "INSUFFICIENT_BALANCE"
            order_id = None
            
            if result and result != "INSUFFICIENT_BALANCE":
                # 从 result.result.order_data.order_id 提取
                if hasattr(result, 'result') and result.result:
                    res = result.result
                    if hasattr(res, 'order_data') and res.order_data:
                        order_id = str(getattr(res.order_data, 'order_id', ''))
                    elif hasattr(res, 'order_id'):
                        order_id = str(res.order_id)
                # 直接从 result 提取
                if not order_id and hasattr(result, 'order_id'):
                    order_id = str(result.order_id)
            
            if not order_id:
                logger.warning(f"无法获取订单ID，跳过")
                return None
        
        if success and order_id:
            shares = self.ORDER_AMOUNT / price
            
            order = MakerOrder(
                order_id=order_id,
                topic_id=state.topic_id,
                title=state.title,
                side=side,
                outcome=outcome,
                price=price,
                amount=self.ORDER_AMOUNT,
                shares=shares,
                create_time=time.time(),
                status=OrderStatus.OPEN,
                last_update=time.time(),
            )
            
            self.orders[order_id] = order
            state.active_order = order
            state.last_order_price = price
            
            self.total_orders += 1
            self.total_volume += self.ORDER_AMOUNT
            
            if self.dashboard:
                self.dashboard.add_trade(
                    market=state.title,
                    direction=side,
                    side=outcome,
                    price=price,
                    shares=shares,
                    status="挂单",
                )
            
            return order
        
        return None
    
    def cancel_order(self, order: MakerOrder) -> bool:
        """撤销挂单"""
        logger.info(f"[撤单] {order.title[:30]} {order.side} @ {order.price:.4f}")
        
        if self.dry_run:
            logger.info(f"[测试模式] 跳过实际撤单")
            success = True
        else:
            success = self.trader.cancel_order(order.order_id)
        
        if success:
            # 计算挂单时间
            order_duration = time.time() - order.create_time
            self.total_order_time += order_duration
            
            order.status = OrderStatus.CANCELLED
            order.last_update = time.time()
            
            logger.info(f"  挂单时长: {order_duration:.1f}秒")
        
        return success
    
    def check_order_status(self, order: MakerOrder) -> bool:
        """
        检查订单状态，监测是否被成交
        返回 True 表示订单仍然有效
        """
        if self.dry_run:
            return True
        
        try:
            # 检查订单是否被成交
            is_filled = self.trader.is_order_filled(order.order_id)
            
            if is_filled:
                # 非预期成交！
                self.unexpected_fills += 1
                order.status = OrderStatus.FILLED
                order.last_update = time.time()
                
                logger.warning(f"[非预期成交] {order.title[:30]} {order.side} @ {order.price:.4f}")
                
                # 发送通知
                msg = f"""⚠️ <b>非预期成交</b>
━━━━━━━━━━━━━━━
📌 市场: {order.title[:40]}
📊 方向: {order.side} {order.outcome}
💰 价格: <code>{order.price:.4f}</code>
📦 数量: <code>{order.shares:.2f}</code>
⏰ 挂单时长: <code>{time.time() - order.create_time:.0f}秒</code>
━━━━━━━━━━━━━━━
请检查市场状况！"""
                send_tg_notification(msg, self.proxy)
                
                if self.dashboard:
                    self.dashboard.add_trade(
                        market=order.title,
                        direction="FILL",
                        side=order.outcome,
                        price=order.price,
                        shares=order.shares,
                        status="成交",
                    )
                
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"检查订单状态失败: {e}")
            return True
    
    def process_market(self, state: MarketState):
        """处理单个市场"""
        # 获取订单簿，传入缓存的价格和 token_id
        cached_price = state.initial_yes_price or state.yes_price
        order_book = self.fetch_orderbook(state.topic_id, cached_price, state.yes_token_id)
        if not order_book:
            return
        
        state.order_book = order_book
        state.yes_price = order_book.best_bid
        state.no_price = 1 - order_book.best_ask
        state.last_update = time.time()
        
        # 更新仪表盘
        if self.dashboard:
            self.dashboard.update_market(
                topic_id=state.topic_id,
                name=state.title,
                yes_price=state.yes_price,
                no_price=state.no_price,
                remaining_min=0,
                volume=state.volume,
            )
        
        # 检查现有挂单
        if state.active_order and state.active_order.status == OrderStatus.OPEN:
            order = state.active_order
            
            # 检查订单是否被成交
            if not self.check_order_status(order):
                state.active_order = None
                return
            
            # 计算新的安全价格
            new_price = self.calculate_safe_price(order_book, order.side)
            if not new_price:
                return
            
            # 判断是否需要调整
            if self.should_adjust_order(order, new_price):
                logger.info(f"盘口变化，调整挂单: {order.price:.4f} -> {new_price:.4f}")
                
                # 撤销旧单
                if self.cancel_order(order):
                    state.active_order = None
                    
                    # 下新单
                    self.place_maker_order(state, order.side, new_price)
        
        else:
            # 没有活跃挂单，尝试下新单
            # 如果余额不足，跳过下单
            if self.insufficient_balance:
                return
            
            active_count = sum(1 for o in self.orders.values() if o.status == OrderStatus.OPEN)
            
            if active_count < self.MAX_ORDERS:
                # 计算安全价格
                buy_price = self.calculate_safe_price(order_book, "BUY")
                
                if buy_price:
                    protection = order_book.get_protection_amount("BUY", buy_price)
                    logger.info(f"[{state.title[:20]}] 目标价位 {buy_price:.4f} 前方保护: ${protection:.0f}")
                    
                    if protection >= self.MIN_PROTECTION_AMOUNT:
                        self.place_maker_order(state, "BUY", buy_price)
                    else:
                        logger.debug(f"[{state.title[:20]}] 保护不足，跳过: ${protection:.0f} < ${self.MIN_PROTECTION_AMOUNT}")
                else:
                    logger.debug(f"[{state.title[:20]}] 无法计算安全价格")
    
    def update_dashboard(self):
        """更新仪表盘"""
        if not self.dashboard:
            return
        
        active_orders = [o for o in self.orders.values() if o.status == OrderStatus.OPEN]
        
        # 计算平均挂单时间
        avg_order_time = self.total_order_time / max(1, self.total_orders)
        
        self.dashboard.update_strategy(
            state=f"做市中 | {len(active_orders)}挂单",
            open_count=len(active_orders),
            closed_count=self.unexpected_fills,
            total_bet=self.total_volume,
            realized_pnl=0.0,  # 做市策略不追求盈利
            unrealized_pnl=0.0,
        )
        
        self.dashboard.update_account(
            address=self.dashboard.state.wallet_address,
            balance=self.dashboard.state.usdc_balance,
            orders=len(active_orders),
            pnl=0.0,
        )
    
    def run(self):
        """运行策略"""
        self.running = True
        logger.info("启动做市刷积分策略...")
        logger.info(f"参数: 保护=${self.MIN_PROTECTION_AMOUNT} 距离={self.MIN_PRICE_DISTANCE}-{self.MAX_PRICE_DISTANCE} 阈值={self.PRICE_CHANGE_THRESHOLD} 金额=${self.ORDER_AMOUNT}")
        
        if self.dashboard:
            self.dashboard.set_dry_run(self.dry_run)
        
        market_refresh_interval = 300  # 5分钟刷新市场列表
        last_market_refresh = 0
        
        try:
            while self.running:
                # 定期刷新市场列表
                if time.time() - last_market_refresh > market_refresh_interval:
                    markets = self.fetch_high_volume_markets()
                    
                    with self.lock:
                        # 更新市场列表
                        new_ids = set(m["topic_id"] for m in markets)
                        
                        # 移除不再监控的市场（但保留有活跃挂单的）
                        for topic_id in list(self.markets.keys()):
                            if topic_id not in new_ids:
                                state = self.markets[topic_id]
                                if not state.active_order or state.active_order.status != OrderStatus.OPEN:
                                    del self.markets[topic_id]
                        
                        # 添加新市场
                        for m in markets:
                            if m["topic_id"] not in self.markets:
                                self.markets[m["topic_id"]] = MarketState(
                                    topic_id=m["topic_id"],
                                    title=m["title"],
                                    yes_price=m["yes_price"],
                                    volume=m["volume"],
                                    initial_yes_price=m["yes_price"],
                                    yes_token_id=m.get("yes_token_id", ""),
                                )
                    
                    last_market_refresh = time.time()
                    logger.info(f"监控 {len(self.markets)} 个市场")
                
                # 处理每个市场
                with self.lock:
                    for state in list(self.markets.values()):
                        try:
                            self.process_market(state)
                        except Exception as e:
                            logger.error(f"处理市场失败: {e}")
                
                # 更新仪表盘
                self.update_dashboard()
                
                if self.dashboard:
                    self.dashboard.update_system_status(api=True, ws=False, proxy=self.proxy is not None)
                
                # 打印统计
                active_count = sum(1 for o in self.orders.values() if o.status == OrderStatus.OPEN)
                avg_time = self.total_order_time / max(1, self.total_orders)
                mode = "监控模式(余额不足)" if self.insufficient_balance else "做市中"
                logger.info(f"统计: {mode} | 挂单={active_count} 总单={self.total_orders} 平均时长={avg_time:.0f}秒 非预期成交={self.unexpected_fills}")
                
                time.sleep(self.ORDERBOOK_REFRESH_INTERVAL)
        
        except KeyboardInterrupt:
            logger.info("策略停止")
        finally:
            # 撤销所有挂单
            logger.info("撤销所有挂单...")
            for order in self.orders.values():
                if order.status == OrderStatus.OPEN:
                    self.cancel_order(order)
            
            self.running = False
    
    def stop(self):
        """停止策略"""
        self.running = False
