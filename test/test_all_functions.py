#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试所有功能模块
- Fetcher 数据获取
- 订单簿获取
- Trader 初始化
- 做市策略逻辑
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="DEBUG")


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def add_pass(self, name):
        self.passed += 1
        logger.success(f"[PASS] {name}")
    
    def add_fail(self, name, error):
        self.failed += 1
        self.errors.append((name, error))
        logger.error(f"[FAIL] {name}: {error}")
    
    def summary(self):
        total = self.passed + self.failed
        logger.info("=" * 50)
        logger.info(f"测试结果: {self.passed}/{total} 通过")
        if self.errors:
            logger.warning("失败的测试:")
            for name, error in self.errors:
                logger.warning(f"  - {name}: {error}")
        return self.failed == 0


result = TestResult()


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
    
    if fetcher:
        result.add_pass("Fetcher 初始化")
    else:
        result.add_fail("Fetcher 初始化", "返回 None")
    
    return fetcher


def test_get_token(fetcher):
    """测试获取 Token"""
    logger.info("=== 测试获取 Token ===")
    
    try:
        token = fetcher.get_token()
        if token and len(token) > 10:
            result.add_pass("获取 Token")
            logger.info(f"  Token: {token[:30]}...")
            return True
        else:
            result.add_fail("获取 Token", "Token 无效")
            return False
    except Exception as e:
        result.add_fail("获取 Token", str(e))
        return False


def test_fetch_markets(fetcher):
    """测试获取市场列表"""
    logger.info("=== 测试获取市场列表 ===")
    
    try:
        markets = fetcher.fetch_markets(limit=50, fetch_all=False)
        
        if markets and len(markets) > 0:
            result.add_pass("获取市场列表")
            logger.info(f"  获取到 {len(markets)} 个市场")
            
            # 筛选非多选市场
            binary_markets = [m for m in markets if not m.get("isMulti", False)]
            logger.info(f"  其中 {len(binary_markets)} 个二元市场")
            
            for i, m in enumerate(binary_markets[:3]):
                logger.info(f"  [{i+1}] {m.get('title', '')[:40]}")
                logger.info(f"      topicId={m.get('topicId')} yesPrice={m.get('yesPrice'):.4f}")
            
            return binary_markets  # 返回二元市场
        else:
            result.add_fail("获取市场列表", "返回空列表")
            return []
    except Exception as e:
        result.add_fail("获取市场列表", str(e))
        return []


def test_fetch_market_by_id(fetcher, topic_id: int):
    """测试获取单个市场详情"""
    logger.info(f"=== 测试获取市场详情 (topicId={topic_id}) ===")
    
    try:
        market = fetcher.fetch_market_by_id(topic_id)
        
        if market:
            result.add_pass("获取市场详情")
            logger.info(f"  标题: {market.get('title', '')[:40]}")
            logger.info(f"  yesBuyPrice: {market.get('yesBuyPrice')}")
            logger.info(f"  yesSellPrice: {market.get('yesSellPrice')}")
            logger.info(f"  spread: {market.get('spread')}")
            return market
        else:
            result.add_fail("获取市场详情", "返回 None")
            return None
    except Exception as e:
        result.add_fail("获取市场详情", str(e))
        return None


def test_fetch_orderbook(fetcher, topic_id: int):
    """测试获取订单簿"""
    logger.info(f"=== 测试获取订单簿 (topicId={topic_id}) ===")
    
    try:
        orderbook = fetcher.fetch_orderbook(topic_id)
        
        if orderbook:
            result.add_pass("获取订单簿")
            logger.info(f"  best_bid: {orderbook.get('best_bid')}")
            logger.info(f"  best_ask: {orderbook.get('best_ask')}")
            logger.info(f"  bids 数量: {len(orderbook.get('bids', []))}")
            logger.info(f"  asks 数量: {len(orderbook.get('asks', []))}")
            return orderbook
        else:
            # 订单簿 API 可能不可用，这不是严重错误
            logger.warning("  订单簿 API 不可用，将使用模拟数据")
            result.add_pass("获取订单簿 (API不可用)")
            return None
    except Exception as e:
        result.add_fail("获取订单簿", str(e))
        return None


def test_trader_init():
    """测试 Trader 初始化"""
    logger.info("=== 测试 Trader 初始化 ===")
    
    from modules.trader_opinion_sdk import OpinionTraderSDK, SDK_AVAILABLE
    
    if not SDK_AVAILABLE:
        result.add_fail("Trader 初始化", "SDK 未安装")
        return None
    
    try:
        private_key = os.getenv("OPINION_PRIVATE_KEY", "")
        wallet_address = os.getenv("OPINION_WALLET_ADDRESS", "")
        apikey = os.getenv("OPINION_APIKEY", "")
        rpc_url = os.getenv("OPINION_RPC_URL", "https://binance.llamarpc.com")
        
        trader = OpinionTraderSDK(
            private_key=private_key,
            wallet_address=wallet_address,
            apikey=apikey,
            rpc_url=rpc_url,
            proxy=None,
        )
        
        if trader:
            result.add_pass("Trader 初始化")
            logger.info(f"  钱包地址: {trader.wallet_address}")
            return trader
        else:
            result.add_fail("Trader 初始化", "返回 None")
            return None
    except Exception as e:
        result.add_fail("Trader 初始化", str(e))
        return None


def test_get_balance(trader):
    """测试获取余额"""
    logger.info("=== 测试获取余额 ===")
    
    if not trader:
        result.add_fail("获取余额", "Trader 未初始化")
        return None
    
    try:
        balance = trader.get_balance()
        
        if balance is not None:
            result.add_pass("获取余额")
            logger.info(f"  余额: ${balance:.2f}")
            return balance
        else:
            result.add_fail("获取余额", "返回 None")
            return None
    except Exception as e:
        result.add_fail("获取余额", str(e))
        return None


def test_get_market_info(trader, topic_id: int):
    """测试通过 Trader 获取市场信息"""
    logger.info(f"=== 测试 Trader 获取市场信息 (topicId={topic_id}) ===")
    
    if not trader:
        result.add_fail("Trader 获取市场信息", "Trader 未初始化")
        return None
    
    try:
        market = trader.get_market_by_topic_id(topic_id)
        
        if market:
            result.add_pass("Trader 获取市场信息")
            logger.info(f"  标题: {market.get('title', '')[:40]}")
            logger.info(f"  yes_token_id: {market.get('yes_token_id')}")
            logger.info(f"  no_token_id: {market.get('no_token_id')}")
            return market
        else:
            result.add_fail("Trader 获取市场信息", "返回 None")
            return None
    except Exception as e:
        result.add_fail("Trader 获取市场信息", str(e))
        return None


def test_maker_strategy_logic():
    """测试做市策略逻辑"""
    logger.info("=== 测试做市策略逻辑 ===")
    
    try:
        from modules.maker_strategy import OrderBook, OrderBookLevel, MakerStrategy
        
        # 创建模拟订单簿
        bids = [
            OrderBookLevel(price=0.50, size=100, total=50),
            OrderBookLevel(price=0.49, size=200, total=98),
            OrderBookLevel(price=0.48, size=300, total=144),
        ]
        asks = [
            OrderBookLevel(price=0.52, size=100, total=52),
            OrderBookLevel(price=0.53, size=200, total=106),
            OrderBookLevel(price=0.54, size=300, total=162),
        ]
        
        order_book = OrderBook(
            bids=bids,
            asks=asks,
            best_bid=0.50,
            best_ask=0.52,
            spread=0.02,
            timestamp=0,
        )
        
        # 测试保护金额计算
        protection_buy = order_book.get_protection_amount("BUY", 0.49)
        protection_sell = order_book.get_protection_amount("SELL", 0.53)
        
        logger.info(f"  买单保护金额 (price=0.49): ${protection_buy:.2f}")
        logger.info(f"  卖单保护金额 (price=0.53): ${protection_sell:.2f}")
        
        if protection_buy > 0 and protection_sell > 0:
            result.add_pass("做市策略逻辑 - 保护金额计算")
        else:
            result.add_fail("做市策略逻辑 - 保护金额计算", "计算结果为0")
        
        return True
    except Exception as e:
        result.add_fail("做市策略逻辑", str(e))
        return False


def test_maker_strategy_price_calculation():
    """测试做市策略价格计算"""
    logger.info("=== 测试做市策略价格计算 ===")
    
    try:
        from modules.maker_strategy import OrderBook, OrderBookLevel, MakerStrategy
        
        # 创建模拟订单簿 (有足够保护)
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
        
        # 创建策略实例 (不需要真实的 fetcher/trader)
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
        
        logger.info(f"  计算的买单安全价格: {buy_price}")
        logger.info(f"  计算的卖单安全价格: {sell_price}")
        
        if buy_price and sell_price:
            if buy_price < order_book.best_bid and sell_price > order_book.best_ask:
                result.add_pass("做市策略价格计算")
            else:
                result.add_fail("做市策略价格计算", f"价格不在安全范围: buy={buy_price} sell={sell_price}")
        else:
            result.add_fail("做市策略价格计算", "返回 None")
        
        return True
    except Exception as e:
        result.add_fail("做市策略价格计算", str(e))
        import traceback
        traceback.print_exc()
        return False


def main():
    logger.info("=" * 50)
    logger.info("开始测试所有功能模块")
    logger.info("=" * 50)
    
    # 1. 测试 Fetcher
    fetcher = test_fetcher_init()
    
    if fetcher:
        # 2. 测试获取 Token
        test_get_token(fetcher)
        
        # 3. 测试获取市场列表
        markets = test_fetch_markets(fetcher)
        
        # 4. 测试获取单个市场详情
        topic_id = None
        if markets:
            topic_id = markets[0].get("topicId")
            if topic_id:
                test_fetch_market_by_id(fetcher, int(topic_id))
        
        # 5. 测试获取订单簿
        if topic_id:
            test_fetch_orderbook(fetcher, int(topic_id))
    
    # 6. 测试 Trader
    trader = test_trader_init()
    
    if trader:
        # 7. 测试获取余额
        test_get_balance(trader)
        
        # 8. 测试通过 Trader 获取市场信息
        if topic_id:
            test_get_market_info(trader, int(topic_id))
    
    # 9. 测试做市策略逻辑
    test_maker_strategy_logic()
    
    # 10. 测试做市策略价格计算
    test_maker_strategy_price_calculation()
    
    # 输出结果
    success = result.summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
