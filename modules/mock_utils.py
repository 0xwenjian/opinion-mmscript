
from loguru import logger
from .models import OrderBook, OrderBookLevel

class MockFetcher:
    """模拟市场信息抓取层"""
    def __init__(self, parent=None):
        self.parent = parent
        self.mock_ob = OrderBook(
            bids=[
                OrderBookLevel(0.80, 1000, 800),
                OrderBookLevel(0.79, 1000, 790),
                OrderBookLevel(0.78, 1000, 780),
                OrderBookLevel(0.77, 1000, 770),
                OrderBookLevel(0.76, 1000, 760),
                OrderBookLevel(0.75, 1000, 750),
                OrderBookLevel(0.74, 1000, 740),
                OrderBookLevel(0.73, 1000, 730),
                OrderBookLevel(0.72, 1000, 720),
                OrderBookLevel(0.71, 1000, 710),
            ],
            asks=[OrderBookLevel(0.81, 1000, 810)],
            best_bid=0.80,
            best_ask=0.81
        )

    def set_mock_bid(self, index: int, price: float, size: float):
        if index < len(self.mock_ob.bids):
            self.mock_ob.bids[index].price = price
            self.mock_ob.bids[index].size = size
            self.mock_ob.bids[index].total = price * size
            self.mock_ob.bids.sort(key=lambda x: x.price, reverse=True)
            self.mock_ob.best_bid = self.mock_ob.bids[0].price

    def shift_book(self, offset: float):
        """整体平移盘口"""
        for level in self.mock_ob.bids:
            level.price = round(level.price + offset, 4)
            level.total = level.price * level.size
        for level in self.mock_ob.asks:
            level.price = round(level.price + offset, 4)
            level.total = level.price * level.size
        self.mock_ob.best_bid = self.mock_ob.bids[0].price
        self.mock_ob.best_ask = self.mock_ob.asks[0].price


class MockTrader:
    """模拟交易执行层"""
    def __init__(self):
        self.mock_fetcher = None
        self.orders = {}
        self.counter = 1000
    
    class MockClient:
        def __init__(self, fetcher): self.fetcher = fetcher
        def get_orderbook(self, token_id):
            class Res: pass
            res = Res(); res.result = self.fetcher.mock_ob
            return res

    def set_fetcher(self, fetcher):
        self.mock_fetcher = fetcher
        self.client = self.MockClient(fetcher)

    def get_market_by_topic_id(self, topic_id):
        return {
            "title": f"Mock Market {topic_id}",
            "yes_token_id": "mock_yes",
            "no_token_id": "mock_no"
        }

    def place_order(self, **kwargs):
        self.counter += 1
        order_id = f"mock_order_{self.counter}"
        logger.debug(f"[MockTrader] 下单: {kwargs['price']} {kwargs['outcome']}")
        return type('Obj', (object,), {'order_id': order_id, 'result': None})

    def cancel_order(self, order_id):
        logger.debug(f"[MockTrader] 撤单: {order_id}")
        return True

    def set_mock_order_status(self, order_id: str, status: any, filled_amount: float = 0.0):
        """设置模拟订单的状态和成交金额"""
        self.orders[order_id] = {
            "status": status,
            "filled_amount": filled_amount
        }

    def check_order_status(self, order_id):
        """查询模拟订单状态"""
        order_info = self.orders.get(order_id, {"status": "open", "filled_amount": 0.0})
        
        # 构造符合 SDK 结构的返回对象
        class OrderData:
            def __init__(self, status, filled_amount):
                self.status = status
                self.filled_amount = filled_amount
        
        class Result:
            def __init__(self, data):
                self.order_data = data
        
        class Response:
            def __init__(self, res):
                self.result = res
                self.status = res.order_data.status # 兼容层
        
        data = OrderData(order_info["status"], order_info["filled_amount"])
        res = Result(data)
        return Response(res)
