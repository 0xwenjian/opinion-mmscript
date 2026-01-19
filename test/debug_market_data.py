#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：测试 SDK 的 get_categorical_market 方法
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from loguru import logger
logger.remove()
logger.add(sys.stderr, format="{message}", level="INFO")


def main():
    from modules.trader_opinion_sdk import OpinionTraderSDK, SDK_AVAILABLE
    
    if not SDK_AVAILABLE:
        logger.error("SDK 未安装")
        return
    
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
    
    # 测试用父级 topicId 调用 get_categorical_market
    topic_id = 97  # 多选市场的父级 ID
    
    logger.info(f"\n测试 get_categorical_market({topic_id})...")
    try:
        result = trader.client.get_categorical_market(topic_id)
        logger.info(f"返回类型: {type(result)}")
        
        if hasattr(result, 'errno'):
            logger.info(f"errno: {result.errno}")
        if hasattr(result, 'errmsg'):
            logger.info(f"errmsg: {result.errmsg}")
        if hasattr(result, 'result'):
            r = result.result
            logger.info(f"result 类型: {type(r)}")
            if hasattr(r, 'data'):
                data = r.data
                logger.info(f"data 类型: {type(data)}")
                # 打印所有属性
                if hasattr(data, '__dict__'):
                    for k, v in data.__dict__.items():
                        if not k.startswith('_'):
                            logger.info(f"  {k}: {v}")
    except Exception as e:
        logger.error(f"get_categorical_market 失败: {e}")
    
    # 测试用 questionId 调用 get_orderbook
    question_id = "94c9067c56b9638f5fca818c42472062a2ca2995f22502484eb4af538539734d"
    logger.info(f"\n测试 get_orderbook({question_id[:20]}...)...")
    try:
        result = trader.client.get_orderbook(question_id)
        logger.info(f"返回类型: {type(result)}")
        if hasattr(result, 'errno'):
            logger.info(f"errno: {result.errno}")
        if hasattr(result, 'result'):
            logger.info(f"result: {result.result}")
    except Exception as e:
        logger.error(f"get_orderbook 失败: {e}")


if __name__ == "__main__":
    main()
