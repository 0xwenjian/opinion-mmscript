#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpinionLabs 交易模块 (使用官方 SDK)
"""

import time
from typing import Dict, Optional, Tuple
from loguru import logger

SDK_AVAILABLE = False
_SDK_PROXY_PATCHED = False

def _patch_sdk_proxy(proxy_url: str):
    """Monkey patch Opinion SDK to use proxy"""
    global _SDK_PROXY_PATCHED
    if _SDK_PROXY_PATCHED:
        return
    
    try:
        from opinion_api import configuration
        _orig_init = configuration.Configuration.__init__
        
        def _patched_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            self.proxy = proxy_url
        
        configuration.Configuration.__init__ = _patched_init
        _SDK_PROXY_PATCHED = True
        logger.info(f"SDK 代理已注入: {proxy_url}")
    except Exception as e:
        logger.warning(f"SDK 代理注入失败: {e}")

try:
    from opinion_clob_sdk import Client, TopicType, TopicStatusFilter
    from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
    from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
    from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER, MARKET_ORDER
    SDK_AVAILABLE = True
except ImportError as e:
    logger.error(f"Opinion SDK 未安装: {e}")
    logger.error("请运行: pip install opinion-clob-sdk")


class OpinionTraderSDK:
    """OpinionLabs 交易执行器 (基于官方 SDK)"""

    def __init__(
        self,
        private_key: str,
        wallet_address: Optional[str] = None,
        host: str = "https://proxy.opinion.trade:8443",
        apikey: str = "",
        chain_id: int = 56,
        rpc_url: str = "https://bsc-dataseed1.binance.org",
        proxy: Optional[Dict] = None,
    ):
        if not SDK_AVAILABLE:
            raise ImportError("Opinion SDK 未安装")

        self.private_key = private_key
        self.host = host
        self.chain_id = chain_id
        self.proxy = proxy

        if proxy and isinstance(proxy, dict) and proxy.get("http"):
            proxy_url = proxy.get("https") or proxy.get("http")
            _patch_sdk_proxy(proxy_url)

        if not wallet_address:
            from eth_account import Account
            account = Account.from_key(private_key)
            wallet_address = account.address
            logger.info(f"从私钥派生钱包地址: {wallet_address}")

        self.wallet_address = wallet_address
        self.apikey = apikey  # 保存 apikey 用于 API 调用

        try:
            if not rpc_url:
                rpc_url = "https://bsc-dataseed1.binance.org"

            self.client = Client(
                host=host,
                apikey=apikey if apikey else "",
                chain_id=chain_id,
                rpc_url=rpc_url,
                private_key=private_key,
                multi_sig_addr=wallet_address,
            )
            
            logger.success(f"Opinion SDK 初始化成功")

        except Exception as e:
            logger.error(f"Opinion SDK 初始化失败: {e}")
            raise

    def get_market_by_topic_id(self, topic_id: int, is_categorical: bool = False) -> Optional[Dict]:
        """根据 topic_id 获取市场详情
        
        Args:
            topic_id: 市场 ID
            is_categorical: 是否为多选市场（categorical market）
        """
        try:
            if is_categorical:
                response = self.client.get_categorical_market(topic_id)
            else:
                response = self.client.get_market(topic_id)

            if not response:
                logger.warning(f"未找到 topic_id={topic_id} 的市场")
                return None

            if hasattr(response, 'errno') and response.errno != 0:
                error_msg = getattr(response, 'errmsg', 'Unknown error')
                logger.error(f"API 错误 ({response.errno}): {error_msg}")
                if response.errno == 10403:
                    logger.error("地理位置限制! 请使用代理或 VPN")
                return None

            market = None
            if hasattr(response, 'result'):
                result = response.result
                if hasattr(result, 'data'):
                    market = result.data
                elif hasattr(result, 'result'):
                    market = result.result
                else:
                    market = result

            if not market and hasattr(response, 'yesTokenId'):
                market = response

            if not market:
                logger.error(f"无法从响应中提取市场数据")
                return None

            yes_token_id = getattr(market, "yesTokenId", None) or getattr(market, "yes_token_id", None)
            no_token_id = getattr(market, "noTokenId", None) or getattr(market, "no_token_id", None)
            
            # 尝试从 API 对象获取标题
            title = (getattr(market, "title", None) or 
                    getattr(market, "topic_title", None) or
                    getattr(market, "topicTitle", None) or
                    getattr(market, "name", None))
            
            # 如果没有标题，从公开 API 获取
            if not title:
                title = self.get_market_title(topic_id)
            
            market_chain_id = getattr(market, "chainId", None) or getattr(market, "chain_id", None)

            market_status = getattr(market, 'status', None)
            if market_status is not None:
                try:
                    status_value = int(market_status) if isinstance(market_status, (int, str)) else getattr(market_status, 'value', None)
                    if status_value is not None and status_value != 2:
                        logger.warning(f'市场 {topic_id} 状态异常: status={status_value}')
                        return None
                except Exception:
                    pass

            if not yes_token_id or not no_token_id:
                logger.error(f"市场 {topic_id} 缺少 tokenId")
                return None

            market_info = {
                "market_id": topic_id,
                "title": title,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "chain_id": market_chain_id,
            }

            logger.debug(f"市场: {market_info['title']} | YES={market_info['yes_token_id']} NO={market_info['no_token_id']}")
            return market_info

        except Exception as e:
            logger.error(f"获取市场详情异常: {e}")
            return None

    def get_market_title(self, topic_id: int) -> str:
        """从 Opinion API 获取市场标题"""
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            url = f"https://openapi.opinion.trade/openapi/market/{topic_id}"
            headers = {'apikey': self.apikey} if hasattr(self, 'apikey') and self.apikey else {}
            
            response = requests.get(url, headers=headers, timeout=5, verify=False)
            if response.status_code == 200:
                data = response.json()
                # API 返回格式: {'result': {'data': {'marketTitle': '...'}}}
                if data and 'result' in data:
                    result = data['result']
                    if isinstance(result, dict) and 'data' in result:
                        market_data = result['data']
                        if isinstance(market_data, dict) and 'marketTitle' in market_data:
                            title = market_data['marketTitle']
                            logger.debug(f"从 API 获取到市场标题: {title[:50]}")
                            return title
        except Exception as e:
            logger.debug(f"获取市场标题失败: {e}")
        return f"Market {topic_id}"

    def place_order(
        self,
        topic_id: int,
        outcome: str,
        amount: float,
        price: float,
        order_type: int = 2,
        side: str = "BUY",
        token_id: str = None,
    ) -> Optional[Dict]:
        """
        下单

        Args:
            topic_id: 市场 ID
            outcome: YES 或 NO
            amount: 下单金额 (USD)
            price: 价格
            order_type: 1=市价单, 2=限价单
            side: BUY 或 SELL
            token_id: 可选，直接传入 tokenId 跳过市场详情获取
        """
        try:
            is_yes = outcome.upper() == "YES"
            is_buy = side.upper() == "BUY"

            logger.info(f"下单: topic={topic_id} {side} {outcome} ${amount} @ {price}")

            # 如果没有传入 token_id，则从市场详情获取
            if not token_id:
                market = self.get_market_by_topic_id(topic_id)
                if not market:
                    logger.error("无法获取市场详情")
                    return None

                token_id = market["yes_token_id"] if is_yes else market["no_token_id"]
                if not token_id:
                    logger.error(f"无法获取 {outcome} 的 tokenId")
                    return None

            order_side = OrderSide.BUY if is_buy else OrderSide.SELL
            order_type_enum = MARKET_ORDER if order_type == 1 else LIMIT_ORDER

            # 卖单使用 makerAmountInBaseToken（token数量），买单使用 makerAmountInQuoteToken（USD金额）
            if not is_buy:
                # 卖单：使用传入的 amount 作为 token 数量
                # 截断到小数点后6位避免精度问题
                sell_amount = int(amount * 1000000) / 1000000
                order_data = PlaceOrderDataInput(
                    marketId=topic_id,
                    tokenId=str(token_id),
                    side=order_side,
                    orderType=order_type_enum,
                    price=str(price),
                    makerAmountInBaseToken=str(sell_amount),
                )
            else:
                # 买单：使用 USD 金额
                # 限价单金额精确到小数点后两位
                if order_type == 2:
                    amount = round(amount, 2)
                order_data = PlaceOrderDataInput(
                    marketId=topic_id,
                    tokenId=str(token_id),
                    side=order_side,
                    orderType=order_type_enum,
                    price=str(price),
                    makerAmountInQuoteToken=str(amount),
                )

            try:
                result = self.client.place_order(data=order_data, check_approval=False)
                logger.debug(f"SDK 返回结果: {result}")
                logger.debug(f"结果类型: {type(result)}")
                if result:
                    # 检查返回结果中是否有错误
                    if hasattr(result, 'errno') and result.errno != 0:
                        error_msg = getattr(result, 'errmsg', '')
                        logger.error(f"下单失败: errno={result.errno}, errmsg={error_msg}")
                        # 检查是否余额不足
                        if result.errno == 10207 or 'Insufficient balance' in str(error_msg):
                            return "INSUFFICIENT_BALANCE"
                        return None
                    if hasattr(result, 'result'):
                        logger.debug(f"订单详情: {result.result}")
            except Exception as sdk_error:
                logger.error(f"SDK place_order 失败: {sdk_error}")
                return None

            if result:
                logger.success(f"下单成功: {side} {outcome} ${amount} @ {price}")
                return result
            else:
                logger.error("下单失败")
                return None

        except Exception as e:
            logger.error(f"下单异常: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        try:
            result = self.client.cancel_order(order_id)
            if result:
                logger.success(f"订单取消成功: {order_id}")
                return True
            else:
                logger.error(f"订单取消失败: {order_id}")
                return False
        except Exception as e:
            logger.error(f"取消订单异常: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """取消账户下所有未成交订单 (三重保险版)"""
        try:
            success = True
            # 1. 尝试全局撤单
            logger.info("正在调用 SDK 全局撤单 API...")
            res = self.client.cancel_all_orders()
            
            # 2. 获取当前账户所有挂单，进行二次确认/手动清理
            # 这样可以确保即使全局 API 漏掉了某些单子也能清空
            remains = self.get_my_orders()
            if remains and len(remains) > 0:
                logger.warning(f"检测到仍然存在 {len(remains)} 个残余订单，开始手工清理...")
                for order in remains:
                    order_id = getattr(order, 'order_id', None) or getattr(order, 'id', None)
                    if order_id:
                        if self.client.cancel_order(order_id):
                            logger.info(f"成功清理残余订单: {order_id}")
                        else:
                            logger.error(f"清理残余订单失败: {order_id}")
                            success = False
            
            if success:
                logger.success("该账号下所有订单已清理完毕")
            return success
        except Exception as e:
            logger.error(f"批量撤单过程发生异常: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """查询订单状态"""
        try:
            result = self.client.get_order_by_id(order_id)
            return result
        except Exception as e:
            logger.error(f"查询订单状态异常: {e}")
            return None

    def check_order_status(self, order_id: str) -> Optional[Dict]:
        """检查订单状态（别名方法，用于兼容性）"""
        return self.get_order_status(order_id)

    def get_balance(self) -> Optional[float]:
        """获取账户余额"""
        try:
            result = self.client.get_my_balances()
            if result:
                balance = float(getattr(result, 'balance', 0) or 0)
                logger.info(f"账户余额: ${balance:.2f}")
                return balance
            return None
        except Exception as e:
            logger.error(f"获取余额异常: {e}")
            return None

    def get_my_orders(self, market_id: int = 0, status: str = "", limit: int = 50) -> Optional[list]:
        """获取我的订单列表"""
        try:
            result = self.client.get_my_orders(market_id=market_id, status=status, limit=limit)
            if result:
                if hasattr(result, 'errno') and result.errno != 0:
                    logger.error(f"获取订单失败: {result.errmsg}")
                    return None
                if hasattr(result, 'result') and hasattr(result.result, 'list'):
                    orders = result.result.list or []
                    logger.debug(f"获取到 {len(orders)} 个订单")
                    return orders
            return []
        except Exception as e:
            logger.error(f"获取订单列表异常: {e}")
            return None

    def is_order_filled(self, order_id: str) -> bool:
        """检查订单是否已成交"""
        try:
            orders = self.get_my_orders()
            if not orders:
                return False
            for order in orders:
                if getattr(order, 'order_id', '') == order_id:
                    status = getattr(order, 'status', '')
                    logger.debug(f"订单 {order_id} 状态: {status}")
                    return str(status).lower() == 'filled'
            return False
        except Exception as e:
            logger.error(f"检查订单状态异常: {e}")
            return False

    def get_positions(self, topic_id: int = None) -> Optional[list]:
        """获取持仓列表"""
        try:
            result = self.client.get_my_positions()
            if result:
                if hasattr(result, 'errno') and result.errno != 0:
                    logger.error(f"获取持仓失败: {result.errmsg}")
                    return None
                positions = getattr(result, 'result', None)
                if positions:
                    pos_list = getattr(positions, 'list', []) or []
                    if topic_id:
                        # 筛选指定市场的持仓 (使用 market_id)
                        pos_list = [p for p in pos_list if getattr(p, 'market_id', None) == topic_id]
                    return pos_list
            return []
        except Exception as e:
            logger.error(f"获取持仓异常: {e}")
            return None

    def get_position_amount(self, topic_id: int, outcome: str = "YES") -> float:
        """获取指定市场的持仓数量"""
        try:
            positions = self.get_positions(topic_id)
            if not positions:
                return 0.0
            
            is_yes = outcome.upper() == "YES"
            for pos in positions:
                # 检查 outcome_side: 1=Yes, 2=No
                pos_outcome_side = getattr(pos, 'outcome_side', 0)
                pos_shares = getattr(pos, 'shares_owned', '0')
                
                try:
                    shares = float(pos_shares)
                except:
                    shares = 0.0
                
                if is_yes and pos_outcome_side == 1:
                    logger.info(f"找到持仓: market={topic_id} YES shares={shares}")
                    return shares
                elif not is_yes and pos_outcome_side == 2:
                    logger.info(f"找到持仓: market={topic_id} NO shares={shares}")
                    return shares
            
            return 0.0
        except Exception as e:
            logger.error(f"获取持仓数量异常: {e}")
            return 0.0


OpinionTrader = OpinionTraderSDK
