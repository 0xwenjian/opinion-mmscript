#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用真实 API 订单簿数据测试 calculate_safe_price 逻辑

用法:
    python3 test/test_with_real_orderbook.py
    python3 test/test_with_real_orderbook.py --env-file account_1.env
    python3 test/test_with_real_orderbook.py --topic-id 5055
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import yaml
from loguru import logger
from dotenv import load_dotenv

from modules.models import OrderBook, OrderBookLevel, SoloMarketOrder
from modules.trader_opinion_sdk import OpinionTraderSDK


def fetch_real_orderbook(trader, topic_id: int) -> tuple:
    """从 API 获取真实订单簿原始数据并返回 (OrderBook, raw_bids)"""
    market_info = trader.get_market_by_topic_id(topic_id)
    if not market_info:
        logger.error(f"无法获取市场 {topic_id} 信息")
        return None, None
    
    title = market_info.get('title', '未知')
    yes_token_id = market_info.get('yes_token_id')
    
    if not yes_token_id:
        logger.error(f"市场 {topic_id} 缺少 yes_token_id")
        return None, None
    
    logger.info(f"市场: {title}")
    logger.info(f"token_id: {yes_token_id[:20]}...")
    
    ob_result = trader.client.get_orderbook(str(yes_token_id))
    if not ob_result or not hasattr(ob_result, 'result'):
        logger.error("获取订单簿失败")
        return None, None
    
    result = ob_result.result
    data = result.data if hasattr(result, 'data') else result
    
    bid_list = getattr(data, 'bids', []) or []
    ask_list = getattr(data, 'asks', []) or []
    
    bids = []
    for bid in bid_list:
        price = float(getattr(bid, 'price', 0) or 0)
        size = float(getattr(bid, 'size', 0) or getattr(bid, 'amount', 0) or 0)
        if price > 0:
            bids.append(OrderBookLevel(price=price, size=size, total=price * size))
    
    asks = []
    for ask in ask_list:
        price = float(getattr(ask, 'price', 0) or 0)
        size = float(getattr(ask, 'size', 0) or getattr(ask, 'amount', 0) or 0)
        if price > 0:
            asks.append(OrderBookLevel(price=price, size=size, total=price * size))
    
    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)
    
    best_bid = bids[0].price if bids else 0.0
    best_ask = asks[0].price if asks else 1.0
    
    order_book = OrderBook(bids=bids, asks=asks, best_bid=best_bid, best_ask=best_ask)
    
    return order_book, title


def test_calculate_safe_price(order_book: OrderBook, title: str, min_protection: float, order_amount: float):
    """使用真实订单簿数据测试 calculate_safe_price 逻辑"""
    
    logger.info("=" * 70)
    logger.info(f"🧪 测试: calculate_safe_price")
    logger.info(f"📌 市场: {title}")
    logger.info(f"💰 最小保护金额: ${min_protection}")
    logger.info(f"📦 挂单金额: ${order_amount}")
    logger.info("=" * 70)
    
    # 打印买方订单簿
    logger.info(f"\n📊 买方订单簿 ({len(order_book.bids)} 档):")
    logger.info(f"   {'档位':>4} | {'价格':>8} | {'数量':>10} | {'本档金额':>10} | {'累计金额':>10} | 满足保护?")
    logger.info(f"   {'----':>4} | {'--------':>8} | {'----------':>10} | {'----------':>10} | {'----------':>10} | ---------")
    
    cumulative = 0.0
    safe_found = False
    safe_rank = None
    safe_price = None
    
    for i, level in enumerate(order_book.bids):
        cumulative += level.total
        meets = cumulative >= min_protection
        marker = " ✅ <-- 安全位" if (meets and not safe_found) else ""
        
        if meets and not safe_found:
            safe_found = True
            safe_rank = i + 2
            safe_price = round(level.price - 0.001, 4)
        
        if i < 20:  # 只打印前 20 档
            logger.info(f"   买{i+1:>2} | {level.price:>8.4f} | {level.size:>10.1f} | ${level.total:>9.0f} | ${cumulative:>9.0f} |{marker}")
    
    if len(order_book.bids) > 20:
        logger.info(f"   ... 还有 {len(order_book.bids) - 20} 档 ...")
    
    logger.info("")
    
    # 模拟 calculate_safe_price 的结果
    if safe_found:
        logger.success(f"✅ 找到安全挂单位置:")
        logger.info(f"   挂单价格: {safe_price:.4f}")
        logger.info(f"   预估档位: 买{safe_rank}")
        logger.info(f"   前方累计保护: ${cumulative:.0f}")
        
        # 精确计算该价格的前方保护
        actual_protection = 0.0
        actual_rank = 1
        for level in order_book.bids:
            if level.price > safe_price + 0.00001:
                actual_protection += level.total
                actual_rank += 1
            else:
                break
        
        # 减去自己的挂单金额（First-In-Queue 假设）
        logger.info(f"   实际前方保护: ${actual_protection:.0f} (买{actual_rank}价)")
        
        if actual_protection >= min_protection:
            logger.success(f"   ✅ 保护充足 (${actual_protection:.0f} >= ${min_protection})")
        else:
            logger.warning(f"   ⚠️ 保护不足 (${actual_protection:.0f} < ${min_protection}) — 需要调整!")
        
        # 输出配置建议
        logger.info(f"\n📋 如果使用此市场的配置:")
        logger.info(f"   topic_ids: [{0}]  # 实际 ID 需替换")
        logger.info(f"   min_protection_amount: {min_protection}")
        logger.info(f"   order_amount: {order_amount}")
    else:
        logger.error(f"❌ 整个订单簿无法满足 ${min_protection} 的保护要求!")
        logger.info(f"   订单簿总深度: ${cumulative:.0f}")
        logger.info(f"   建议降低 min_protection_amount 或更换市场")
    
    return safe_price, safe_rank


def test_adjustment_scenarios(order_book: OrderBook, title: str, min_protection: float, order_amount: float, safe_price: float):
    """
    模拟调整场景：
    1. 假设当前挂在较深位置，测试是否会前进
    2. 假设当前挂在较浅位置，测试是否会后退
    """
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"🧪 测试: 调整逻辑模拟")
    logger.info("=" * 70)
    
    if not safe_price or not order_book.bids:
        logger.warning("跳过调整测试 (无安全价格)")
        return
    
    # 场景 A: 当前在最深档，应该前进
    deep_price = order_book.bids[-1].price - 0.001 if len(order_book.bids) > 1 else order_book.bids[0].price - 0.005
    logger.info(f"\n📍 场景 A: 假设当前挂在 {deep_price:.4f} (最深档)")
    
    if safe_price > deep_price:
        logger.success(f"   → 应该前进到 {safe_price:.4f} ✅ (前方出现安全位置)")
    else:
        logger.info(f"   → 位置相同或已是最优，无需调整")
    
    # 场景 B: 当前在买2，如果买1保护不足，应该后退
    if len(order_book.bids) >= 2:
        shallow_price = order_book.bids[0].price - 0.001
        shallow_protection = 0.0
        for level in order_book.bids:
            if level.price > shallow_price + 0.00001:
                shallow_protection += level.total
            else:
                break
        
        logger.info(f"\n📍 场景 B: 假设当前挂在 {shallow_price:.4f} (买2价)")
        logger.info(f"   前方保护: ${shallow_protection:.0f}")
        
        if shallow_protection < min_protection:
            logger.success(f"   → 保护不足，应该后退到 {safe_price:.4f} ✅ (后退避险)")
        else:
            logger.success(f"   → 保护充足 (${shallow_protection:.0f} >= ${min_protection})，可以保持 ✅")


def main():
    parser = argparse.ArgumentParser(description="使用真实 API 订单簿数据测试挂单逻辑")
    parser.add_argument("--env-file", type=str, default=".env", help="环境变量文件路径")
    parser.add_argument("--config-file", type=str, default="config.yaml", help="配置文件路径")
    parser.add_argument("--topic-id", type=int, default=None, help="指定测试的市场 ID (默认使用 config 中第一个)")
    parser.add_argument("--min-protection", type=float, default=None, help="覆盖最小保护金额")
    parser.add_argument("--order-amount", type=float, default=None, help="覆盖挂单金额")
    args = parser.parse_args()
    
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="DEBUG")
    
    # 加载环境变量
    if os.path.exists(args.env_file):
        load_dotenv(args.env_file, override=True)
        logger.info(f"已加载环境变量: {args.env_file}")
    else:
        logger.error(f"环境文件不存在: {args.env_file}")
        sys.exit(1)
    
    # 加载配置
    if os.path.exists(args.config_file):
        with open(args.config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    else:
        config = {}
    
    solo_config = config.get('solo_market', {})
    min_protection = args.min_protection or solo_config.get('min_protection_amount', 500)
    order_amount = args.order_amount or solo_config.get('order_amount', 50)
    
    # 确定测试市场 ID
    if args.topic_id:
        topic_ids = [args.topic_id]
    else:
        topic_ids = solo_config.get('topic_ids', [])
    
    if not topic_ids:
        logger.error("未指定市场 ID。使用 --topic-id 参数或在 config.yaml 中配置 topic_ids")
        sys.exit(1)
    
    # 初始化 trader
    private_key = os.getenv('OPINION_PRIVATE_KEY')
    apikey = os.getenv('OPINION_APIKEY')
    wallet_address = os.getenv('OPINION_WALLET_ADDRESS')
    rpc_url = os.getenv('OPINION_RPC_URL', 'https://binance.llamarpc.com')
    
    if not private_key or not apikey:
        logger.error("未找到 OPINION_PRIVATE_KEY 或 OPINION_APIKEY")
        sys.exit(1)
    
    # 代理配置
    proxy_config = config.get('proxy', {})
    proxy = None
    if proxy_config.get('enabled'):
        proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
    
    trader = OpinionTraderSDK(
        private_key=private_key,
        wallet_address=wallet_address,
        apikey=apikey,
        rpc_url=rpc_url,
        proxy=proxy,
    )
    
    logger.info(f"📡 钱包: {trader.wallet_address[:8]}...")
    logger.info(f"🔧 配置: min_protection=${min_protection}, order_amount=${order_amount}")
    logger.info(f"🎯 测试市场: {topic_ids}")
    
    # 测试每个市场
    for topic_id in topic_ids:
        logger.info(f"\n{'='*70}")
        logger.info(f"📡 获取市场 {topic_id} 的订单簿...")
        
        order_book, title = fetch_real_orderbook(trader, topic_id)
        if not order_book:
            logger.error(f"跳过市场 {topic_id}")
            continue
        
        safe_price, safe_rank = test_calculate_safe_price(
            order_book, title, min_protection, order_amount
        )
        
        test_adjustment_scenarios(
            order_book, title, min_protection, order_amount, safe_price
        )
    
    logger.info(f"\n{'='*70}")
    logger.info("🏁 所有测试完成")


if __name__ == "__main__":
    main()
