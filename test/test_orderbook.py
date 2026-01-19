#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专门测试订单簿获取功能
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


def test_orderbook():
    """测试订单簿获取"""
    from modules.fetch_opinion import OpinionFetcher
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    token = os.getenv("OPINION_TOKEN", "")
    
    logger.info("初始化 Fetcher...")
    fetcher = OpinionFetcher(
        private_key=private_key,
        token=token,
        proxy=None,
    )
    
    # 获取市场列表
    logger.info("获取市场列表...")
    time.sleep(1)  # 避免请求过快
    
    try:
        markets = fetcher.fetch_markets(limit=20, fetch_all=False)
    except Exception as e:
        logger.error(f"获取市场列表失败: {e}")
        logger.info("等待 5 秒后重试...")
        time.sleep(5)
        markets = fetcher.fetch_markets(limit=20, fetch_all=False)
    
    logger.info(f"获取到 {len(markets)} 个市场")
    
    # 找一个二元市场测试
    test_market = None
    for m in markets:
        if not m.get("isMulti", False):
            test_market = m
            break
    
    if not test_market:
        # 如果没有二元市场，用第一个市场测试
        if markets:
            test_market = markets[0]
            logger.warning("没有找到二元市场，使用第一个市场测试")
    
    if not test_market:
        logger.error("没有可用的市场进行测试")
        return False
    
    topic_id = test_market.get("topicId")
    logger.info(f"测试市场: {test_market.get('title', '')[:40]}")
    logger.info(f"  topicId: {topic_id}")
    logger.info(f"  yesPrice: {test_market.get('yesPrice')}")
    
    # 测试订单簿获取
    logger.info("\n=== 测试订单簿 API ===")
    time.sleep(1)
    
    orderbook = fetcher.fetch_orderbook(int(topic_id))
    
    if orderbook:
        logger.success("订单簿 API 可用!")
        logger.info(f"  best_bid: {orderbook.get('best_bid')}")
        logger.info(f"  best_ask: {orderbook.get('best_ask')}")
        logger.info(f"  spread: {orderbook.get('best_ask', 0) - orderbook.get('best_bid', 0):.4f}")
        logger.info(f"  bids 数量: {len(orderbook.get('bids', []))}")
        logger.info(f"  asks 数量: {len(orderbook.get('asks', []))}")
        
        if orderbook.get('bids'):
            logger.info("  买单 (bids):")
            for i, bid in enumerate(orderbook['bids'][:5]):
                logger.info(f"    [{i+1}] price={bid.get('price'):.4f} size={bid.get('size'):.2f}")
        
        if orderbook.get('asks'):
            logger.info("  卖单 (asks):")
            for i, ask in enumerate(orderbook['asks'][:5]):
                logger.info(f"    [{i+1}] price={ask.get('price'):.4f} size={ask.get('size'):.2f}")
        
        return True
    else:
        logger.warning("订单簿 API 不可用")
        logger.info("做市策略将使用模拟订单簿数据")
        
        # 测试模拟订单簿
        logger.info("\n=== 测试模拟订单簿 ===")
        from modules.maker_strategy import MakerStrategy
        
        class MockTrader:
            pass
        
        strategy = MakerStrategy(
            fetcher=fetcher,
            trader=MockTrader(),
            dashboard=None,
            dry_run=True,
        )
        
        simulated_ob = strategy._simulate_orderbook(int(topic_id))
        
        if simulated_ob:
            logger.success("模拟订单簿生成成功!")
            logger.info(f"  best_bid: {simulated_ob.best_bid:.4f}")
            logger.info(f"  best_ask: {simulated_ob.best_ask:.4f}")
            logger.info(f"  spread: {simulated_ob.spread:.4f}")
            logger.info(f"  bids 数量: {len(simulated_ob.bids)}")
            logger.info(f"  asks 数量: {len(simulated_ob.asks)}")
            
            # 测试保护金额计算
            protection = simulated_ob.get_protection_amount("BUY", simulated_ob.best_bid - 0.01)
            logger.info(f"  买单保护金额: ${protection:.2f}")
            
            return True
        else:
            logger.error("模拟订单簿生成失败")
            return False


def main():
    logger.info("=" * 50)
    logger.info("测试订单簿获取功能")
    logger.info("=" * 50)
    
    try:
        success = test_orderbook()
        
        if success:
            logger.success("\n订单簿测试通过!")
            return 0
        else:
            logger.error("\n订单簿测试失败")
            return 1
            
    except Exception as e:
        logger.error(f"测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
