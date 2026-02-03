#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卖出所有持仓（只卖出>=1份的有效持仓）
支持3小时未成交自动取消订单
"""

import os
import sys
import time
from datetime import datetime, timezone

from pathlib import Path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from modules.trader_opinion_sdk import OpinionTraderSDK

# 配置
ORDER_TIMEOUT_HOURS = 3  # 订单超时时间（小时）
CHECK_INTERVAL_SECONDS = 60  # 检查间隔（秒）
MONITOR_MODE = '--monitor' in sys.argv  # 是否启用监控模式

trader = OpinionTraderSDK(
    private_key=os.getenv('OPINION_PRIVATE_KEY', ''),
    wallet_address=os.getenv('OPINION_WALLET_ADDRESS', ''),
    apikey=os.getenv('OPINION_APIKEY', ''),
    rpc_url=os.getenv('OPINION_RPC_URL', 'https://binance.llamarpc.com'),
)


def cancel_expired_orders():
    """取消超过3小时未成交的订单"""
    print('检查超时订单...')
    # status 为空字符串获取所有订单，然后在代码中过滤未成交的
    orders = trader.get_my_orders()
    
    if not orders:
        print('没有待处理订单')
        return 0
    
    cancelled_count = 0
    now = datetime.now(timezone.utc)
    
    for order in orders:
        order_id = getattr(order, 'order_id', None) or getattr(order, 'id', None)
        created_at = getattr(order, 'created_at', None) or getattr(order, 'create_time', None)
        
        if not order_id or not created_at:
            continue
        
        # 解析创建时间
        try:
            if isinstance(created_at, (int, float)):
                # 时间戳（秒或毫秒）
                if created_at > 1e12:
                    created_at = created_at / 1000
                order_time = datetime.fromtimestamp(created_at, tz=timezone.utc)
            elif isinstance(created_at, str):
                # ISO 格式字符串
                order_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            else:
                continue
        except Exception as e:
            print(f'解析订单时间失败: {order_id} - {e}')
            continue
        
        # 计算订单存在时间
        age_hours = (now - order_time).total_seconds() / 3600
        
        if age_hours >= ORDER_TIMEOUT_HOURS:
            print(f'取消超时订单: {order_id} (已存在 {age_hours:.1f} 小时)')
            if trader.cancel_order(order_id):
                cancelled_count += 1
                print(f'  已取消')
            else:
                print(f'  取消失败')
    
    return cancelled_count


def sell_all_positions():
    """卖出所有持仓"""
    print('获取持仓...')
    positions = trader.get_positions()

    if not positions:
        print('没有持仓')
        return []

    print(f'找到 {len(positions)} 个持仓')
    placed_orders = []

    for p in positions:
        market_id = getattr(p, 'market_id', None)
        shares = float(getattr(p, 'shares_owned', '0') or 0)
        outcome_side = getattr(p, 'outcome_side', 0)
        side = 'YES' if outcome_side == 1 else 'NO'
        
        # 跳过小于1份的持仓
        if shares < 1 or not market_id:
            print(f'跳过: market_id={market_id} {side} shares={shares:.4f} (太小)')
            continue
        
        # 减少1%避免精度问题
        sell_shares = shares * 0.99
        sell_shares = int(sell_shares * 100) / 100  # 保留2位小数
        
        # 检查卖出价值是否>=1.3 USDT
        price = 0.95 if side == 'NO' else 0.05
        value = sell_shares * price
        if value < 1.3:
            print(f'跳过: market_id={market_id} {side} shares={shares:.4f} (价值太低 ${value:.2f})')
            continue
        
        print(f'卖出: market_id={market_id} {side} shares={sell_shares:.2f} @ {price}')
        
        result = trader.place_order(
            topic_id=market_id,
            outcome=side,
            amount=sell_shares,
            price=price,
            order_type=2,
            side='SELL',
        )
        
        if result:
            print(f'  成功')
            placed_orders.append({
                'market_id': market_id,
                'side': side,
                'shares': sell_shares,
                'price': price,
                'time': datetime.now(timezone.utc),
            })
        else:
            print(f'  失败')
    
    return placed_orders


def monitor_orders():
    """监控订单，超时自动取消"""
    print(f'\n启动订单监控模式 (超时: {ORDER_TIMEOUT_HOURS}小时, 检查间隔: {CHECK_INTERVAL_SECONDS}秒)')
    print('按 Ctrl+C 退出\n')
    
    try:
        while True:
            cancelled = cancel_expired_orders()
            if cancelled > 0:
                print(f'本轮取消了 {cancelled} 个超时订单')
            
            print(f'下次检查: {CHECK_INTERVAL_SECONDS}秒后...')
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print('\n监控已停止')


if __name__ == '__main__':
    # 先取消已超时的订单
    cancel_expired_orders()
    
    # 卖出所有持仓
    sell_all_positions()
    
    print('完成')
    
    # 如果启用监控模式，持续检查并取消超时订单
    if MONITOR_MODE:
        monitor_orders()
