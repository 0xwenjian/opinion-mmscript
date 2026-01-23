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
import sys
import time
import yaml
from typing import Dict, List, Optional
from dataclasses import dataclass
from loguru import logger
from dotenv import load_dotenv

# 导入现有模块
from modules.fetch_opinion import OpinionFetcher
from modules.trader_opinion_sdk import OpinionTraderSDK


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
    
    def get_protection_amount(self, side: str, price: float) -> float:
        """计算目标价位前方的累计挂单金额（保护厚度）"""
        total = 0.0
        if side == "BUY":
            for level in self.bids:
                if level.price > price:
                    total += level.size * level.price
                else:
                    break
        else:
            for level in self.asks:
                if level.price < price:
                    total += level.size * level.price
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
        self.config = config
        solo_config = config.get('solo_market', {})
        
        self.topic_ids = solo_config.get('topic_ids', [])
        self.min_protection = solo_config.get('min_protection_amount', 500.0)
        self.order_amount = solo_config.get('order_amount', 50.0)
        
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
        
        logger.info(f"Solo Market 监控器初始化完成")
        logger.info(f"监控市场: {self.topic_ids}")
        logger.info(f"最小保护金额: ${self.min_protection}")
        logger.info(f"挂单金额: ${self.order_amount}")
    
    def fetch_orderbook(self, topic_id: int, token_id: str) -> Optional[OrderBook]:
        """获取订单簿"""
        try:
            if not token_id:
                logger.warning(f"市场 {topic_id} 缺少 token_id")
                return None
            
            # 使用 SDK 获取订单簿
            ob_result = self.trader.client.get_orderbook(str(token_id))
            if not ob_result or not hasattr(ob_result, 'result'):
                return None
            
            result = ob_result.result
            data = result.data if hasattr(result, 'data') else result
            
            # 解析订单簿
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
                logger.debug(f"订单簿: 市场 {topic_id} best_bid={best_bid:.4f} best_ask={best_ask:.4f}")
                return OrderBook(bids=bids, asks=asks, best_bid=best_bid, best_ask=best_ask)
            
            return None
            
        except Exception as e:
            logger.debug(f"获取订单簿失败: {e}")
            return None
    
    def _get_rank_and_protection(self, order_book: OrderBook, side: str, price: float) -> str:
        """获取价格排名描述和前方保护金额"""
        if not order_book:
            return "(未知)"
        
        better_count = 0
        protection = 0.0
        
        if side == "BUY":
            for level in order_book.bids:
                if level.price > price + 0.0001:
                    better_count += 1
                    protection += level.total
                else:
                    break
            return f"(买{better_count + 1}价 ${protection:.0f})"
        else:
            for level in order_book.asks:
                if level.price < price - 0.0001:
                    better_count += 1
                    protection += level.total
                else:
                    break
            return f"(卖{better_count + 1}价 ${protection:.0f})"
    
    def calculate_safe_price(self, order_book: OrderBook) -> Optional[float]:
        """计算安全挂单价格（买单）
        
        逻辑：
        1. 从 best_bid 开始向下遍历订单簿
        2. 累加每一档的保护金额
        3. 当累计保护金额达到 min_protection_amount 时
        4. 在当前档位价格下方 0.1¢ (0.001) 处挂单
        """
        if not order_book or order_book.best_bid <= 0:
            return None
        
        # 遍历整个订单簿，找到第一个保护金额足够的位置
        cumulative_protection = 0.0
        
        for i, level in enumerate(order_book.bids):
            # 累加当前档位的保护金额
            cumulative_protection += level.total
            
            # 如果累计保护金额达到要求
            if cumulative_protection >= self.min_protection:
                # 在当前档位下方 0.1¢ 挂单
                target_price = level.price - 0.001
                
                # 确保价格合理（不低于 0.01）
                if target_price < 0.01:
                    target_price = 0.01
                
                logger.debug(f"找到安全位置: 当前档位 {level.price:.4f}, 累计保护 ${cumulative_protection:.0f}, 挂单价格 {target_price:.4f}")
                return round(target_price, 4)
        
        # 如果整个订单簿都没有足够保护，返回 None
        logger.warning(f"整个订单簿保护不足: ${cumulative_protection:.0f} < ${self.min_protection}")
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
            
            # 计算安全价格
            price = self.calculate_safe_price(order_book)
            if not price:
                logger.warning(f"无法计算市场 {topic_id} 安全价格")
                return False
            
            # 检查保护金额
            protection = order_book.get_protection_amount("BUY", price)
            if protection < self.min_protection:
                logger.warning(f"市场 {topic_id} 保护不足: ${protection:.0f} < ${self.min_protection}")
                return False
            
            # 计算排名和保护
            rank_str = self._get_rank_and_protection(order_book, "BUY", price)
            
            logger.info(f"[挂单] {title[:30]} @ {price:.4f} ${self.order_amount} {rank_str}")
            
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
            
            # 获取订单簿
            order_book = self.fetch_orderbook(topic_id, yes_token_id)
            if not order_book:
                return False
            
            # 检查当前订单的保护金额
            current_protection = order_book.get_protection_amount("BUY", order.price)
            
            # 如果保护金额足够，保持不动
            if current_protection >= self.min_protection:
                logger.debug(f"市场 {topic_id} 保护充足: ${current_protection:.0f} >= ${self.min_protection}")
                order.last_check_time = time.time()
                return True
            
            # 保护不足，需要调整
            logger.info(f"市场 {topic_id} 保护不足: ${current_protection:.0f} < ${self.min_protection}，准备调整")
            
            # 计算新的安全价格
            new_price = self.calculate_safe_price(order_book)
            if not new_price:
                logger.warning(f"无法计算新的安全价格")
                return False
            
            # 计算新旧排名
            old_rank = self._get_rank_and_protection(order_book, "BUY", order.price)
            new_rank = self._get_rank_and_protection(order_book, "BUY", new_price)
            
            logger.info(f"盘口变化，调整挂单: {order.price:.4f} {old_rank} -> {new_price:.4f} {new_rank}")
            
            # 撤销旧单
            duration = int(time.time() - order.create_time)
            logger.info(f"[撤单] {order.title[:30]} @ {order.price:.4f} | 挂单时长: {duration}s")
            success = self.trader.cancel_order(order.order_id)
            
            if not success:
                logger.error("撤单失败")
                return False
            
            # 删除旧订单记录
            del self.orders[topic_id]
            
            # 下新单
            time.sleep(0.5)  # 短暂延迟
            return self.place_order(topic_id)
            
        except Exception as e:
            logger.error(f"检查调整订单异常: {e}")
            return False
    
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
                                rank_str = self._get_rank_and_protection(order_book, "BUY", order.price)
                            else:
                                rank_str = "(未知)"
                        else:
                            rank_str = "(未知)"
                        
                        duration = int(time.time() - order.create_time)
                        logger.debug(f"[{order.title[:30]}] @ {order.price:.4f} {rank_str} | 已挂单: {duration}s")
                    logger.debug("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
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
    
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 创建监控器
    monitor = SoloMarketMonitor(config)
    
    # 运行
    monitor.run()


if __name__ == '__main__':
    main()
