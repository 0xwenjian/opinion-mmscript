#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opinion 做市机器人 - 综合测试脚本
运行此脚本测试所有模块功能
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level:7}</level> | {message}", level="DEBUG")


class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = []
    
    def run_test(self, name, func):
        logger.info(f"[TEST] {name}")
        try:
            result = func()
            if result is True:
                self.passed += 1
                logger.success(f"  PASS")
                return True
            elif result is None:
                self.skipped += 1
                logger.warning(f"  SKIP")
                return None
            else:
                self.failed += 1
                self.errors.append((name, "返回 False"))
                logger.error(f"  FAIL")
                return False
        except Exception as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            logger.error(f"  FAIL: {e}")
            return False
    
    def summary(self):
        total = self.passed + self.failed + self.skipped
        logger.info("=" * 50)
        logger.info(f"测试结果: {self.passed} 通过 / {self.failed} 失败 / {self.skipped} 跳过 (共 {total})")
        if self.errors:
            logger.warning("失败详情:")
            for name, err in self.errors:
                logger.warning(f"  - {name}: {err}")
        return self.failed == 0


runner = TestRunner()
fetcher = None
trader = None


# ==================== 离线测试 ====================

def test_orderbook_structure():
    """测试订单簿数据结构"""
    from modules.maker_strategy import OrderBook, OrderBookLevel
    
    bids = [OrderBookLevel(price=0.50, size=1000, total=500)]
    asks = [OrderBookLevel(price=0.52, size=1000, total=520)]
    ob = OrderBook(bids=bids, asks=asks, best_bid=0.50, best_ask=0.52, spread=0.02, timestamp=0)
    
    return ob.best_bid == 0.50 and ob.best_ask == 0.52


def test_protection_calculation():
    """测试保护金额计算"""
    from modules.maker_strategy import OrderBook, OrderBookLevel
    
    bids = [
        OrderBookLevel(price=0.50, size=1000, total=500),
        OrderBookLevel(price=0.49, size=2000, total=980),
    ]
    ob = OrderBook(bids=bids, asks=[], best_bid=0.50, best_ask=0.52, spread=0.02, timestamp=0)
    
    protection = ob.get_protection_amount("BUY", 0.49)
    return protection == 500.0


def test_safe_price_calculation():
    """测试安全价格计算"""
    from modules.maker_strategy import OrderBook, OrderBookLevel, MakerStrategy
    
    bids = [OrderBookLevel(price=0.50, size=1000, total=500)]
    asks = [OrderBookLevel(price=0.52, size=1000, total=520)]
    ob = OrderBook(bids=bids, asks=asks, best_bid=0.50, best_ask=0.52, spread=0.02, timestamp=0)
    
    class Mock: pass
    strategy = MakerStrategy(fetcher=Mock(), trader=Mock(), dry_run=True)
    
    buy_price = strategy.calculate_safe_price(ob, "BUY")
    sell_price = strategy.calculate_safe_price(ob, "SELL")
    
    return buy_price and buy_price < 0.50 and sell_price and sell_price > 0.52


def test_price_filter():
    """测试微幅变动过滤"""
    from modules.maker_strategy import MakerStrategy, MakerOrder, OrderStatus
    
    class Mock: pass
    strategy = MakerStrategy(fetcher=Mock(), trader=Mock(), dry_run=True)
    
    order = MakerOrder(
        order_id="test", topic_id=1, title="Test", side="BUY", outcome="YES",
        price=0.50, amount=50, shares=100, create_time=time.time(), status=OrderStatus.OPEN
    )
    
    small_change = strategy.should_adjust_order(order, 0.501)  # 0.2%
    large_change = strategy.should_adjust_order(order, 0.52)   # 4%
    
    return small_change == False and large_change == True


def test_simulated_orderbook():
    """测试模拟订单簿"""
    from modules.maker_strategy import MakerStrategy
    
    class MockFetcher:
        def fetch_market_by_id(self, topic_id):
            return {"topicId": topic_id, "yesPrice": 0.65, "yesBuyPrice": 0.65}
    
    class Mock: pass
    strategy = MakerStrategy(fetcher=MockFetcher(), trader=Mock(), dry_run=True)
    
    ob = strategy._simulate_orderbook(123)
    return ob and ob.best_bid > 0 and ob.best_ask > 0


# ==================== 网络测试 ====================

def test_fetcher_init():
    """测试 Fetcher 初始化"""
    global fetcher
    from modules.fetch_opinion import OpinionFetcher
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    token = os.getenv("OPINION_TOKEN", "")
    
    fetcher = OpinionFetcher(private_key=private_key, token=token, proxy=None)
    return fetcher is not None


def test_get_token():
    """测试获取 Token"""
    if not fetcher:
        return None
    token = fetcher.get_token()
    return token and len(token) > 10


def test_fetch_markets():
    """测试获取市场列表"""
    if not fetcher:
        return None
    markets = fetcher.fetch_markets(limit=10, fetch_all=False)
    logger.info(f"  获取到 {len(markets)} 个市场")
    return len(markets) > 0


def test_trader_init():
    """测试 Trader 初始化"""
    global trader
    from modules.trader_opinion_sdk import OpinionTraderSDK, SDK_AVAILABLE
    
    if not SDK_AVAILABLE:
        logger.warning("  SDK 未安装")
        return None
    
    private_key = os.getenv("OPINION_PRIVATE_KEY", "")
    wallet_address = os.getenv("OPINION_WALLET_ADDRESS", "")
    apikey = os.getenv("OPINION_APIKEY", "")
    rpc_url = os.getenv("OPINION_RPC_URL", "https://binance.llamarpc.com")
    
    trader = OpinionTraderSDK(
        private_key=private_key, wallet_address=wallet_address,
        apikey=apikey, rpc_url=rpc_url, proxy=None
    )
    return trader is not None


def test_get_balance():
    """测试获取余额"""
    if not trader:
        return None
    balance = trader.get_balance()
    if balance is not None:
        logger.info(f"  余额: ${balance:.2f}")
    return balance is not None


def test_get_orders():
    """测试获取订单列表"""
    if not trader:
        return None
    orders = trader.get_my_orders(limit=5)
    if orders is not None:
        logger.info(f"  订单数: {len(orders)}")
    return orders is not None


def test_get_positions():
    """测试获取持仓"""
    if not trader:
        return None
    positions = trader.get_positions()
    if positions is not None:
        logger.info(f"  持仓数: {len(positions)}")
    return positions is not None


# ==================== 挂单撤单测试 ====================

def test_place_and_cancel_order():
    """测试挂单和撤单（只测试二元市场）"""
    if not fetcher or not trader:
        return None
    
    # 获取市场
    markets = fetcher.fetch_markets(limit=50, fetch_all=True)
    test_market = None
    market_id = None
    token_id = None
    
    # 只找二元市场（非多选）
    binary_markets = [m for m in markets if not m.get("isMulti", False)]
    logger.info(f"  找到 {len(binary_markets)} 个二元市场（共 {len(markets)} 个市场）")
    
    for m in binary_markets:
        topic_id = m.get("topicId") or m.get("marketId")
        try:
            market_id = int(topic_id)
            test_market = m
            token_id = m.get("tokenId")
            break
        except (ValueError, TypeError):
            continue
    
    if not test_market or not market_id:
        logger.warning("  未找到二元市场，跳过测试（策略只支持二元市场）")
        return None
    
    current_price = float(test_market.get("yesPrice", 0.5) or 0.5)
    test_price = round(max(0.01, current_price - 0.1), 2)
    
    logger.info(f"  市场: {test_market.get('title', '')[:30]}")
    logger.info(f"  market_id: {market_id}")
    logger.info(f"  挂单价格: {test_price} (当前: {current_price})")
    
    # 挂单 - 二元市场不传 token_id，让 SDK 自动获取正确的 yesTokenId
    result = trader.place_order(
        topic_id=market_id, outcome="YES", amount=15.0,
        price=test_price, order_type=2, side="BUY",
    )
    
    if not result:
        logger.error("  挂单失败")
        return False
    
    logger.success("  挂单成功")
    time.sleep(2)
    
    # 获取订单ID
    order_id = None
    if hasattr(result, 'order_id'):
        order_id = result.order_id
    elif hasattr(result, 'result') and hasattr(result.result, 'order_id'):
        order_id = result.result.order_id
    
    if not order_id:
        orders = trader.get_my_orders(limit=10)
        if orders:
            for order in orders:
                status = str(getattr(order, 'status', '')).lower()
                if status in ['open', 'pending', '1', '2']:
                    order_id = getattr(order, 'order_id', None)
                    break
    
    if not order_id:
        logger.warning("  无法获取订单ID，跳过撤单测试")
        return None
    
    logger.info(f"  订单ID: {order_id}")
    
    # 撤单
    cancel_result = trader.cancel_order(str(order_id))
    if cancel_result:
        logger.success("  撤单成功")
        return True
    else:
        logger.error("  撤单失败")
        return False


# ==================== 主函数 ====================

def main():
    logger.info("=" * 50)
    logger.info("Opinion 做市机器人 - 综合测试")
    logger.info("=" * 50)
    
    # 离线测试
    logger.info("\n[离线测试]")
    runner.run_test("订单簿数据结构", test_orderbook_structure)
    runner.run_test("保护金额计算", test_protection_calculation)
    runner.run_test("安全价格计算", test_safe_price_calculation)
    runner.run_test("微幅变动过滤", test_price_filter)
    runner.run_test("模拟订单簿生成", test_simulated_orderbook)
    
    # 网络测试
    logger.info("\n[网络测试]")
    runner.run_test("Fetcher 初始化", test_fetcher_init)
    runner.run_test("获取 Token", test_get_token)
    runner.run_test("获取市场列表", test_fetch_markets)
    runner.run_test("Trader 初始化", test_trader_init)
    runner.run_test("获取余额", test_get_balance)
    runner.run_test("获取订单列表", test_get_orders)
    runner.run_test("获取持仓", test_get_positions)
    
    # 挂单撤单测试
    logger.info("\n[挂单撤单测试]")
    runner.run_test("挂单和撤单", test_place_and_cancel_order)
    
    # 结果
    logger.info("")
    success = runner.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
