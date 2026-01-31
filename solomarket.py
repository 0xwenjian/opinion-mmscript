#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Solo Market 监控脚本

功能：
- 监控指定的二元市场
- 在 YES 方向挂单
- 基于订单簿保护金额调整订单
- 保护金额足够时保持挂单不动
"""

import os
import socket
import sys
import time
import yaml
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass
from loguru import logger
from dotenv import load_dotenv

# 导入现有模块
from modules.fetch_opinion import OpinionFetcher
from modules.trader_opinion_sdk import OpinionTraderSDK

# Telegram 通知配置（从 config.yaml 加载）
TG_BOT_TOKEN = ""
TG_CHAT_ID = ""


def send_tg_notification(message: str, proxy: Dict = None):
    """发送 Telegram 通知"""
    if not TG_CHAT_ID or not TG_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, data=data, timeout=10, proxies=proxy)
    except Exception as e:
        logger.warning(f"TG通知失败: {e}")


class MockFetcher:
    """模拟市场信息抓取层"""
    def __init__(self, parent=None):
        self.parent = parent
        self.mock_ob = OrderBook(
            bids=[
                OrderBookLevel(0.80, 1000, 800),
                OrderBookLevel(0.79, 1000, 790),
                OrderBookLevel(0.78, 1000, 780),
                OrderBookLevel(0.77, 1000, 770),
                OrderBookLevel(0.76, 1000, 760),
                OrderBookLevel(0.75, 1000, 750),
                OrderBookLevel(0.74, 1000, 740),
                OrderBookLevel(0.73, 1000, 730),
                OrderBookLevel(0.72, 1000, 720),
                OrderBookLevel(0.71, 1000, 710),
            ],
            asks=[OrderBookLevel(0.81, 1000, 810)],
            best_bid=0.80,
            best_ask=0.81
        )

    def set_mock_bid(self, index: int, price: float, size: float):
        if index < len(self.mock_ob.bids):
            self.mock_ob.bids[index].price = price
            self.mock_ob.bids[index].size = size
            self.mock_ob.bids[index].total = price * size
            self.mock_ob.bids.sort(key=lambda x: x.price, reverse=True)
            self.mock_ob.best_bid = self.mock_ob.bids[0].price

    def shift_book(self, offset: float):
        """整体平移盘口"""
        for level in self.mock_ob.bids:
            level.price = round(level.price + offset, 4)
            level.total = level.price * level.size
        for level in self.mock_ob.asks:
            level.price = round(level.price + offset, 4)
            level.total = level.price * level.size
        self.mock_ob.best_bid = self.mock_ob.bids[0].price
        self.mock_ob.best_ask = self.mock_ob.asks[0].price


class MockTrader:
    """模拟交易执行层"""
    def __init__(self):
        self.mock_fetcher = None
        self.orders = {}
        self.counter = 1000
    
    class MockClient:
        def __init__(self, fetcher): self.fetcher = fetcher
        def get_orderbook(self, token_id):
            class Res: pass
            res = Res(); res.result = self.fetcher.mock_ob
            return res

    def set_fetcher(self, fetcher):
        self.mock_fetcher = fetcher
        self.client = self.MockClient(fetcher)

    def get_market_by_topic_id(self, topic_id):
        return {
            "title": f"Mock Market {topic_id}",
            "yes_token_id": "mock_yes",
            "no_token_id": "mock_no"
        }

    def place_order(self, **kwargs):
        self.counter += 1
        order_id = f"mock_order_{self.counter}"
        logger.debug(f"[MockTrader] 下单: {kwargs['price']} {kwargs['outcome']}")
        return type('Obj', (object,), {'order_id': order_id, 'result': None})

    def cancel_order(self, order_id):
        logger.debug(f"[MockTrader] 撤单: {order_id}")
        return True

    def check_order_status(self, order_id):
        # 模拟模式下，订单永远是活跃的（不会被成交）
        return {"status": "open"}


@dataclass
class OrderBookLevel:
    """订单簿价位"""
    price: float
    size: float
    total: float


@dataclass
class OrderBook:
    """订单簿"""
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    best_bid: float = 0.0
    best_ask: float = 0.0
    
    def get_protection_amount(self, side: str, price: float, order_amount: float = 0.0) -> float:
        """计算目标价位前方的累计挂单金额（保护厚度）
        
        逻辑:
        - 包含所有优于目标价格的挂单
        - 包含同一价格下优先于我们的挂单 (通过减去我们自己的金额来估算)
        """
        total = 0.0
        if side == "BUY":
            for level in self.bids:
                if level.price > price + 0.00001:
                    total += level.total
                elif abs(level.price - price) < 0.00001:
                    # 同一价格层级，假设我们排在最后，那么前方保护就是 (该层总额 - 我们自己的金额)
                    total += max(0, level.total - order_amount)
                else:
                    break
        else:
            for level in self.asks:
                if level.price < price - 0.00001:
                    total += level.total
                elif abs(level.price - price) < 0.00001:
                    total += max(0, level.total - order_amount)
                else:
                    break
        return total


@dataclass
class SoloMarketOrder:
    """订单记录"""
    order_id: str
    topic_id: int
    title: str
    price: float
    amount: float
    create_time: float
    last_check_time: float = 0.0


class SoloMarketMonitor:
    """Solo Market 监控器"""
    
    def __init__(self, config: Dict):
        # 设置全局 socket 超时，防止网络请求无限挂起，解决 urllib3 无限重试或卡死问题
        socket.setdefaulttimeout(20)
        
        self.config = config
        solo_config = config.get('solo_market', {})
        
        self.topic_ids = solo_config.get('topic_ids', [])
        self.min_protection = solo_config.get('min_protection_amount', 500.0)
        self.order_amount = solo_config.get('order_amount', 50.0)
        self.max_rank = solo_config.get('check_bid_position', 10) # 挂单最大档位限制
        
        # 加载 Telegram 配置
        global TG_BOT_TOKEN, TG_CHAT_ID
        tg_config = config.get('telegram', {})
        TG_BOT_TOKEN = tg_config.get('bot_token', '')
        TG_CHAT_ID = tg_config.get('chat_id', '')
        
        # 初始化 fetcher 和 trader
        load_dotenv()
        private_key = os.getenv('OPINION_PRIVATE_KEY')
        apikey = os.getenv('OPINION_APIKEY')
        wallet_address = os.getenv('OPINION_WALLET_ADDRESS')
        rpc_url = os.getenv('OPINION_RPC_URL', 'https://binance.llamarpc.com')
        
        if not private_key:
            raise ValueError("未找到 OPINION_PRIVATE_KEY，请在 .env 文件中配置")
        
        if not apikey:
            raise ValueError("未找到 OPINION_APIKEY，请在 .env 文件中配置")
        
        # 代理配置
        proxy_config = config.get('proxy', {})
        proxy = None
        if proxy_config.get('enabled'):
            proxy = {
                'http': proxy_config.get('http'),
                'https': proxy_config.get('https'),
            }
        
        if config.get('simulation'):
            logger.info(">>> 启用模拟模式 (Simulation Mode) <<<")
            self.fetcher = MockFetcher(self)
            self.trader = MockTrader()
            self.trader.set_fetcher(self.fetcher)
        else:
            self.fetcher = OpinionFetcher(private_key=private_key, proxy=proxy, apikey=apikey)
            self.trader = OpinionTraderSDK(
                private_key=private_key,
                wallet_address=wallet_address,
                apikey=apikey,
                rpc_url=rpc_url,
                proxy=proxy,
            )
        
        # 订单跟踪
        self.orders: Dict[int, SoloMarketOrder] = {}
        self.market_info: Dict[int, Dict] = {}
        
        self.running = False
        
        # 状态报告定时器 - 改为每小时整点推送
        self.last_status_report = time.time()
        self.next_report_hour = -1  # 下次报告的小时数
        
        logger.info(f"Solo Market 监控器初始化完成")
        logger.info(f"监控市场: {self.topic_ids}")
        logger.info(f"最小保护金额: ${self.min_protection}")
        logger.info(f"挂单金额: ${self.order_amount}")
        logger.info(f"挂单档位限制: {self.max_rank}")
    
    def fetch_orderbook(self, topic_id: int, token_id: str) -> Optional[OrderBook]:
        """获取订单簿"""
        try:
            if not token_id:
                logger.warning(f"市场 {topic_id} 缺少 token_id")
                return None
            
            # 使用 SDK 获取订单簿
            logger.debug(f"正在获取订单簿: topic_id={topic_id}, token_id={token_id[:20]}...")
            ob_result = self.trader.client.get_orderbook(str(token_id))
            
            if not ob_result:
                logger.debug(f"SDK 返回空结果")
                return None
                
            if not hasattr(ob_result, 'result'):
                logger.debug(f"SDK 返回无 result 属性: {type(ob_result)}")
                return None
            
            result = ob_result.result
            data = result.data if hasattr(result, 'data') else result
            
            # 解析订单簿
            bids = []
            asks = []
            
            bid_list = getattr(data, 'bids', []) or []
            ask_list = getattr(data, 'asks', []) or []
            
            logger.debug(f"订单簿数据: {len(bid_list)} bids, {len(ask_list)} asks")
            
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
                logger.debug(f"订单簿: 市场 {topic_id} best_bid={best_bid:.4f} best_ask={best_ask:.4f}")
                return OrderBook(bids=bids, asks=asks, best_bid=best_bid, best_ask=best_ask)
            else:
                logger.debug(f"订单簿无有效买单")
            
            return None
            
        except Exception as e:
            logger.debug(f"获取订单簿失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _get_rank_and_protection(self, order_book: OrderBook, side: str, price: float) -> tuple[int, float]:
        """获取价格排名(1-based)和前方保护金额"""
        if not order_book:
            return 0, 0.0
        
        rank = 1
        # 在计算当前订单保护时，减去自己这一单的金额
        protection = order_book.get_protection_amount(side, price, self.order_amount)
        
        if side == "BUY":
            for level in order_book.bids:
                if level.price > price + 0.00001:
                    rank += 1
                else:
                    break
        else:
            for level in order_book.asks:
                if level.price < price - 0.00001:
                    rank += 1
                else:
                    break
        return rank, protection
    
    def calculate_safe_price(self, order_book: OrderBook, max_rank: Optional[int] = None) -> Optional[tuple[float, int]]:
        """计算安全挂单价格
        
        逻辑:
        1. 遍历订单簿
        2. 找到第一个满足 Cumulative_Protection >= min_protection_amount 的档位 i
        3. 如果 i+1 > max_rank，说明位置太靠后了
        4. 价格策略:
           - 如果 i+1 == 1 (买1满足保护): 挂单价格 = level_1.price - 0.001
           - 如果 i+1 > 1: 挂单价格 = level_i.price (匹配该档位)
        """
        if not order_book or not order_book.bids:
            return None
        
        cumulative_protection = 0.0
        for i, level in enumerate(order_book.bids):
            rank = i + 1
            if max_rank and rank > max_rank:
                break
                
            cumulative_protection += level.total
            
            if cumulative_protection >= self.min_protection:
                if rank == 1:
                    # 买1特殊处理: 挂在买1价 - 0.001
                    target_price = level.price - 0.001
                else:
                    # 买2及以下: 匹配该档位价格
                    target_price = level.price
                
                if target_price < 0.01: target_price = 0.01
                return round(target_price, 4), rank
        
        return None
    
    def place_order(self, topic_id: int) -> bool:
        """下单"""
        try:
            # 获取市场信息
            if topic_id not in self.market_info:
                market_info = self.trader.get_market_by_topic_id(topic_id)
                if not market_info:
                    logger.error(f"无法获取市场 {topic_id} 信息（可能是多选市场）")
                    return False
                
                # 验证是否为二元市场（必须有 yes_token_id 和 no_token_id）
                if not market_info.get('yes_token_id') or not market_info.get('no_token_id'):
                    logger.warning(f"市场 {topic_id} 不是二元市场，跳过")
                    return False
                
                self.market_info[topic_id] = market_info
            
            market_info = self.market_info[topic_id]
            title = market_info['title']
            yes_token_id = market_info['yes_token_id']
            
            # 获取订单簿
            order_book = self.fetch_orderbook(topic_id, yes_token_id)
            if not order_book:
                logger.warning(f"无法获取市场 {topic_id} 订单簿")
                return False
            
            # 初始下单限制在 max_rank 内
            calc_res = self.calculate_safe_price(order_book, max_rank=self.max_rank)
            if not calc_res:
                logger.warning(f"无法在限制档位 {self.max_rank} 内找到安全价格")
                
                # 发送 TG 通知
                proxy_config = self.config.get('proxy', {})
                proxy = None
                if proxy_config.get('enabled'):
                    proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
                
                msg = f"""⚠️ <b>订单超出档位限制</b>
━━━━━━━━━━━━━━━
📌 市场: {title[:40]}
📊 限制档位: <code>{self.max_rank}</code>
💰 最小保护: <code>${self.min_protection}</code>
━━━━━━━━━━━━━━━
无法在限制档位内找到安全价格，订单已取消！"""
                send_tg_notification(msg, proxy)
                
                return False
            
            price, rank = calc_res
            
            # 计算该价格的排名和前方保护（用于日志显示）
            rank_check, protection = self._get_rank_and_protection(order_book, "BUY", price)
            
            rank_str = f"(买{rank_check}价 ${protection:.0f})"
            logger.info(f"[下单准备] {title[:30]} | 目标价格: {price:.4f} {rank_str}")
            
            # 下单（直接传递 token_id 避免重复获取市场信息）
            result = self.trader.place_order(
                topic_id=topic_id,
                outcome="YES",
                amount=self.order_amount,
                price=price,
                order_type=2,
                side="BUY",
                token_id=yes_token_id,  # 直接传递已获取的 token_id
            )
            
            if result == "INSUFFICIENT_BALANCE":
                logger.error("余额不足")
                return False
            
            if not result:
                logger.error("下单失败")
                return False
            
            # 提取订单 ID
            order_id = None
            if hasattr(result, 'result') and result.result:
                res = result.result
                if hasattr(res, 'order_data') and res.order_data:
                    order_id = str(getattr(res.order_data, 'order_id', ''))
                elif hasattr(res, 'order_id'):
                    order_id = str(res.order_id)
            
            if not order_id and hasattr(result, 'order_id'):
                order_id = str(result.order_id)
            
            if not order_id:
                logger.error("无法获取订单 ID")
                return False
            
            # 记录订单
            self.orders[topic_id] = SoloMarketOrder(
                order_id=order_id,
                topic_id=topic_id,
                title=title,
                price=price,
                amount=self.order_amount,
                create_time=time.time(),
                last_check_time=time.time(),
            )
            
            logger.success(f"[挂单成功] {title[:30]} @ {price:.4f} {rank_str} | 单号: {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"下单异常: {e}")
            return False
    
    def check_and_adjust_order(self, topic_id: int) -> bool:
        """检查并调整订单"""
        try:
            if topic_id not in self.orders:
                return False
            
            order = self.orders[topic_id]
            market_info = self.market_info[topic_id]
            yes_token_id = market_info['yes_token_id']
            
            # 检查订单是否还存在（可能已被成交）
            try:
                order_status = self.trader.check_order_status(order.order_id)
                if order_status:
                    # API 返回的是对象，不是字典
                    status = getattr(order_status, 'status', None)
                    if hasattr(order_status, 'result') and order_status.result:
                        result_data = order_status.result
                        if hasattr(result_data, 'order_data'):
                            status = getattr(result_data.order_data, 'status', None)
                    
                    # 检查是否已成交 (status=3 表示已成交)
                    if status in [3, '3', 'filled', 'FILLED']:
                        duration = int(time.time() - order.create_time)
                        logger.warning(f"⚠️ [非预期成交] {order.title[:30]} @ {order.price:.4f} | 金额: ${order.amount} | 时长: {duration}s")
                        
                        # 发送 TG 通知
                        proxy_config = self.config.get('proxy', {})
                        proxy = None
                        if proxy_config.get('enabled'):
                            proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
                        
                        msg = f"""⚠️ <b>非预期成交</b>
━━━━━━━━━━━━━━━
📌 市场: {order.title[:40]}
📊 方向: BUY YES
💰 价格: <code>{order.price:.4f}</code>
💵 金额: <code>${order.amount}</code>
⏰ 挂单时长: <code>{duration}秒</code>
━━━━━━━━━━━━━━━
请检查市场状况！"""
                        send_tg_notification(msg, proxy)
                        
                        del self.orders[topic_id]
                        return False
            except Exception as e:
                logger.debug(f"检查订单状态失败: {e}")
            
            # 获取订单簿
            order_book = self.fetch_orderbook(topic_id, yes_token_id)
            if not order_book:
                return False
            
            # 获取当前状态
            current_rank, current_protection = self._get_rank_and_protection(order_book, "BUY", order.price)
            
            needs_adjust = False
            reason = ""
            
            # 触发器 A: 保护不足 (始终监控)
            if current_protection < self.min_protection:
                needs_adjust = True
                reason = "保护不足"
                logger.info(f"市场 {topic_id} {reason}: ${current_protection:.0f} < ${self.min_protection}")
            
            # 触发器 B: 档位超标 (仅在 > N 位时触发向上部位)
            elif current_rank > self.max_rank:
                needs_adjust = True
                reason = "档位超标"
                logger.info(f"市场 {topic_id} {reason}: 买{current_rank} > 限制{self.max_rank}")
            
            if not needs_adjust:
                order.last_check_time = time.time()
                return True
            
            # 需要调整
            # 策略：即使因为档位超标触发，也是寻找 [1, max_rank] 范围内最好的安全位置
            # 如果实在找不到，说明市场变厚了或者保护设置太高。
            calc_res = self.calculate_safe_price(order_book, max_rank=self.max_rank)
            
            # 如果全球范围内（不限档位）也没有安全位置，那就没办法了
            if not calc_res:
                global_res = self.calculate_safe_price(order_book) # 全球搜索
                if not global_res:
                    logger.warning(f"市场 {topic_id} 全球搜索亦无安全位置，保持原样")
                    return True
                calc_res = global_res
                
            new_price, new_rank = calc_res
            
            # 如果算出来价格没变，且不是因为保护不足触发的，那就没必要动
            if abs(new_price - order.price) < 0.00001 and reason != "保护不足":
                return True

            logger.info(f"触发调整({reason}): {order.price:.4f}(买{current_rank}) -> {new_price:.4f}(买{new_rank})")
            
            # 撤销旧单
            success = self.trader.cancel_order(order.order_id)
            if not success:
                logger.error("撤单失败")
                return False
            
            del self.orders[topic_id]
            time.sleep(0.5)
            return self.place_order(topic_id)
            
        except Exception as e:
            logger.error(f"检查调整订单异常: {e}")
            return False
    
    def send_status_report(self):
        """发送状态报告到 Telegram"""
        try:
            # 获取账户余额
            available_balance = "未知"
            frozen_balance = "未知"
            total_balance = "未知"
            try:
                if hasattr(self.trader, 'client') and hasattr(self.trader.client, 'get_my_balances'):
                    balances = self.trader.client.get_my_balances()
                    if balances and hasattr(balances, 'result'):
                        result = balances.result
                        
                        # result 直接就是数据对象，没有 data 包装
                        if hasattr(result, 'balances') and result.balances:
                            # 通常只有一个 USDC 余额
                            bal = result.balances[0]
                            available_balance = f"${float(getattr(bal, 'available_balance', 0) or 0):.2f}"
                            frozen_balance = f"${float(getattr(bal, 'frozen_balance', 0) or 0):.2f}"
                            total_balance = f"${float(getattr(bal, 'total_balance', 0) or 0):.2f}"
            except Exception as e:
                logger.debug(f"获取余额失败: {e}")
            
            # 构建挂单信息
            order_lines = []
            total_amount = 0.0
            
            for topic_id, order in self.orders.items():
                market_info = self.market_info.get(topic_id)
                if market_info:
                    order_book = self.fetch_orderbook(topic_id, market_info['yes_token_id'])
                    if order_book:
                        rank, protection = self._get_rank_and_protection(order_book, "BUY", order.price)
                        rank_str = f"买{rank}价"
                        protection_str = f"${protection:.0f}"
                    else:
                        rank_str = "未知"
                        protection_str = "未知"
                else:
                    rank_str = "未知"
                    protection_str = "未知"
                
                duration = int((time.time() - order.create_time) / 3600)  # 转换为小时
                order_lines.append(
                    f"📌 {order.title[:30]}\n"
                    f"   价格: <code>{order.price:.4f}</code> | {rank_str} | 保护: {protection_str}\n"
                    f"   金额: <code>${order.amount}</code> | 已挂: {duration}小时"
                )
                total_amount += order.amount
            
            if not order_lines:
                order_info = "<i>当前无挂单</i>"
            else:
                order_info = "\n\n".join(order_lines)
            
            # 发送通知
            proxy_config = self.config.get('proxy', {})
            proxy = None
            if proxy_config.get('enabled'):
                proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
            
            msg = f"""📊 <b>Solo Market 状态报告</b>
━━━━━━━━━━━━━━━
💰 可用余额: <code>{available_balance}</code>
🔒 冻结余额: <code>{frozen_balance}</code>
💵 总余额: <code>{total_balance}</code>
📦 挂单数量: <code>{len(self.orders)}</code>
💼 挂单总额: <code>${total_amount:.2f}</code>
━━━━━━━━━━━━━━━

{order_info}

━━━━━━━━━━━━━━━
⏰ 报告时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"""
            
            send_tg_notification(msg, proxy)
            logger.info("已发送状态报告到 Telegram")
            
        except Exception as e:
            logger.error(f"发送状态报告失败: {e}")
    
    def run(self):
        """运行监控"""
        self.running = True
        logger.info("启动 Solo Market 监控...")
        
        try:
            # 初始下单
            for topic_id in self.topic_ids:
                logger.info(f"初始化市场 {topic_id}...")
                self.place_order(topic_id)
                time.sleep(1)  # 避免请求过快
            
            logger.info(f"已下单 {len(self.orders)} 个市场")
            
            # 发送初始状态报告
            self.send_status_report()
            
            # 持续监控
            while self.running:
                for topic_id in list(self.orders.keys()):
                    try:
                        self.check_and_adjust_order(topic_id)
                    except Exception as e:
                        logger.error(f"处理市场 {topic_id} 失败: {e}")
                
                # 显示当前挂单状态
                if self.orders:
                    logger.debug("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    for topic_id, order in self.orders.items():
                        # 获取订单簿计算当前排名
                        market_info = self.market_info.get(topic_id)
                        if market_info:
                            order_book = self.fetch_orderbook(topic_id, market_info['yes_token_id'])
                            if order_book:
                                rank, protection = self._get_rank_and_protection(order_book, "BUY", order.price)
                                rank_str = f"(买{rank}价 ${protection:.0f})"
                            else:
                                rank_str = "(未知)"
                        else:
                            rank_str = "(未知)"
                        
                        duration = int(time.time() - order.create_time)
                        logger.debug(f"[{order.title[:30]}] @ {order.price:.4f} {rank_str} | 已挂单: {duration}s")
                    logger.debug("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                # 检查是否需要发送整点状态报告
                current_time = time.localtime()
                current_hour = current_time.tm_hour
                current_minute = current_time.tm_min
                
                # 在每小时的第0分钟发送报告（允许1分钟的误差窗口）
                if current_minute == 0 and current_hour != self.next_report_hour:
                    self.send_status_report()
                    self.next_report_hour = current_hour
                elif current_minute > 1:
                    # 重置下次报告小时数，避免错过整点
                    self.next_report_hour = -1
                
                if self.config.get('simulation'):
                    # 模拟模式下，根据输入执行特定的盘口变化
                    # 注意：在真实的循环中，这通常需要异步非阻塞输入，这里简化为每5秒自动触发一次演示
                    elapsed = int(time.time()) % 30
                    if elapsed == 5:
                        logger.warning("[模拟] 盘口向上大平移 10¢, 触发档位由1变为11+ (超标)...")
                        self.fetcher.shift_book(0.10)
                        time.sleep(1)
                    elif elapsed == 15:
                        logger.warning("[模拟] 剧烈削减盘口厚度, 触发保护不足...")
                        # 将前5档全部削减
                        for i in range(5):
                            self.fetcher.set_mock_bid(i, 0.85 - i*0.01, 10.0)
                        time.sleep(1)
                    elif elapsed == 25:
                        logger.warning("[模拟] 盘口恢复厚度...")
                        self.fetcher.set_mock_bid(0, 0.85, 2000.0)
                        time.sleep(1)

                time.sleep(1)  # 尽可能频繁检查
        
        except KeyboardInterrupt:
            logger.info("收到停止信号")
        finally:
            # 撤销所有订单
            logger.info("撤销所有挂单...")
            for order in self.orders.values():
                try:
                    logger.info(f"[撤单] {order.title[:30]} @ {order.price:.4f}")
                    self.trader.cancel_order(order.order_id)
                except Exception as e:
                    logger.error(f"撤单失败: {e}")
            
            self.running = False
            logger.info("Solo Market 监控已停止")


def main():
    # 配置日志
    logger.remove()
    logger.add(
        "log/solo_market_{time:YYYY-MM-DD_HH-mm-ss}.txt",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
        level="INFO",
        rotation="10 MB",
    )
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level} | {message}",
        level="DEBUG",
    )
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", action="store_true", help="运行模拟模式")
    args = parser.parse_args()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    if args.sim:
        config['simulation'] = True
        config['solo_market']['topic_ids'] = [4306]
        config['solo_market']['min_protection_amount'] = 500
        config['solo_market']['check_bid_position'] = 5 # 模拟模式把限制调小，容易触发

    # 创建监控器
    monitor = SoloMarketMonitor(config)
    
    # 运行
    monitor.run()


if __name__ == '__main__':
    main()
