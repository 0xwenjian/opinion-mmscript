"""
OpinionLabs 自动签名模块
功能：使用私钥自动生成 SIWE 签名
"""

import time
import secrets
from typing import Dict
from eth_account import Account
from eth_account.messages import encode_defunct
from loguru import logger


class OpinionSigner:
    """OpinionLabs 自动签名器"""

    def __init__(self, private_key: str):
        self.account = Account.from_key(private_key)
        self.wallet_address = self.account.address
        logger.info(f"签名器初始化成功，钱包地址: {self.wallet_address}")

    def generate_siwe_message(self, nonce: str, timestamp: int) -> str:
        issued_at = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(timestamp))

        message = (
            f"app.opinion.trade wants you to sign in with your Ethereum account:\n"
            f"{self.wallet_address}\n"
            f"\n"
            f"Welcome to opinion.trade! By proceeding, you agree to our Privacy Policy and Terms of Use.\n"
            f"\n"
            f"URI: https://app.opinion.trade\n"
            f"Version: 1\n"
            f"Chain ID: 56\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {issued_at}"
        )

        return message

    def sign_message(self, message: str) -> str:
        encoded_message = encode_defunct(text=message)
        signed_message = self.account.sign_message(encoded_message)
        signature = signed_message.signature.hex()
        if signature.startswith('0x'):
            signature = signature[2:]
        return signature

    def generate_login_payload(self) -> Dict:
        nonce = secrets.token_hex(8)
        timestamp = int(time.time())
        siwe_message = self.generate_siwe_message(nonce, timestamp)
        signature = self.sign_message(siwe_message)

        payload = {
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": signature,
            "siwe_message": siwe_message,
            "sign_in_wallet_plugin": "com.okex.wallet",
            "sources": "web"
        }

        logger.debug(f"生成登录 payload: nonce={nonce}, timestamp={timestamp}")
        return payload
