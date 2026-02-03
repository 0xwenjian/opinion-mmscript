
import os
import sys
import yaml
import json
from loguru import logger
from dotenv import load_dotenv

from pathlib import Path
root_dir = Path(__file__).parent.parent
sys.path.append(str(root_dir))

from modules.trader_opinion_sdk import OpinionTraderSDK

def load_config():
    config_paths = [root_dir / "config.yaml"]
    config_paths.extend(list((root_dir / "accounts").glob("*/config.yaml")))
    for p in config_paths:
        if p.exists():
            with open(p, 'r') as f:
                return yaml.safe_load(f)
    return {}

def main():
    # 尝试加载环境变量 (多路径支持)
    env_paths = [root_dir / ".env"]
    env_paths.extend(list((root_dir / "accounts").glob("*/.env")))
    for p in env_paths:
        if p.exists():
            load_dotenv(p)
            break
            
    config = load_config()

    print("=== 开始获取我的交易历史/订单 ===")
    
    # 初始化 SDK 交易器
    try:
        # 从环境变量加载敏感信息
        private_key = os.getenv('OPINION_PRIVATE_KEY')
        apikey = os.getenv('OPINION_APIKEY')
        wallet_address = os.getenv('OPINION_WALLET_ADDRESS')
        rpc_url = os.getenv('OPINION_RPC_URL', 'https://binance.llamarpc.com')
        
        if not private_key:
            print("错误: 未找到 OPINION_PRIVATE_KEY，请在 .env 文件中配置")
            return
        
        # 代理配置
        proxy_config = config.get('proxy', {})
        proxy = None
        if proxy_config.get('enabled'):
            proxy = {
                'http': proxy_config.get('http'),
                'https': proxy_config.get('https'),
            }

        trader = OpinionTraderSDK(
            private_key=private_key,
            wallet_address=wallet_address,
            apikey=apikey,
            rpc_url=rpc_url,
            proxy=proxy
        )
        print("SDK 初始化成功。")
    except Exception as e:
        print(f"初始化失败: {e}")
        return

    # 调用 get_my_trades (或类似的 get_orders/get_history 方法)
    # 用户特别请求了 get_my_trades，在官方 SDK 文档中可能对应获取历史成交或订单的方法
    
    # 调用正确的 SDK 方法
    try:
        # 1. 获取我的订单 (get_my_orders)
        if hasattr(trader.client, 'get_my_orders'):
             print("\n正在调用 get_my_orders (获取我的订单)...")
             # 调用 get_my_orders，通常默认返回最近的订单
             orders_res = trader.client.get_my_orders()
             
             # 处理 SDK 返回的对象
             orders = []
             if hasattr(orders_res, 'result'):
                 result = orders_res.result
                 if hasattr(result, 'list'):
                     orders = result.list
                 else:
                     orders = result
             
             print(f"订单数量: {len(orders) if isinstance(orders, list) else '未知'}")
             if orders:
                 print(f"最近订单: {json.dumps(orders[:5], indent=2, default=str)}")
        
        # 2. 获取我的成交 (get_my_trades)
        if hasattr(trader.client, 'get_my_trades'):
             print("\n正在调用 get_my_trades (获取我的成交历史)...")
             trades_res = trader.client.get_my_trades()
             
             # 处理 SDK 返回的对象
             trades = []
             if hasattr(trades_res, 'result'):
                 result = trades_res.result
                 if hasattr(result, 'list'):
                     trades = result.list
                 else:
                     trades = result
             
             print(f"成交数量: {len(trades) if isinstance(trades, list) else '未知'}")
             if trades:
                 print(f"最近成交: {json.dumps(trades[:5], indent=2, default=str)}")
             
        # 如果方法都不存在
        if not hasattr(trader.client, 'get_my_orders') and not hasattr(trader.client, 'get_my_trades'):
             print("\n在 SDK 中未找到 get_my_orders 或 get_my_trades 方法。")

    except Exception as e:
        print(f"获取数据时发生错误: {e}")
        import traceback
        print(traceback.format_exc())

if __name__ == "__main__":
    main()
