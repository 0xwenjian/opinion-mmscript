#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订单取消脚本
- 取消所有未成交订单
- 或取消超过指定时间的订单
- 或取消指定订单ID

用法:
    python cancel_orders.py                    # 取消所有超过3小时的订单
    python cancel_orders.py --all              # 取消所有未成交订单
    python cancel_orders.py --hours 1          # 取消超过1小时的订单
    python cancel_orders.py --id ORDER_ID      # 取消指定订单
    python cancel_orders.py --list             # 仅列出订单，不取消
"""

import os
import sys
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv('.env')

from modules.trader_opinion_sdk import OpinionTraderSDK

trader = OpinionTraderSDK(
    private_key=os.getenv('OPINION_PRIVATE_KEY', ''),
    wallet_address=os.getenv('OPINION_WALLET_ADDRESS', ''),
    apikey=os.getenv('OPINION_APIKEY', ''),
    rpc_url=os.getenv('OPINION_RPC_URL', 'https://binance.llamarpc.com'),
)


def parse_order_time(created_at):
    """解析订单创建时间"""
    if isinstance(created_at, (int, float)):
        if created_at > 1e12:
            created_at = created_at / 1000
        return datetime.fromtimestamp(created_at, tz=timezone.utc)
    elif isinstance(created_at, str):
        return datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    return None


def list_orders():
    """列出所有未成交订单"""
    print('获取订单列表...')
    orders = trader.get_my_orders()
    
    if not orders:
        print('没有未成交订单')
        return []
    
    print(f'\n找到 {len(orders)} 个未成交订单:\n')
    print(f'{"订单ID":<40} {"市场ID":<10} {"方向":<6} {"价格":<8} {"数量":<12} {"已存在时间"}')
    print('-' * 100)
    
    now = datetime.now(timezone.utc)
    
    for order in orders:
        order_id = getattr(order, 'order_id', None) or getattr(order, 'id', 'N/A')
        market_id = getattr(order, 'market_id', None) or getattr(order, 'topic_id', 'N/A')
        side = getattr(order, 'side', 'N/A')
        price = getattr(order, 'price', 'N/A')
        amount = getattr(order, 'amount', None) or getattr(order, 'size', 'N/A')
        created_at = getattr(order, 'created_at', None) or getattr(order, 'create_time', None)
        
        age_str = 'N/A'
        if created_at:
            order_time = parse_order_time(created_at)
            if order_time:
                age_hours = (now - order_time).total_seconds() / 3600
                if age_hours < 1:
                    age_str = f'{age_hours * 60:.0f}分钟'
                else:
                    age_str = f'{age_hours:.1f}小时'
        
        print(f'{str(order_id):<40} {str(market_id):<10} {str(side):<6} {str(price):<8} {str(amount):<12} {age_str}')
    
    print()
    return orders


def cancel_order_by_id(order_id: str):
    """取消指定订单"""
    print(f'取消订单: {order_id}')
    if trader.cancel_order(order_id):
        print('取消成功')
        return True
    else:
        print('取消失败')
        return False


def cancel_all_orders():
    """取消所有未成交订单"""
    print('获取订单列表...')
    orders = trader.get_my_orders()
    
    if not orders:
        print('没有未成交订单')
        return 0
    
    print(f'找到 {len(orders)} 个未成交订单')
    cancelled = 0
    
    for order in orders:
        order_id = getattr(order, 'order_id', None) or getattr(order, 'id', None)
        if not order_id:
            continue
        
        print(f'取消订单: {order_id}')
        if trader.cancel_order(order_id):
            cancelled += 1
            print('  成功')
        else:
            print('  失败')
    
    return cancelled


def cancel_expired_orders(timeout_hours: float):
    """取消超过指定时间的订单"""
    print(f'获取订单列表 (超时: {timeout_hours}小时)...')
    orders = trader.get_my_orders()
    
    if not orders:
        print('没有未成交订单')
        return 0
    
    print(f'找到 {len(orders)} 个未成交订单')
    cancelled = 0
    now = datetime.now(timezone.utc)
    
    for order in orders:
        order_id = getattr(order, 'order_id', None) or getattr(order, 'id', None)
        created_at = getattr(order, 'created_at', None) or getattr(order, 'create_time', None)
        
        if not order_id or not created_at:
            continue
        
        order_time = parse_order_time(created_at)
        if not order_time:
            continue
        
        age_hours = (now - order_time).total_seconds() / 3600
        
        if age_hours >= timeout_hours:
            print(f'取消超时订单: {order_id} (已存在 {age_hours:.1f} 小时)')
            if trader.cancel_order(order_id):
                cancelled += 1
                print('  成功')
            else:
                print('  失败')
        else:
            print(f'跳过订单: {order_id} (仅存在 {age_hours:.1f} 小时)')
    
    return cancelled


def main():
    parser = argparse.ArgumentParser(description='订单取消脚本')
    parser.add_argument('--all', action='store_true', help='取消所有未成交订单')
    parser.add_argument('--hours', type=float, default=3, help='取消超过指定小时数的订单 (默认: 3)')
    parser.add_argument('--id', type=str, help='取消指定订单ID')
    parser.add_argument('--list', action='store_true', help='仅列出订单，不取消')
    
    args = parser.parse_args()
    
    if args.list:
        list_orders()
    elif args.id:
        cancel_order_by_id(args.id)
    elif args.all:
        cancelled = cancel_all_orders()
        print(f'\n共取消 {cancelled} 个订单')
    else:
        cancelled = cancel_expired_orders(args.hours)
        print(f'\n共取消 {cancelled} 个超时订单')


if __name__ == '__main__':
    main()
