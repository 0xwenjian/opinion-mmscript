#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpinionLabs 数据抓取模块（修正版）
功能：
1) 自动/手动登录获取 JWT
2) 抓取活跃市场（支持单选、多选）
3) 自动生成网页链接
"""

import time
import secrets
from typing import Dict, List, Optional

import requests
from loguru import logger

# 自动签名模块（可选）
try:
    from modules.auto_signer import OpinionSigner
    AUTO_SIGN_AVAILABLE = True
except Exception:
    AUTO_SIGN_AVAILABLE = False


class OpinionFetcher:
    """OpinionLabs 数据抓取器"""

    BASE_URL = "https://proxy.opinion.trade:8443/api/bsc"
    FRONTEND_TOPIC = "https://app.opinion.trade/topic"
    FRONTEND_QUESTION = "https://app.opinion.trade/question"
    FRONTEND_BASE = "https://app.opinion.trade/detail"

    def __init__(
        self,
        wallet_address: str = "",
        sign: str = "",
        siwe_message: str = "",
        proxy: Optional[Dict] = None,
        token: Optional[str] = None,
        private_key: Optional[str] = None,
        apikey: Optional[str] = None,
        **kwargs,
    ) -> None:
        self.wallet_address = wallet_address
        self.sign = sign
        self.siwe_message = siwe_message
        self.proxy = proxy
        self.token = token
        self.token_expiry = 0 if not token else time.time() + 24 * 3600
        self.apikey = apikey
        if kwargs:
            logger.debug(f"OpinionFetcher 忽略多余参数: {list(kwargs.keys())}")

        self.signer = None
        if private_key and AUTO_SIGN_AVAILABLE:
            try:
                self.signer = OpinionSigner(private_key)
                self.wallet_address = self.signer.wallet_address
                logger.success(f"自动签名启用，地址: {self.wallet_address}")
            except Exception as e:
                logger.error(f"初始化自动签名失败: {e}")
                self.signer = None

    def _generate_nonce(self) -> str:
        return secrets.token_hex(8)

    def get_token(self) -> str:
        if self.token and time.time() < self.token_expiry:
            return self.token

        url = f"{self.BASE_URL}/api/v1/user/token"

        if self.signer:
            logger.info("使用自动签名登录 OpinionLabs...")
            payload = self.signer.generate_login_payload()
        elif self.sign and self.siwe_message:
            logger.info("使用手动签名登录 OpinionLabs...")
            payload = {
                "nonce": self._generate_nonce(),
                "timestamp": int(time.time()),
                "sign": self.sign,
                "siwe_message": self.siwe_message,
                "sign_in_wallet_plugin": "com.okex.wallet",
                "sources": "web",
            }
        else:
            raise Exception("缺少登录凭证：请提供 private_key 或 (sign + siwe_message)")

        headers = {
            "Content-Type": "application/json",
            "Origin": "https://app.opinion.trade",
            "Referer": "https://app.opinion.trade/",
            "User-Agent": "Mozilla/5.0",
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, proxies=self.proxy, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("errno") == 0 and isinstance(data.get("result"), dict):
                token = data["result"].get("token")
                expire = data["result"].get("expire")
                if not token:
                    raise Exception("响应缺少 token")
                self.token = token
                self.token_expiry = expire if isinstance(expire, (int, float)) else time.time() + 24 * 3600
                logger.success("登录成功，已获取 Token")
                return self.token
            raise Exception(f"登录失败: {data}")
        except requests.RequestException as e:
            logger.error(f"登录请求失败: {e}")
            raise

    def _build_detail_url(self, topic_id: Optional[int] = None, is_multi: bool = False) -> str:
        if not topic_id:
            return ""
        if is_multi:
            return f"{self.FRONTEND_BASE}?topicId={topic_id}&type=multi"
        return f"{self.FRONTEND_BASE}?topicId={topic_id}"

    def fetch_markets(self, limit: int = 20, fetch_all: bool = True) -> List[Dict]:
        token = self.get_token()
        url = f"{self.BASE_URL}/api/v2/topic"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://app.opinion.trade",
            "Referer": "https://app.opinion.trade/",
            "User-Agent": "Mozilla/5.0",
        }

        base_params = {
            "labelId": "",
            "keywords": "",
            "sortBy": 3,
            "chainId": 56,
            "limit": min(20, max(1, int(limit))),
            "status": 2,
            "isShow": 1,
            "topicType": 2,
            "indicatorType": 2,
        }

        markets: List[Dict] = []
        page = 1
        total_expected: Optional[int] = None

        while True:
            params = dict(base_params)
            params["page"] = page
            response = requests.get(url, headers=headers, params=params, proxies=self.proxy, timeout=10)

            if response.status_code == 401:
                self.token = None
                self.token_expiry = 0
                token = self.get_token()
                headers["Authorization"] = f"Bearer {token}"
                continue

            response.raise_for_status()
            data = response.json()

            if not (isinstance(data, dict) and data.get("errno") == 0 and isinstance(data.get("result"), dict)):
                logger.error(f"获取市场数据失败: {data}")
                break

            result = data["result"]
            items = result.get("list", []) or []
            if total_expected is None:
                try:
                    total_expected = int(result.get("total") or 0)
                except Exception:
                    total_expected = None

            for t in items:
                if not isinstance(t, dict):
                    continue

                title = (t.get("title") or "").strip()
                topic_id = t.get("topicId") or t.get("id") or 0
                volume = float(t.get("volume", 0) or 0)
                cutoff = t.get("cutoffTime")

                try:
                    end_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(cutoff))) if cutoff else ""
                except Exception:
                    end_iso = ""

                child_list = t.get("childList") or []
                is_multi = isinstance(child_list, list) and len(child_list) > 0

                if is_multi:
                    for child in child_list:
                        try:
                            opt_title = child.get("title") or f"{title} - Option"
                            yp = child.get("yesMarketPrice") or child.get("yesBuyPrice") or 0
                            yes_price = float(yp) if yp not in ("", None) else 0.0
                            child_qid = child.get("id") or child.get("questionId") or ""
                            # 尝试获取整数形式的 market_id
                            child_market_id = child.get("marketId") or child.get("market_id") or child.get("questionId") or ""
                            parent_tid = t.get("topicId") or t.get("id") or ""
                            # 获取 tokenId (用于下单)
                            child_token_id = child.get("yesTokenId") or child.get("yes_token_id") or child_qid or ""
                            market_item = {
                                "title": f"{title} - {opt_title}",
                                "yesPrice": yes_price,
                                "volume": volume,
                                "endTime": end_iso,
                                "platform": "OpinionLabs",
                                "marketId": child_market_id or child_qid or parent_tid,
                                "topicId": parent_tid,
                                "questionId": child_qid,
                                "tokenId": str(child_token_id or child_qid or parent_tid or "0"),
                                "yesTokenId": child_token_id,
                                "isMulti": True,
                                "url": self._build_detail_url(topic_id=parent_tid, is_multi=True),
                            }
                            markets.append(market_item)
                        except Exception:
                            continue
                    continue

                yp = t.get("yesMarketPrice") or t.get("yesBuyPrice") or t.get("yesPrice") or 0
                try:
                    yes_price = float(yp) if yp not in ("", None) else 0.0
                except Exception:
                    yes_price = 0.0

                qid = t.get("id") or t.get("questionId") or 0
                market_item = {
                    "title": title,
                    "yesPrice": yes_price,
                    "volume": volume,
                    "endTime": end_iso,
                    "platform": "OpinionLabs",
                    "marketId": (t.get("id") or t.get("questionId") or topic_id),
                    "topicId": topic_id,
                    "questionId": qid,
                    "tokenId": str(qid or topic_id or "0"),
                    "isMulti": False,
                    "url": self._build_detail_url(topic_id=topic_id, is_multi=False),
                }
                markets.append(market_item)

            if not fetch_all:
                break
            if len(items) < base_params["limit"]:
                break
            if total_expected and page * base_params["limit"] >= total_expected:
                break
            page += 1

        logger.info(f"OpinionLabs: {len(markets)} 个市场")
        return markets

    def fetch_market_by_id(self, topic_id: int) -> Optional[Dict]:
        """获取单个市场详情，包含买卖价差"""
        token = self.get_token()
        url = f"{self.BASE_URL}/api/v2/topic/{topic_id}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Origin": "https://app.opinion.trade",
            "Referer": "https://app.opinion.trade/",
            "User-Agent": "Mozilla/5.0",
        }

        try:
            response = requests.get(url, headers=headers, proxies=self.proxy, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("errno") == 0 and data.get("result"):
                result = data["result"]
                
                # 获取买入价和卖出价
                yes_buy = result.get("yesBuyPrice") or result.get("yesMarketPrice") or result.get("yesPrice") or 0
                yes_sell = result.get("yesSellPrice") or yes_buy
                
                try:
                    yes_buy_price = float(yes_buy) if yes_buy not in ("", None) else 0.0
                    yes_sell_price = float(yes_sell) if yes_sell not in ("", None) else yes_buy_price
                except Exception:
                    yes_buy_price = 0.0
                    yes_sell_price = 0.0
                
                # 计算价差
                spread = abs(yes_sell_price - yes_buy_price) if yes_buy_price > 0 else 0

                return {
                    "topicId": result.get("topicId") or result.get("id") or topic_id,
                    "title": result.get("title", ""),
                    "yesPrice": yes_buy_price,
                    "yesBuyPrice": yes_buy_price,
                    "yesSellPrice": yes_sell_price,
                    "spread": spread,
                    "volume": float(result.get("volume", 0) or 0),
                }
            return None
        except Exception as e:
            logger.error(f"获取市场 {topic_id} 详情失败: {e}")
            return None

    def fetch_orderbook(self, topic_id: int, outcome: str = "YES") -> Optional[Dict]:
        """
        获取订单簿数据
        
        Args:
            topic_id: 市场 ID
            outcome: YES 或 NO
            
        Returns:
            订单簿数据，包含 bids 和 asks
        """
        token = self.get_token()
        
        # 尝试多个可能的 API 端点
        endpoints = [
            f"{self.BASE_URL}/api/v2/topic/{topic_id}/orderbook",
            f"{self.BASE_URL}/api/v1/orderbook/{topic_id}",
            f"{self.BASE_URL}/api/v2/orderbook",
        ]
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Origin": "https://app.opinion.trade",
            "Referer": "https://app.opinion.trade/",
            "User-Agent": "Mozilla/5.0",
        }
        
        for url in endpoints:
            try:
                params = {"outcome": outcome} if "orderbook" in url else {}
                response = requests.get(url, headers=headers, params=params, proxies=self.proxy, timeout=10)
                
                if response.status_code == 404:
                    continue
                    
                response.raise_for_status()
                data = response.json()
                
                if data.get("errno") == 0 and data.get("result"):
                    result = data["result"]
                    
                    bids = result.get("bids", []) or []
                    asks = result.get("asks", []) or []
                    
                    # 标准化订单簿格式
                    return {
                        "bids": [{"price": float(b.get("price", 0)), "size": float(b.get("size", 0))} for b in bids],
                        "asks": [{"price": float(a.get("price", 0)), "size": float(a.get("size", 0))} for a in asks],
                        "best_bid": float(bids[0].get("price", 0)) if bids else 0.0,
                        "best_ask": float(asks[0].get("price", 0)) if asks else 1.0,
                        "timestamp": time.time(),
                    }
                    
            except requests.RequestException:
                continue
            except Exception as e:
                logger.debug(f"获取订单簿失败 ({url}): {e}")
                continue
        
        # 如果所有端点都失败，返回 None
        logger.debug(f"无法获取市场 {topic_id} 的订单簿")
        return None
