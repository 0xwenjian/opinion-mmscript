
import os
import sys
import time
import yaml
from loguru import logger

# 确保能导入 solomarket
sys.path.append(os.getcwd())
from solomarket import SoloMarketMonitor, OrderBookLevel

def run_test():
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    # 1. 构造测试配置
    config = {
        "simulation": True,
        "solo_market": {
            "topic_ids": [9999],
            "min_protection_amount": 3000,
            "order_amount": 100,
        },
        "telegram": {
            "bot_token": "",
            "chat_id": ""
        }
    }
    
    # 设置环境变量以满足初始化检查
    os.environ['OPINION_PRIVATE_KEY'] = "0x" + "1"*64
    os.environ['OPINION_APIKEY'] = "mock_key"
    
    logger.info(">>> 开始模拟测试 - 核心逻辑校验 <<<")
    monitor = SoloMarketMonitor(config)
    
    # ==========================================
    # 场景 1: 保护不足，必须去更深的地方挂单
    # ==========================================
    logger.info("\n[场景 1] 模拟各档保护不足 ($400/档)，需要去第 8 档")
    # 设置盘口：每档只有 $400 保护
    for i in range(10):
        monitor.fetcher.set_mock_bid(i, 0.80 - i*0.01, 500) 
        # 保护金额 = 价格 * 数量。如果价格 0.8，数量 500，则保护 = $400。
        # 累计到第 8 档: 400 * 8 = $3200 > $3000。
    
    logger.info("尝试初始下单...")
    monitor.place_order(9999)
    
    if 9999 in monitor.orders:
        order = monitor.orders[9999]
        logger.info(f"下单结果: 价格={order.price}, 预期是在较深档位")
    else:
        logger.error("场景 1 下单失败！")
        return

    # ==========================================
    # 场景 2: 前方出现大单，自动前进到更优位置
    # ==========================================
    logger.info("\n[场景 2] 模拟第 2 档突然出现巨款 ($5000)，检查是否自动前进")
    # 修改第 2 档 (index 1)，放入 $5000
    monitor.fetcher.set_mock_bid(1, 0.79, 7000) # 0.79 * 7000 = $5530
    
    logger.info("执行一次检查...")
    monitor.check_and_adjust_order(9999)
    
    if 9999 in monitor.orders:
        new_order = monitor.orders[9999]
        if new_order.price > order.price:
            logger.success(f"成功前进！新价格 {new_order.price} 优于旧价格 {order.price}")
        else:
            logger.warning("未触发前进，请检查逻辑")
    
    logger.info("\n>>> 模拟测试结束 <<<")

if __name__ == "__main__":
    run_test()

