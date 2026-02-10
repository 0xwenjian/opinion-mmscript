#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试订单成交检测优化逻辑
验证：即使订单状态为 canceled，只要 filled_amount > 0，也能正确检测为成交并报警。
"""

import sys
import os
import yaml
import time
from loguru import logger

# 添加当前目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from solomarket import SoloMarketMonitor
from modules.models import SoloMarketOrder

def test_fill_detection():
    # 1. 模拟配置
    config = {
        'solo_market': {
            'topic_ids': [1234],
            'min_protection_amount': 500,
            'order_amount': 100,
        },
        'telegram': {
            'bot_token': '',
            'chat_id': ''
        },
        'simulation': True
    }
    
    # 2. 初始化监控器
    monitor = SoloMarketMonitor(config)
    topic_id = 1234
    order_id = "test_canceled_but_filled"
    
    # 3. 手动注入一个模拟订单
    monitor.orders[topic_id] = SoloMarketOrder(
        order_id=order_id,
        topic_id=topic_id,
        title="Test Market For Partial Fill",
        price=0.5,
        amount=100.0,
        create_time=time.time() - 60, # 1分钟前创建
        last_check_time=time.time()
    )
    
    # 模拟市场信息
    monitor.market_info[topic_id] = {
        'title': "Test Market For Partial Fill",
        'yes_token_id': 'mock_token'
    }
    
    # 4. 设置模拟状态：已撤单，但成交了 99.5u
    logger.info(f">>> 模拟场景：订单 {order_id} 状态为 canceled，但成交金额为 99.5")
    monitor.trader.set_mock_order_status(order_id, status="canceled", filled_amount=99.5)
    
    # 5. 执行检查逻辑
    logger.info(">>> 调用 check_and_adjust_order 进行检查...")
    result = monitor.check_and_adjust_order(topic_id)
    
    # 6. 验证结果
    if topic_id not in monitor.orders:
        logger.success("✅ 测试通过：订单已被正确识别为已成交并从监控列表中移除。")
        # 返回 False 表示因为成交而停止了后续检查，这是符合预期的
        if result is False:
             logger.success("✅ 返回值验证正确 (False)。")
    else:
        logger.error("❌ 测试失败：订单未被识别为成交，仍在监控列表中。")

if __name__ == '__main__':
    test_fill_detection()
