#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线测试 calculate_safe_price + check_and_adjust_order 逻辑
使用模拟的真实订单簿数据，无需网络连接

用法:
    python3 test/test_safe_price_offline.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="INFO")

from modules.models import OrderBook, OrderBookLevel


def make_orderbook(bid_data: list[tuple[float, float]]) -> OrderBook:
    """
    从 (price, total_dollar) 列表创建 OrderBook
    total_dollar 是该档位的总金额（price * size）
    """
    bids = []
    for price, total in bid_data:
        size = total / price if price > 0 else 0
        bids.append(OrderBookLevel(price=price, size=size, total=total))
    bids.sort(key=lambda x: x.price, reverse=True)
    
    best_bid = bids[0].price if bids else 0
    return OrderBook(bids=bids, asks=[], best_bid=best_bid, best_ask=1.0)


def calculate_safe_price(order_book: OrderBook, min_protection: float):
    """复制 solomarket.py 中的 calculate_safe_price 逻辑"""
    if not order_book or not order_book.bids:
        return None
    
    cumulative_total = 0.0
    for i, level in enumerate(order_book.bids):
        estimated_rank = i + 2
        cumulative_total += level.total
        if cumulative_total >= min_protection:
            target_price = level.price - 0.001
            if target_price < 0.01:
                target_price = 0.01
            return round(target_price, 4), estimated_rank
    
    return None


def get_protection_at_price(order_book: OrderBook, price: float) -> float:
    """计算某个价格的前方保护金额"""
    protection = 0.0
    for level in order_book.bids:
        if level.price > price + 0.00001:
            protection += level.total
        else:
            break
    return protection


def print_orderbook(order_book: OrderBook, min_protection: float, label: str = ""):
    """打印订单簿"""
    if label:
        logger.info(f"\n📊 {label}")
    
    cumulative = 0.0
    safe_found = False
    for i, level in enumerate(order_book.bids):
        cumulative += level.total
        marker = ""
        if cumulative >= min_protection and not safe_found:
            safe_found = True
            marker = " ✅ 安全位"
        logger.info(f"   买{i+1:>2}: {level.price:.4f} (${level.total:>7.0f} | 累计: ${cumulative:>7.0f}){marker}")


# ============================================================
# 测试用例
# ============================================================

def test_1_basic_protection():
    """场景 1: 买1足够厚，直接挂买2"""
    logger.info("=" * 60)
    logger.info("🧪 场景 1: 买1足够厚 ($2000)，直接挂买2")
    logger.info("   配置: min_protection = $500")
    
    ob = make_orderbook([
        (0.6260, 2000),  # 买1: $2000
        (0.6250, 800),   # 买2
        (0.6240, 450),   # 买3
    ])
    min_p = 500
    print_orderbook(ob, min_p)
    
    result = calculate_safe_price(ob, min_p)
    assert result is not None, "应该找到安全价格"
    price, rank = result
    
    assert price == 0.6250, f"应挂 0.6250 (买1价-0.001)，实际 {price}"
    assert rank == 2, f"应在买2，实际买{rank}"
    
    protection = get_protection_at_price(ob, price)
    assert protection >= min_p, f"保护 ${protection} 应 >= ${min_p}"
    
    logger.success(f"✅ 通过: 挂 {price:.4f} (买{rank}), 前方保护 ${protection:.0f}")
    return True


def test_2_thin_book_deep_placement():
    """场景 2: 同一个订单簿，不同保护金额的对比"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 2: 同一盘口，不同 min_protection 的挂单对比")
    
    # 模拟真实的 3039 (Israel/Iran) 市场数据
    ob = make_orderbook([
        (0.3640, 11),    # 买1
        (0.3620, 100),   # 买2
        (0.3610, 33),    # 买3
        (0.3600, 30),    # 买4
        (0.3550, 33),    # 买5
        (0.3510, 679),   # 买6: 累计 $886
        (0.3500, 74),    # 买7: 累计 $960
        (0.3490, 191),   # 买8: 累计 $1151
        (0.3480, 30),    # 买9
        (0.3440, 30),    # 买10
    ])
    
    # --- 子测试 A: min_protection = $500 ---
    logger.info("\n   📍 A) min_protection = $500")
    print_orderbook(ob, 500)
    
    result_500 = calculate_safe_price(ob, 500)
    assert result_500 is not None
    price_500, rank_500 = result_500
    
    assert price_500 == 0.3500, f"$500: 应挂 0.3500 (买6价-0.001)，实际 {price_500}"
    assert rank_500 == 7, f"$500: 应在买7，实际买{rank_500}"
    
    protection_500 = get_protection_at_price(ob, price_500)
    logger.success(f"   ✅ $500: 挂 {price_500:.4f} (买{rank_500}), 前方保护 ${protection_500:.0f}")
    
    # --- 子测试 B: min_protection = $1111 ---
    logger.info("\n   📍 B) min_protection = $1111")
    print_orderbook(ob, 1111)
    
    result_1111 = calculate_safe_price(ob, 1111)
    assert result_1111 is not None
    price_1111, rank_1111 = result_1111
    
    assert price_1111 == 0.3480, f"$1111: 应挂 0.3480 (买8价-0.001)，实际 {price_1111}"
    assert rank_1111 == 9, f"$1111: 应在买9，实际买{rank_1111}"
    
    protection_1111 = get_protection_at_price(ob, price_1111)
    logger.success(f"   ✅ $1111: 挂 {price_1111:.4f} (买{rank_1111}), 前方保护 ${protection_1111:.0f}")
    
    # 对比
    logger.info(f"\n   📊 对比: $500→买{rank_500}@{price_500:.4f} | $1111→买{rank_1111}@{price_1111:.4f}")
    return True


def test_3_forward_adjustment():
    """场景 3: 前方出现大单，应该前进"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 3: 前方出现大单，自动前进")
    logger.info("   当前挂在买9 @ 0.3480，买2突然出现 $5000 大单")
    
    # 初始状态：挂在买9
    current_price = 0.3480
    min_p = 1111
    
    # 盘口变化后
    ob_after = make_orderbook([
        (0.3640, 11),
        (0.3620, 5000),  # 买2 突然出现大单！
        (0.3610, 33),
        (0.3600, 30),
        (0.3550, 33),
        (0.3510, 679),
        (0.3500, 74),
        (0.3490, 191),
        (0.3480, 30),
        (0.3440, 30),
    ])
    print_orderbook(ob_after, min_p, "盘口变化后")
    
    result = calculate_safe_price(ob_after, min_p)
    assert result is not None
    new_price, new_rank = result
    
    assert new_price > current_price, f"新价格 {new_price} 应高于当前 {current_price} (前进)"
    assert new_price == 0.3610, f"应挂 0.3610 (买2价-0.001)，实际 {new_price}"
    
    direction = "前方出现安全位置 (前进)" if new_price > current_price else "保护不足 (后退)"
    logger.success(f"✅ 通过: {current_price:.4f} → {new_price:.4f} (买{new_rank}) | {direction}")
    return True


def test_4_backward_adjustment():
    """场景 4: 买1被吃掉，保护不足，应该后退"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 4: 买1被吃，保护不足，自动后退")
    logger.info("   当前挂在买2 @ 0.6250，买1被吃掉")
    
    current_price = 0.6250
    min_p = 500
    
    # 买1被吃掉后的盘口
    ob_after = make_orderbook([
        # 买1 ($2000) 已被吃掉！
        (0.6250, 100),   # 新买1 (原买2, 薄)
        (0.6240, 80),    # 新买2
        (0.6230, 50),    # 新买3
        (0.6210, 100),   # 新买4
        (0.6180, 300),   # 新买5: 累计 $630 >= $500 ✅
        (0.6100, 52),
        (0.6000, 3334),
    ])
    print_orderbook(ob_after, min_p, "买1被吃后")
    
    result = calculate_safe_price(ob_after, min_p)
    assert result is not None
    new_price, new_rank = result
    
    assert new_price < current_price, f"新价格 {new_price} 应低于当前 {current_price} (后退)"
    
    direction = "前方出现安全位置 (前进)" if new_price > current_price else "保护不足 (后退)"
    logger.success(f"✅ 通过: {current_price:.4f} → {new_price:.4f} (买{new_rank}) | {direction}")
    return True


def test_5_no_adjustment_needed():
    """场景 5: 最优位置没变，应该不动"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 5: 盘口微调，最优位置不变，保持不动")
    
    current_price = 0.6250
    min_p = 500
    
    ob = make_orderbook([
        (0.6260, 2000),  # 买1 还是很厚
        (0.6250, 850),   # 买2 微调，不影响
        (0.6240, 400),
    ])
    print_orderbook(ob, min_p)
    
    result = calculate_safe_price(ob, min_p)
    assert result is not None
    new_price, _ = result
    
    no_change = abs(new_price - current_price) < 0.00001
    assert no_change, f"价格不应变化：当前 {current_price}，计算 {new_price}"
    
    logger.success(f"✅ 通过: 保持 {current_price:.4f} 不动")
    return True


def test_6_insufficient_depth():
    """场景 6: 整个订单簿都不够深"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 6: 整个订单簿深度不足，无法找到安全位置")
    
    min_p = 5000
    
    ob = make_orderbook([
        (0.50, 100),
        (0.49, 100),
        (0.48, 100),
    ])
    print_orderbook(ob, min_p, f"总深度 $300 < 要求 ${min_p}")
    
    result = calculate_safe_price(ob, min_p)
    assert result is None, "不应找到安全价格"
    
    logger.success("✅ 通过: 正确返回 None（无安全位置）")
    return True


def test_7_real_5055_data():
    """场景 7: 使用真实 5055 (Hyperliquid) 市场数据"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 7: 真实数据 — 5055 (Hyperliquid listed on Binance)")
    logger.info("   配置: min_protection = $500")
    
    ob = make_orderbook([
        (0.6260, 1867),
        (0.6250, 1797),
        (0.6240, 450),
        (0.6230, 214),
        (0.6210, 299),
        (0.6180, 1000),
        (0.6100, 52),
        (0.6000, 3334),
        (0.5980, 46),
        (0.5960, 502),
        (0.5800, 11),
        (0.5650, 332),
    ])
    min_p = 500
    print_orderbook(ob, min_p)
    
    result = calculate_safe_price(ob, min_p)
    assert result is not None
    price, rank = result
    
    assert price == 0.6250, f"应挂 0.6250，实际 {price}"
    assert rank == 2
    
    protection = get_protection_at_price(ob, price)
    logger.success(f"✅ 通过: 挂 {price:.4f} (买{rank}), 前方保护 ${protection:.0f}")
    return True


def test_8_real_3039_data():
    """场景 8: 使用真实 3039 (Israel/Iran) 市场数据"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 8: 真实数据 — 3039 (Israel strikes Iran)")
    logger.info("   配置: min_protection = $1111")
    
    ob = make_orderbook([
        (0.3640, 11),
        (0.3620, 100),
        (0.3610, 33),
        (0.3600, 30),
        (0.3550, 33),
        (0.3510, 679),
        (0.3500, 74),
        (0.3490, 191),
        (0.3480, 30),
        (0.3440, 30),
        (0.3310, 95),
        (0.3270, 30),
        (0.3240, 49),
        (0.3200, 106),
        (0.2760, 492),
        (0.2500, 6262),
    ])
    min_p = 1111
    print_orderbook(ob, min_p)
    
    result = calculate_safe_price(ob, min_p)
    assert result is not None
    price, rank = result
    
    assert price == 0.3480, f"应挂 0.3480，实际 {price}"
    assert rank == 9
    
    protection = get_protection_at_price(ob, price)
    logger.success(f"✅ 通过: 挂 {price:.4f} (买{rank}), 前方保护 ${protection:.0f}")
    return True


def test_9_edge_exact_threshold():
    """场景 9: 刚好卡在阈值边界"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 9: 累计保护刚好等于阈值 (边界)")
    
    min_p = 500
    
    ob = make_orderbook([
        (0.50, 500),  # 买1: 刚好 $500 = min_protection
        (0.49, 300),
    ])
    print_orderbook(ob, min_p)
    
    result = calculate_safe_price(ob, min_p)
    assert result is not None
    price, rank = result
    
    # 买1金额 = $500 >= $500，所以应该挂在买1价-0.001
    assert price == 0.499, f"应挂 0.499，实际 {price}"
    
    logger.success(f"✅ 通过: 边界情况正确处理，挂 {price:.4f}")
    return True


def test_10_progressive_scenario():
    """场景 10: 完整动态场景 — 初始→后退→前进"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 场景 10: 完整动态场景 (初始 → 后退 → 前进)")
    
    min_p = 1000
    
    # 阶段 1: 初始下单
    logger.info("\n  📍 阶段 1: 初始下单")
    ob1 = make_orderbook([
        (0.50, 600),
        (0.49, 500),  # 累计: $1100 >= $1000 ✅
        (0.48, 300),
    ])
    print_orderbook(ob1, min_p, "初始盘口")
    r1 = calculate_safe_price(ob1, min_p)
    assert r1 is not None
    p1, rk1 = r1
    assert p1 == 0.489, f"阶段1: 应挂 0.489，实际 {p1}"
    logger.success(f"  ✅ 初始挂单: {p1:.4f} (买{rk1})")
    
    # 阶段 2: 买1被吃，后退
    logger.info("\n  📍 阶段 2: 买1被吃，保护不足")
    ob2 = make_orderbook([
        # 买1 被吃掉了
        (0.49, 500),
        (0.48, 300),
        (0.47, 400),  # 累计: $1200 >= $1000 ✅
        (0.46, 200),
    ])
    print_orderbook(ob2, min_p, "买1被吃后")
    r2 = calculate_safe_price(ob2, min_p)
    assert r2 is not None
    p2, rk2 = r2
    assert p2 < p1, f"阶段2: 应该后退 ({p2} < {p1})"
    logger.success(f"  ✅ 后退: {p1:.4f} → {p2:.4f} (买{rk2})")
    
    # 阶段 3: 大单出现，前进
    logger.info("\n  📍 阶段 3: 买1出现大单，恢复")
    ob3 = make_orderbook([
        (0.50, 3000),  # 大单出现！累计 $3000 >= $1000 ✅
        (0.49, 500),
        (0.48, 300),
        (0.47, 400),
    ])
    print_orderbook(ob3, min_p, "大单出现后")
    r3 = calculate_safe_price(ob3, min_p)
    assert r3 is not None
    p3, rk3 = r3
    assert p3 > p2, f"阶段3: 应该前进 ({p3} > {p2})"
    logger.success(f"  ✅ 前进: {p2:.4f} → {p3:.4f} (买{rk3})")
    
    logger.success(f"\n  ✅ 完整流程: {p1:.4f} → {p2:.4f}(后退) → {p3:.4f}(前进)")
    return True


def main():
    logger.info("=" * 60)
    logger.info("🏁 Solo Market 离线测试 — 模拟 API 数据")
    logger.info("=" * 60)
    
    tests = [
        ("基本保护 — 买1足够厚", test_1_basic_protection),
        ("稀薄订单簿 — 深挂", test_2_thin_book_deep_placement),
        ("前方大单 — 自动前进", test_3_forward_adjustment),
        ("买1被吃 — 自动后退", test_4_backward_adjustment),
        ("微调不变 — 保持不动", test_5_no_adjustment_needed),
        ("深度不足 — 无安全位置", test_6_insufficient_depth),
        ("真实数据 — 5055 Hyperliquid", test_7_real_5055_data),
        ("真实数据 — 3039 Israel/Iran", test_8_real_3039_data),
        ("边界情况 — 刚好等于阈值", test_9_edge_exact_threshold),
        ("完整动态 — 初始→后退→前进", test_10_progressive_scenario),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
                logger.error(f"❌ {name} 失败")
        except AssertionError as e:
            failed += 1
            logger.error(f"❌ {name} 断言失败: {e}")
        except Exception as e:
            failed += 1
            logger.error(f"❌ {name} 异常: {e}")
    
    logger.info("\n" + "=" * 60)
    logger.info(f"📊 测试结果: {passed} 通过, {failed} 失败 (共 {passed + failed} 项)")
    
    if failed == 0:
        logger.success("🎉 全部通过!")
        return 0
    else:
        logger.error(f"💥 {failed} 项失败!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
