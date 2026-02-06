#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查找可用的二元市场

运行此脚本找到可以正常交易的市场 ID
"""

import os
import sys
import yaml
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

# 添加项目根目录到 sys.path
root_dir = Path(__file__).parent.parent
sys.path.append(str(root_dir))

from modules.fetch_opinion import OpinionFetcher
from modules.trader_opinion_sdk import OpinionTraderSDK


def main():
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=str, default=".env", help="环境变量文件路径")
    parser.add_argument("--config-file", type=str, default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    # 配置日志
    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    
    # 加载环境变量
    if os.path.exists(args.env_file):
        load_dotenv(args.env_file, override=True)
        logger.info(f"已加载环境变量: {args.env_file}")
    else:
        # 尝试自动搜索逻辑作为退路
        env_paths = [root_dir / ".env"]
        env_paths.extend(list(root_dir.glob("account_*.env")))
        env_paths.extend(list((root_dir / "accounts").glob("*/.env")))
        
        for p in env_paths:
            if p.exists():
                load_dotenv(p)
                logger.info(f"自动加载环境变量: {p}")
                break
    
    # 尝试加载配置（主要是为了获取代理）
    config = {}
    config_paths = [args.config_file, root_dir / "config.yaml"]
    config_paths.extend(list(root_dir.glob("account_*.config.yaml")))
    
    for p in config_paths:
        p_path = Path(p)
        if p_path.exists():
            with open(p_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                logger.debug(f"已加载配置: {p_path}")
                break
    
    private_key = os.getenv('OPINION_PRIVATE_KEY')
    apikey = os.getenv('OPINION_APIKEY')
    
    if not private_key:
        logger.error("❌ 未找到私钥 (OPINION_PRIVATE_KEY)！")
        logger.info("请通过 --env-file 指定环境文件，或者在根目录创建 .env 文件。")
        logger.info("用法示例: python scripts/find_markets.py --env-file account_1.env")
        return

    if not apikey:
        logger.error("❌ 未找到 APIKEY (OPINION_APIKEY)！")
        return

    # 代理配置
    proxy_config = config.get('proxy', {})
    proxy = None
    if proxy_config.get('enabled'):
        proxy = {'http': proxy_config.get('http'), 'https': proxy_config.get('https')}
    
    fetcher = OpinionFetcher(private_key=private_key, proxy=proxy, apikey=apikey)
    trader = OpinionTraderSDK(private_key=private_key, proxy=proxy, apikey=apikey)
    
    logger.info("正在获取高流动性二元市场...")
    
    # 获取市场列表
    markets = fetcher.fetch_markets(limit=50, fetch_all=True)
    available_markets = []
    
    for m in markets:
        # 跳过多选市场
        if m.get("isMulti", False):
            continue
        
        topic_id = m.get("topicId") or m.get("marketId")
        if not topic_id:
            continue
        
        try:
            topic_id = int(topic_id)
        except:
            continue
        
        volume = float(m.get("volume", 0) or 0)
        if volume < 50000:  # 最小交易量
            continue
        
        yes_price = float(m.get("yesPrice", 0) or 0)
        # 只排除极端价格
        if yes_price < 0.1 or yes_price > 0.9:
            continue
        
        # 验证市场是否可用
        try:
            market_info = trader.get_market_by_topic_id(topic_id)
            if market_info and market_info.get('yes_token_id'):
                available_markets.append({
                    "topic_id": topic_id,
                    "title": market_info.get("title", m.get("title", ""))[:50],
                    "yes_price": yes_price,
                    "volume": volume,
                    "distance_to_half": abs(yes_price - 0.5),  # 计算与 0.5 的距离
                })
                logger.info(f"✓ {topic_id}: {market_info.get('title', '')[:50]} | Price: ${yes_price:.3f} | Vol: ${volume:,.0f}")
        except Exception as e:
            logger.debug(f"✗ {topic_id}: {e}")
            continue
    
    logger.info(f"\n找到 {len(available_markets)} 个可用市场")
    logger.info("\n推荐使用的市场 ID（按价格接近0.5优先，交易量从低到高排序）：")
    
    # 先按价格距离 0.5 的远近排序，再按交易量从低到高排序
    available_markets.sort(key=lambda x: (x['distance_to_half'], x['volume']))
    
    for i, m in enumerate(available_markets[:20], 1):
        distance_pct = m['distance_to_half'] * 100
        logger.info(f"{i}. {m['topic_id']} - {m['title']} | Price: ${m['yes_price']:.3f} (距0.5: {distance_pct:.1f}%) | Vol: ${m['volume']:,.0f}")
    
    logger.info(f"\n请将这些 ID 添加到 config.yaml 的 solo_market.topic_ids 中")


if __name__ == '__main__':
    main()
