#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opinion 做市刷积分机器人
主入口文件

策略核心逻辑：
- 目标是获取积分而不是交易盈利
- 只在安全位置提供流动性吃平台做市补贴
- 刻意避免成交，不承担方向和价格风险
"""

import os
import sys
import yaml
from pathlib import Path
from threading import Thread

from dotenv import load_dotenv
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    "log/log_{time:YYYY-MM-DD_HH-mm-ss}.txt",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {message}",
    level="INFO",
    rotation="10 MB",
)
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level} | {message}",
    level="DEBUG",
)


def load_config():
    """加载配置"""
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)
    
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    config["opinion_labs"] = {
        "private_key": os.getenv("OPINION_PRIVATE_KEY", ""),
        "token": os.getenv("OPINION_TOKEN", ""),
        "wallet_address": os.getenv("OPINION_WALLET_ADDRESS", ""),
        "rpc_url": os.getenv("OPINION_RPC_URL", "https://binance.llamarpc.com"),
        "apikey": os.getenv("OPINION_APIKEY", ""),
    }
    
    if os.getenv("PROXY_ENABLED"):
        config["proxy"]["enabled"] = os.getenv("PROXY_ENABLED", "").lower() == "true"
    if os.getenv("PROXY_HTTP"):
        config["proxy"]["http"] = os.getenv("PROXY_HTTP")
    if os.getenv("PROXY_HTTPS"):
        config["proxy"]["https"] = os.getenv("PROXY_HTTPS")
    
    return config


def main():
    """主函数"""
    config = load_config()
    
    opinion_cfg = config.get("opinion_labs", {})
    proxy_cfg = config.get("proxy", {})
    
    proxy = None
    if proxy_cfg.get("enabled"):
        proxy = {"http": proxy_cfg["http"], "https": proxy_cfg["https"]}
        logger.info(f"代理已启用: {proxy_cfg['http']}")
    
    # 初始化 Fetcher
    from modules.fetch_opinion import OpinionFetcher
    fetcher = OpinionFetcher(
        private_key=opinion_cfg.get("private_key"),
        token=opinion_cfg.get("token"),
        proxy=proxy,
    )
    
    # 初始化 Trader
    from modules.trader_opinion_sdk import OpinionTraderSDK, SDK_AVAILABLE
    if not SDK_AVAILABLE:
        logger.error("请安装SDK: pip install opinion-clob-sdk")
        return
    
    trader = OpinionTraderSDK(
        private_key=opinion_cfg["private_key"],
        wallet_address=opinion_cfg.get("wallet_address"),
        apikey=opinion_cfg.get("apikey", ""),
        rpc_url=opinion_cfg.get("rpc_url", "https://bsc-dataseed1.binance.org"),
        proxy=proxy,
    )
    
    # 获取账户余额
    balance = trader.get_balance() or 0.0
    logger.info(f"账户余额: ${balance:.2f}")
    
    # 初始化做市策略
    from modules.maker_strategy import MakerStrategy
    strategy = MakerStrategy(
        fetcher=fetcher,
        trader=trader,
        dashboard=None,
        dry_run=False,  # 实盘模式
        proxy=proxy,
        config=config,
    )
    
    logger.info("启动做市刷积分策略...")
    
    # 直接运行策略
    try:
        strategy.run()
    except KeyboardInterrupt:
        strategy.stop()
        logger.info("程序退出")


if __name__ == "__main__":
    main()
