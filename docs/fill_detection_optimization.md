# 订单成交检测逻辑优化建议

## 1. 现状问题分析

目前的 `solomarket.py` 脚本在检测成交时存在一个**关键疏漏**：它仅依赖订单的 `status` 字段是否等于 `3` (即 `Filled`/全额成交) 来判定成交。

**漏判场景：**
*   **部分成交后撤单**：一个 120u 的订单，成交了 119.37u，但因为价格波动或机器人主动调整，剩下的 0.63u 被撤单了。此时 API 返回的最终状态是 `canceled`，但实际上 99% 的资金已经换成了仓位。
*   **部分成交中**：订单正在被蚕食，状态还是 `2` (Pending)，但已经产生了实际持仓。

如果机器人只认 `status=3`，就会漏掉这些“非预期持仓”，导致：
1.  **不报警**：账号多出了仓位，TG 却没通知。
2.  **重复下单**：机器人以为没成，撤单后又下了一单，导致总仓位翻倍，风险失控。

---

## 2. 优化方案：基于“成交量”而非“状态码”

我们需要将判断逻辑从 **“状态是否为已成交”** 转向 **“已成交数量是否大于 0”**。

### 核心修改逻辑
在 `check_and_adjust_order` 中，无论 `status` 是什么，都要检查 `filled_amount` (或 `executed_shares`)。

#### 代码实现思路：
```python
# 修改前的逻辑
if status in [3, '3', 'filled', 'FILLED']:
    # 触发报警...

# 建议修改后的逻辑 (伪代码)
filled_amount = getattr(result_data.order_data, 'filled_amount', 0)
status = getattr(result_data.order_data, 'status', None)

# 只要有成交，就应该处理
if float(filled_amount) > 0:
    # 这种情况即包含全额成交(status=3)，也包含部分成交后被撤单(status=canceled)
    is_fully_filled = (status in [3, '3', 'filled', 'FILLED'])
    
    duration = int(time.time() - order.create_time)
    logger.warning(f"⚠️ [发现成交] {order.title} | 成交金额: ${filled_amount}/{order.amount} | 状态: {status}")
    
    # 发送更详细的 TG 通知...
    # 然后从本地列表中移除，防止重复监控
    del self.orders[topic_id]
```

---

## 3. 如何进行模拟测试？

为了验证这个优化，你需要模拟一个“已撤单但有成交量”的场景。

### 步骤 A：修改 `modules/mock_utils.py`
在模拟器中增加一个特殊的 Test Case：
1.  创建一个 Mock 对象，令其 `status` 返回 `canceled`。
2.  令其 `result.order_data.filled_amount` 返回一个大于 0 的数值（例如 `119.37`）。

### 步骤 B：编写测试脚本 `test_partial_fill.py`
```python
# test_partial_fill.py 核心片段
def test_partial_fill_detection():
    # 1. 构造一个模拟订单
    monitor.orders[5055] = SoloMarketOrder(order_id="test-id", ...)
    
    # 2. 强制模拟器返回“已撤单但部分成交”的状态
    monitor.trader.set_mock_order_status("test-id", status="canceled", filled_amount=119.37)
    
    # 3. 运行检查逻辑
    monitor.check_and_adjust_order(5055)
    
    # 4. 断言：5055 应该被从监控列表中移除，且触发了日志/通知
    assert 5055 not in monitor.orders
```

---

## 4. 优化后的收益

1.  **仓位零漏报**：即使是只成交了 0.01u 的“蚊子肉”，机器人也能感知到并及时报警，方便你手动对冲。
2.  **资金安全**：防止了在“部分成交”后机器人误判订单失效而重复补单，避免了仓位过载。
3.  **对账准确**：配合 `get_my_trades` 接口，可以准确计算每一笔交易的平均成本，为后续的自动对冲或平仓逻辑打下基础。

**建议：** 立即在 `solomarket.py` 中寻找 `check_order_status` 相关的代码块，并引入 `filled_amount` 的数值判断。
