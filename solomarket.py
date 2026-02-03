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
import traceback
from typing import Dict, List, Optional
from dataclasses import dataclass
from loguru import logger
from dotenv import load_dotenv

# 导入现有模块
from modules.fetch_opinion import OpinionFetcher
from modules.trader_opinion_sdk import OpinionTraderSDK
from modules.models import OrderBook, OrderBookLevel, SoloMarketOrder
from modules.mock_utils import MockFetcher, MockTrader

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
        
        # 加载环境变量 (main中已经加载过一次，这里确保同步)
        load_dotenv()
        
        # 加载 Telegram 配置 (优先从 .env 加载)
        global TG_BOT_TOKEN, TG_CHAT_ID
        TG_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('telegram_bot_token')
        TG_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('telegram_chat_id')
        
        # 如果 .env 没写，再看 config.yaml (兼容性处理)
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            tg_config = config.get('telegram', {})
            TG_BOT_TOKEN = TG_BOT_TOKEN or tg_config.get('bot_token', '')
            TG_CHAT_ID = TG_CHAT_ID or tg_config.get('chat_id', '')
        
        # 初始化 fetcher 和 trader
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
            self.wallet_address = "MOCK_WALLET_ADDRESS"
        else:
            self.fetcher = OpinionFetcher(private_key=private_key, proxy=proxy, apikey=apikey)
            self.trader = OpinionTraderSDK(
                private_key=private_key,
                wallet_address=wallet_address,
                apikey=apikey,
                rpc_url=rpc_url,
                proxy=proxy,
            )
            self.wallet_address = self.trader.wallet_address
            
        self.wallet_alias = os.getenv('OPINION_WALLET_ALIAS', '')
        
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
        
    def _send_tg(self, message: str):
        """发送带钱包地址的 Telegram 通知"""
        proxy_config = self.config.get('proxy', {})
        proxy = None
        if proxy_config.get('enabled'):
            proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
            
        if self.wallet_alias:
            user_label = f"🏷️ 别名: <b>{self.wallet_alias}</b>"
        else:
            addr_short = f"{self.wallet_address[:6]}...{self.wallet_address[-4:]}"
            user_label = f"👤 钱包: <code>{addr_short}</code>"
            
        footer = f"\n━━━━━━━━━━━━━━━\n{user_label}"
        
        # 避免重复添加 footer
        if footer not in message:
            message += footer
            
        send_tg_notification(message, proxy)
    
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
            # 遇到网络错误时暂停 10 秒，防止请求过快导致被服务器重置连接 (ConnectionResetError)
            time.sleep(10)
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
        2. 累加各档位金额，找到第一个满足累计金额 >= min_protection 的档位 i
        3. 挂单价格 = level[i].price - 0.001 (躲在该档位后面)
        4. 预估档位 = i + 2
        """
        if not order_book or not order_book.bids:
            return None
        
        cumulative_total = 0.0
        for i, level in enumerate(order_book.bids):
            estimated_rank = i + 2
            
            # 如果指定了最大档位限制，超出则停止搜索
            if max_rank and estimated_rank > max_rank:
                break
                
            cumulative_total += level.total
            if cumulative_total >= self.min_protection:
                target_price = level.price - 0.001
                if target_price < 0.01: target_price = 0.01
                return round(target_price, 4), estimated_rank
        
        return None
    
    def place_order(self, topic_id: int) -> bool:
        """下单"""
        try:
            # 获取市场信息
            if topic_id not in self.market_info:
                market_info = self.trader.get_market_by_topic_id(topic_id)
                if not market_info:
                    logger.error(f"无法获取市场 {topic_id} 信息")
                    return False
                
                if not market_info.get('yes_token_id'):
                    logger.warning(f"市场 {topic_id} 缺少 YES TOKEN，跳过")
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
            
            # 初始下单直接进行全局搜索 (不设 max_rank)
            # 如果下单位置超过了 max_rank，则由 check_and_adjust_order 的触发器 B 负责后续回归
            calc_res = self.calculate_safe_price(order_book, max_rank=None)
            
            if not calc_res:
                logger.warning(f"在全球范围内亦无法找到满足 ${self.min_protection} 保护的安全价格")
                
                msg = f"""⚠️ <b>无法找到安全挂单位置</b>
━━━━━━━━━━━━━━━
📌 市场: {title[:40]}
💰 最小保护: <code>${self.min_protection}</code>
━━━━━━━━━━━━━━━
当前订单簿深度不足以满足保护要求，下单已跳过！"""
                self._send_tg(msg)
                
                return False
            
            price, rank = calc_res

            # 打印前10档盘口信息，辅助观察
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[{title[:30]}] 市场深度 (前10档):")
            cumulative_total = 0.0
            for i, level in enumerate(order_book.bids[:10]):
                cumulative_total += level.total
                logger.info(f"   买{i+1}: {level.price:.4f} (本档: ${level.total:.0f} | 累计保护: ${cumulative_total:.0f})")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
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
                    # 获取状态和成交金额
                    status = getattr(order_status, 'status', None)
                    filled_amount = 0.0
                    
                    if hasattr(order_status, 'result') and order_status.result:
                        result_data = order_status.result
                        if hasattr(result_data, 'order_data'):
                            order_data = result_data.order_data
                            status = getattr(order_data, 'status', status)
                            # 尝试获取成交金额 (兼容多种可能的字段名)
                            filled_amount = float(
                                getattr(order_data, 'filled_amount', 0) or 
                                getattr(order_data, 'executed_amount', 0) or
                                getattr(order_data, 'filledAmount', 0) or
                                0
                            )
                    
                    # 只要有成交金额，就认作成交（解决部分成交后状态变为 canceled 的漏洞）
                    if filled_amount > 0:
                        is_partial = (status not in [3, '3', 'filled', 'FILLED'])
                        status_str = "部分成交" if is_partial else "全额成交"
                        
                        duration = int(time.time() - order.create_time)
                        logger.warning(f"⚠️ [{status_str}] {order.title[:30]} @ {order.price:.4f} | 成交: ${filled_amount}/{order.amount} | 状态: {status} | 时长: {duration}s")
                        
                        msg = f"""⚠️ <b>{status_str}</b>
━━━━━━━━━━━━━━━
📌 市场: {order.title[:40]}
📊 方向: BUY YES
💰 挂单价格: <code>{order.price:.4f}</code>
💵 成交金额: <code>${filled_amount} / ${order.amount}</code>
⚙️ 最终状态: <code>{status}</code>
⏰ 挂单时长: <code>{duration}秒</code>
━━━━━━━━━━━━━━━
请检查持仓！"""
                        self._send_tg(msg)
                        
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
            calc_res = None
            
            # 触发器 A: 保护不足 (始终监控)
            if current_protection < self.min_protection:
                needs_adjust = True
                reason = "保护不足"
                logger.info(f"市场 {topic_id} {reason}: 当前保护 ${current_protection:.0f} < 阈值 ${self.min_protection}")
                
                # 寻找新位置：先看范围内，再看全球
                calc_res = self.calculate_safe_price(order_book, max_rank=self.max_rank)
                if not calc_res:
                    calc_res = self.calculate_safe_price(order_book) # 全球搜索
            
            # 触发器 B: 档位超标 (仅在当前处于范围推荐外，且范围内出现了新的安全位置时触发)
            elif current_rank > self.max_rank:
                # 检查范围内是否有安全价格可以回归
                back_in_range_res = self.calculate_safe_price(order_book, max_rank=self.max_rank)
                if back_in_range_res:
                    # 发现范围内有安全位置了，执行回归
                    needs_adjust = True
                    reason = "档位超标 (回归范围)"
                    calc_res = back_in_range_res
                    logger.info(f"市场 {topic_id} {reason}: 当前买{current_rank}，探测到范围内买{calc_res[1]}已安全")
                else:
                    # 虽然档位超标，但范围内依然不安全，继续保持当前深度观察，不报警
                    pass
            
            if not needs_adjust or not calc_res:
                order.last_check_time = time.time()
                return True
            
            new_price, new_rank = calc_res
            
            # 如果新算出的价格和旧价格一致，且不是因为保护不足（即保护依然由于某种边界计算导致的微小差异），则忽略
            if abs(new_price - order.price) < 0.00001:
                return True


            # 打印前10档盘口信息，辅助观察
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[{order.title[:30]}] 触发调整 - 市场深度 (前10档):")
            cumulative_total = 0.0
            for i, level in enumerate(order_book.bids[:10]):
                cumulative_total += level.total
                logger.info(f"   买{i+1}: {level.price:.4f} (本档: ${level.total:.0f} | 累计保护: ${cumulative_total:.0f})")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            logger.info(f"执行调整({reason}): {order.price:.4f}(买{current_rank}) -> {new_price:.4f}(买{new_rank})")
            
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
            
            self._send_tg(msg)
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
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", action="store_true", help="运行模拟模式")
    parser.add_argument("--config", type=str, default="config.yaml", help="配置文件路径")
    parser.add_argument("--env", type=str, default=".env", help="环境变量文件路径")
    args = parser.parse_args()

    # 配置日志 (使用配置名区分日志文件)
    config_name = os.path.splitext(os.path.basename(args.config))[0]
    logger.remove()
    logger.add(
        f"log/solo_{config_name}_{{time:YYYY-MM-DD_HH-mm-ss}}.txt",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
        level="INFO",
        rotation="10 MB",
    )
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level} | {message}",
        level="DEBUG",
    )
    
    # 强制先加载指定的 .env
    if os.path.exists(args.env):
        load_dotenv(args.env, override=True)
        logger.info(f"已加载环境变量: {args.env}")

    # 加载配置
    with open(args.config, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    if args.sim:
        config['simulation'] = True
        config['solo_market']['topic_ids'] = [4306]
        config['solo_market']['min_protection_amount'] = 500
        config['solo_market']['check_bid_position'] = 5 # 模拟模式把限制调小，容易触发

    # 创建监控器
    monitor = SoloMarketMonitor(config)
    
    # 运行
    try:
        monitor.run()
    except Exception as e:
        error_msg = f"❌ <b>脚本致命错误</b>\n\n<code>{str(e)}</code>\n\n<pre>{traceback.format_exc()[-500:]}</pre>"
        monitor._send_tg(error_msg)
        logger.critical(f"脚本致命错误: {e}")
        raise e


if __name__ == '__main__':
    main()
