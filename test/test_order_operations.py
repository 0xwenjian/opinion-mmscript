#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试挂单和撤单功能
警告: 此脚本会进行真实的挂单和撤单操作！
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="DEBUG")


# 测试配置
TEST_AMOUNT = 1.0  # 测试挂单金额 (USD) - 使用最小金额
TEST_PRICE_OFFSET = 0.05  # 价格偏移 - 确保不会成交


def get_fetcher():
    """获取 Fetcher 实例"""
    from modules.fetch_opinion import OpinionFetcher
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    token = os.getenv("OPINION_TOKEN", "")
    
    return OpinionFetcher(
        private_key=private_key,
        token=token,
        proxy=None,
    )


def get_trader():
    """获取 Trader 实例"""
    from modules.trader_opinion_sdk import OpinionTraderSDK, SDK_AVAILABLE
    
    if not SDK_AVAILABLE:
        logger.error("SDK 未安装，请运行: pip install opinion-clob-sdk")
        return None
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    wallet_address = os.getenv("OPINION_WALLET_ADDRESS", "")
    apikey = os.getenv("OPINION_APIKEY", "")
    rpc_url = os.getenv("OPINION_RPC_URL", "https://binance.llamarpc.com")
    
    return OpinionTraderSDK(
        private_key=private_key,
        wallet_address=wallet_address,
        apikey=apikey,
        rpc_url=rpc_url,
        proxy=None,
    )


def find_test_market(fetcher):
    """找一个适合测试的市场"""
    logger.info("=== 寻找测试市场 ===")
    
    markets = fetcher.fetch_markets(limit=20, fetch_all=False)
    
    for m in markets:
        if m.get("isMulti", False):
            continue
        
        topic_id = m.get("topicId")
        yes_price = float(m.get("yesPrice", 0) or 0)
        volume = float(m.get("volume", 0) or 0)
        
        # 选择价格在 0.3-0.7 之间、交易量较大的市场
        if 0.3 <= yes_price <= 0.7 and volume >= 10000:
            logger.info(f"选择测试市场:")
            logger.info(f"  标题: {m.get('title', '')[:50]}")
            logger.info(f"  topicId: {topic_id}")
            logger.info(f"  yesPrice: {yes_price}")
            logger.info(f"  volume: ${volume:.0f}")
            return {
                "topic_id": int(topic_id),
                "title": m.get("title", ""),
                "yes_price": yes_price,
            }
    
    logger.error("未找到合适的测试市场")
    return None


def test_place_order(trader, topic_id: int, price: float):
    """测试挂单"""
    logger.info("=== 测试挂单 ===")
    logger.info(f"  topic_id: {topic_id}")
    logger.info(f"  方向: BUY YES")
    logger.info(f"  价格: {price}")
    logger.info(f"  金额: ${TEST_AMOUNT}")
    
    try:
        result = trader.place_order(
            topic_id=topic_id,
            outcome="YES",
            amount=TEST_AMOUNT,
            price=price,
            order_type=2,  # 限价单
            side="BUY",
        )
        
        if result:
            logger.success("挂单成功!")
            logger.info(f"  返回结果: {result}")
            
            # 尝试提取 order_id
            order_id = None
            if hasattr(result, 'order_id'):
                order_id = result.order_id
            elif hasattr(result, 'result') and hasattr(result.result, 'order_id'):
                order_id = result.result.order_id
            
            if order_id:
                logger.info(f"  order_id: {order_id}")
            
            return result, order_id
        else:
            logger.error("挂单失败: 返回 None")
            return None, None
            
    except Exception as e:
        logger.error(f"挂单异常: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def test_get_orders(trader):
    """测试获取订单列表"""
    logger.info("=== 测试获取订单列表 ===")
    
    try:
        orders = trader.get_my_orders(limit=10)
        
        if orders is not None:
            logger.success(f"获取到 {len(orders)} 个订单")
            
            for i, order in enumerate(orders[:5]):
                order_id = getattr(order, 'order_id', 'N/A')
                status = getattr(order, 'status', 'N/A')
                side = getattr(order, 'side', 'N/A')
                price = getattr(order, 'price', 'N/A')
                logger.info(f"  [{i+1}] id={order_id} status={status} side={side} price={price}")
            
            return orders
        else:
            logger.error("获取订单列表失败")
            return None
            
    except Exception as e:
        logger.error(f"获取订单列表异常: {e}")
        return None


def test_cancel_order(trader, order_id: str):
    """测试撤单"""
    logger.info(f"=== 测试撤单 (order_id={order_id}) ===")
    
    try:
        result = trader.cancel_order(order_id)
        
        if result:
            logger.success("撤单成功!")
            return True
        else:
            logger.error("撤单失败")
            return False
            
    except Exception as e:
        logger.error(f"撤单异常: {e}")
        return False


def test_get_positions(trader, topic_id: int = None):
    """测试获取持仓"""
    logger.info("=== 测试获取持仓 ===")
    
    try:
        positions = trader.get_positions(topic_id)
        
        if positions is not None:
            logger.success(f"获取到 {len(positions)} 个持仓")
            
            for i, pos in enumerate(positions[:5]):
                market_id = getattr(pos, 'market_id', 'N/A')
                outcome_side = getattr(pos, 'outcome_side', 'N/A')
                shares = getattr(pos, 'shares_owned', 'N/A')
                logger.info(f"  [{i+1}] market={market_id} side={outcome_side} shares={shares}")
            
            return positions
        else:
            logger.error("获取持仓失败")
            return None
            
    except Exception as e:
        logger.error(f"获取持仓异常: {e}")
        return None


def main():
    logger.info("=" * 50)
    logger.info("测试挂单和撤单功能")
    logger.info("警告: 此脚本会进行真实操作!")
    logger.info("=" * 50)
    
    # 初始化
    fetcher = get_fetcher()
    trader = get_trader()
    
    if not trader:
        return 1
    
    # 获取余额
    logger.info("=== 检查账户余额 ===")
    balance = trader.get_balance()
    if balance is not None:
        logger.info(f"  当前余额: ${balance:.2f}")
    else:
        logger.warning("无法获取余额")
    logger.info("  继续测试...")
    
    # 找测试市场
    market = find_test_market(fetcher)
    if not market:
        return 1
    
    topic_id = market["topic_id"]
    current_price = market["yes_price"]
    
    # 计算安全的测试价格 (远低于当前价格，确保不会成交)
    test_price = round(current_price - TEST_PRICE_OFFSET, 2)
    test_price = max(0.01, test_price)  # 确保价格 > 0
    
    logger.info(f"\n测试价格: {test_price} (当前价格: {current_price})")
    
    # 测试挂单
    order_result, order_id = test_place_order(trader, topic_id, test_price)
    
    if not order_result:
        logger.error("挂单测试失败，跳过后续测试")
        return 1
    
    # 等待一下
    logger.info("\n等待 3 秒...")
    time.sleep(3)
    
    # 测试获取订单列表
    orders = test_get_orders(trader)
    
    # 如果没有从挂单结果获取到 order_id，尝试从订单列表获取
    if not order_id and orders:
        for order in orders:
            status = str(getattr(order, 'status', '')).lower()
            if status in ['open', 'pending', '1', '2']:
                order_id = getattr(order, 'order_id', None)
                if order_id:
                    logger.info(f"从订单列表获取 order_id: {order_id}")
                    break
    
    # 测试获取持仓
    test_get_positions(trader, topic_id)
    
    # 测试撤单
    if order_id:
        logger.info(f"\n准备撤单: {order_id}")
        time.sleep(2)
        cancel_result = test_cancel_order(trader, str(order_id))
        
        if cancel_result:
            logger.success("\n挂单和撤单测试全部通过!")
        else:
            logger.warning("\n撤单可能失败，请手动检查")
    else:
        logger.warning("\n无法获取 order_id，请手动撤单!")
        logger.warning("可以运行 cancel_orders.py 撤销所有挂单")
    
    # 最终余额
    logger.info("\n=== 最终余额 ===")
    final_balance = trader.get_balance()
    if final_balance is not None:
        logger.info(f"  余额: ${final_balance:.2f}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
