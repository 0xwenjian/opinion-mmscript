#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线测试订单簿逻辑（不需要网络连接）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="DEBUG")


def test_orderbook_logic():
    """测试订单簿数据结构和计算逻辑"""
    logger.info("=== 测试订单簿数据结构 ===")
    
    from modules.maker_strategy import OrderBook, OrderBookLevel
    
    # 创建模拟订单簿
    bids = [
        OrderBookLevel(price=0.50, size=1000, total=500),
        OrderBookLevel(price=0.49, size=2000, total=980),
        OrderBookLevel(price=0.48, size=3000, total=1440),
        OrderBookLevel(price=0.47, size=4000, total=1880),
        OrderBookLevel(price=0.46, size=5000, total=2300),
    ]
    asks = [
        OrderBookLevel(price=0.52, size=1000, total=520),
        OrderBookLevel(price=0.53, size=2000, total=1060),
        OrderBookLevel(price=0.54, size=3000, total=1620),
        OrderBookLevel(price=0.55, size=4000, total=2200),
        OrderBookLevel(price=0.56, size=5000, total=2800),
    ]
    
    order_book = OrderBook(
        bids=bids,
        asks=asks,
        best_bid=0.50,
        best_ask=0.52,
        spread=0.02,
        timestamp=0,
    )
    
    logger.success("订单簿创建成功")
    logger.info(f"  best_bid: {order_book.best_bid}")
    logger.info(f"  best_ask: {order_book.best_ask}")
    logger.info(f"  spread: {order_book.spread}")
    logger.info(f"  bids 数量: {len(order_book.bids)}")
    logger.info(f"  asks 数量: {len(order_book.asks)}")
    
    return order_book


def test_protection_calculation(order_book):
    """测试前方保护金额计算"""
    logger.info("\n=== 测试前方保护金额计算 ===")
    
    # 测试买单保护
    test_prices = [0.49, 0.48, 0.47, 0.46]
    logger.info("买单保护金额 (目标价位前方的累计挂单):")
    for price in test_prices:
        protection = order_book.get_protection_amount("BUY", price)
        logger.info(f"  price={price:.2f} -> 保护金额=${protection:.2f}")
    
    # 测试卖单保护
    test_prices = [0.53, 0.54, 0.55, 0.56]
    logger.info("卖单保护金额:")
    for price in test_prices:
        protection = order_book.get_protection_amount("SELL", price)
        logger.info(f"  price={price:.2f} -> 保护金额=${protection:.2f}")
    
    return True


def test_safe_price_calculation():
    """测试安全价格计算"""
    logger.info("\n=== 测试安全价格计算 ===")
    
    from modules.maker_strategy import OrderBook, OrderBookLevel, MakerStrategy
    
    # 创建有足够保护的订单簿
    bids = [
        OrderBookLevel(price=0.50, size=1000, total=500),
        OrderBookLevel(price=0.495, size=2000, total=990),
        OrderBookLevel(price=0.49, size=3000, total=1470),
    ]
    asks = [
        OrderBookLevel(price=0.52, size=1000, total=520),
        OrderBookLevel(price=0.525, size=2000, total=1050),
        OrderBookLevel(price=0.53, size=3000, total=1590),
    ]
    
    order_book = OrderBook(
        bids=bids,
        asks=asks,
        best_bid=0.50,
        best_ask=0.52,
        spread=0.02,
        timestamp=0,
    )
    
    # 创建策略实例
    class MockFetcher:
        pass
    class MockTrader:
        pass
    
    strategy = MakerStrategy(
        fetcher=MockFetcher(),
        trader=MockTrader(),
        dashboard=None,
        dry_run=True,
    )
    
    # 测试安全价格计算
    buy_price = strategy.calculate_safe_price(order_book, "BUY")
    sell_price = strategy.calculate_safe_price(order_book, "SELL")
    
    logger.info(f"  best_bid: {order_book.best_bid}")
    logger.info(f"  best_ask: {order_book.best_ask}")
    logger.info(f"  计算的买单安全价格: {buy_price}")
    logger.info(f"  计算的卖单安全价格: {sell_price}")
    
    # 验证
    if buy_price and buy_price < order_book.best_bid:
        logger.success("买单安全价格正确 (低于 best_bid)")
    else:
        logger.error("买单安全价格错误")
        return False
    
    if sell_price and sell_price > order_book.best_ask:
        logger.success("卖单安全价格正确 (高于 best_ask)")
    else:
        logger.error("卖单安全价格错误")
        return False
    
    return True


def test_price_change_filter():
    """测试微幅变动过滤"""
    logger.info("\n=== 测试微幅变动过滤 ===")
    
    from modules.maker_strategy import MakerStrategy, MakerOrder, OrderStatus
    import time
    
    class MockFetcher:
        pass
    class MockTrader:
        pass
    
    strategy = MakerStrategy(
        fetcher=MockFetcher(),
        trader=MockTrader(),
        dashboard=None,
        dry_run=True,
    )
    
    # 创建模拟订单
    order = MakerOrder(
        order_id="test_001",
        topic_id=1,
        title="Test Market",
        side="BUY",
        outcome="YES",
        price=0.50,
        amount=50.0,
        shares=100.0,
        create_time=time.time(),
        status=OrderStatus.OPEN,
    )
    
    # 测试小幅变动 (不应调整)
    small_change_price = 0.501  # 0.2% 变化
    should_adjust = strategy.should_adjust_order(order, small_change_price)
    logger.info(f"  原价格: {order.price}, 新价格: {small_change_price}")
    logger.info(f"  变化: {abs(small_change_price - order.price) / order.price * 100:.2f}%")
    logger.info(f"  是否调整: {should_adjust}")
    
    if not should_adjust:
        logger.success("小幅变动正确过滤 (不调整)")
    else:
        logger.error("小幅变动过滤失败")
        return False
    
    # 测试大幅变动 (应该调整)
    large_change_price = 0.52  # 4% 变化
    should_adjust = strategy.should_adjust_order(order, large_change_price)
    logger.info(f"  原价格: {order.price}, 新价格: {large_change_price}")
    logger.info(f"  变化: {abs(large_change_price - order.price) / order.price * 100:.2f}%")
    logger.info(f"  是否调整: {should_adjust}")
    
    if should_adjust:
        logger.success("大幅变动正确触发调整")
    else:
        logger.error("大幅变动过滤失败")
        return False
    
    return True


def test_simulated_orderbook():
    """测试模拟订单簿生成"""
    logger.info("\n=== 测试模拟订单簿生成 ===")
    
    from modules.maker_strategy import MakerStrategy
    
    # 创建 Mock Fetcher
    class MockFetcher:
        def fetch_market_by_id(self, topic_id):
            return {
                "topicId": topic_id,
                "title": "Test Market",
                "yesPrice": 0.65,
                "yesBuyPrice": 0.65,
                "yesSellPrice": 0.66,
            }
    
    class MockTrader:
        pass
    
    strategy = MakerStrategy(
        fetcher=MockFetcher(),
        trader=MockTrader(),
        dashboard=None,
        dry_run=True,
    )
    
    # 生成模拟订单簿
    simulated_ob = strategy._simulate_orderbook(123)
    
    if simulated_ob:
        logger.success("模拟订单簿生成成功")
        logger.info(f"  best_bid: {simulated_ob.best_bid:.4f}")
        logger.info(f"  best_ask: {simulated_ob.best_ask:.4f}")
        logger.info(f"  spread: {simulated_ob.spread:.4f}")
        logger.info(f"  bids 数量: {len(simulated_ob.bids)}")
        logger.info(f"  asks 数量: {len(simulated_ob.asks)}")
        
        # 验证保护金额
        protection = simulated_ob.get_protection_amount("BUY", simulated_ob.best_bid - 0.01)
        logger.info(f"  买单保护金额: ${protection:.2f}")
        
        return True
    else:
        logger.error("模拟订单簿生成失败")
        return False


def main():
    logger.info("=" * 50)
    logger.info("离线测试订单簿逻辑")
    logger.info("=" * 50)
    
    all_passed = True
    
    # 测试订单簿数据结构
    order_book = test_orderbook_logic()
    if not order_book:
        all_passed = False
    
    # 测试保护金额计算
    if order_book:
        if not test_protection_calculation(order_book):
            all_passed = False
    
    # 测试安全价格计算
    if not test_safe_price_calculation():
        all_passed = False
    
    # 测试微幅变动过滤
    if not test_price_change_filter():
        all_passed = False
    
    # 测试模拟订单簿
    if not test_simulated_orderbook():
        all_passed = False
    
    # 结果
    logger.info("\n" + "=" * 50)
    if all_passed:
        logger.success("所有订单簿逻辑测试通过!")
        return 0
    else:
        logger.error("部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
