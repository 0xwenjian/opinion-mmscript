#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 fetch_opinion 模块
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>", level="DEBUG")


def test_fetcher_init():
    """测试 Fetcher 初始化"""
    logger.info("=== 测试 Fetcher 初始化 ===")
    
    from modules.fetch_opinion import OpinionFetcher
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    token = os.getenv("OPINION_TOKEN", "")
    
    fetcher = OpinionFetcher(
        private_key=private_key,
        token=token,
        proxy=None,
    )
    
    logger.info(f"Fetcher 初始化成功")
    logger.info(f"Token: {fetcher.token[:20] if fetcher.token else 'None'}...")
    return fetcher


def test_fetch_markets(fetcher):
    """测试获取市场列表"""
    logger.info("=== 测试获取市场列表 ===")
    
    markets = fetcher.fetch_markets(limit=10, fetch_all=False)
    
    logger.info(f"获取到 {len(markets)} 个市场")
    
    if markets:
        for i, m in enumerate(markets[:3]):
            logger.info(f"  [{i+1}] {m.get('title', '')[:50]}")
            logger.info(f"      topicId={m.get('topicId')} yesPrice={m.get('yesPrice')} endTime={m.get('endTime')}")
    
    return markets


def test_fetch_market_by_id(fetcher, topic_id: int):
    """测试获取单个市场详情"""
    logger.info(f"=== 测试获取市场详情 (topicId={topic_id}) ===")
    
    market = fetcher.fetch_market_by_id(topic_id)
    
    if market:
        logger.info(f"市场: {market.get('title', '')[:50]}")
        logger.info(f"  yesBuyPrice: {market.get('yesBuyPrice')}")
        logger.info(f"  yesSellPrice: {market.get('yesSellPrice')}")
        logger.info(f"  spread: {market.get('spread')}")
    else:
        logger.error(f"获取市场 {topic_id} 失败")
    
    return market


def test_fetch_orderbook(fetcher, topic_id: int):
    """测试获取订单簿"""
    logger.info(f"=== 测试获取订单簿 (topicId={topic_id}) ===")
    
    orderbook = fetcher.fetch_orderbook(topic_id)
    
    if orderbook:
        logger.info(f"订单簿获取成功:")
        logger.info(f"  best_bid: {orderbook.get('best_bid')}")
        logger.info(f"  best_ask: {orderbook.get('best_ask')}")
        logger.info(f"  bids 数量: {len(orderbook.get('bids', []))}")
        logger.info(f"  asks 数量: {len(orderbook.get('asks', []))}")
        
        if orderbook.get('bids'):
            logger.info(f"  前3档买单:")
            for i, bid in enumerate(orderbook['bids'][:3]):
                logger.info(f"    [{i+1}] price={bid.get('price')} size={bid.get('size')}")
    else:
        logger.warning(f"订单簿 API 不可用，将使用模拟数据")
    
    return orderbook


def main():
    logger.info("开始测试 fetch_opinion 模块")
    
    try:
        # 测试初始化
        fetcher = test_fetcher_init()
        
        # 测试获取市场列表
        markets = test_fetch_markets(fetcher)
        
        # 测试获取单个市场
        topic_id = None
        if markets:
            topic_id = markets[0].get("topicId")
            if topic_id:
                test_fetch_market_by_id(fetcher, int(topic_id))
        
        # 测试获取订单簿
        if topic_id:
            test_fetch_orderbook(fetcher, int(topic_id))
        
        logger.success("fetch_opinion 模块测试完成")
        
    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
