
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class OrderBookLevel:
    """订单簿价位"""
    price: float
    size: float
    total: float

@dataclass
class OrderBook:
    """订单簿"""
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    best_bid: float = 0.0
    best_ask: float = 0.0
    
    def get_protection_amount(self, side: str, price: float, order_amount: float = 0.0) -> float:
        """计算目标价位前方的累计挂单金额（保护厚度）"""
        total = 0.0
        if side == "BUY":
            for level in self.bids:
                if level.price > price + 0.00001:
                    total += level.total
                elif abs(level.price - price) < 0.00001:
                    total += 0 
                else:
                    break
        else:
            for level in self.asks:
                if level.price < price - 0.00001:
                    total += level.total
                elif abs(level.price - price) < 0.00001:
                    total += 0
                else:
                    break
        return total

@dataclass
class SoloMarketOrder:
    """订单记录"""
    order_id: str
    topic_id: int
    title: str
    price: float
    amount: float
    create_time: float
    last_check_time: float = 0.0
