# Solo Market说明

本文档详细说明了 `solomarket.py` 脚本的核心执行逻辑和设计原理。
## Author @0xwenjian
---

## 核心配置参数

- `min_protection_amount`: 最小前方保护金额（如 $500）
- `check_bid_position`: **首次下单**的档位限制（如 5，表示首次下单最多只能挂在买5）
- `order_amount`: 每次挂单金额（如 $30）

---

## 一、价格计算逻辑 (`calculate_safe_price`)

### 核心实现

```python
def calculate_safe_price(order_book, max_rank=None):
    """
    遍历订单簿，找到第一个满足保护要求的档位
    
    参数:
        max_rank: 搜索范围限制（仅用于首次下单）
                 如果指定，仅在 [1, max_rank] 范围内搜索
                 如果不指定，搜索整个订单簿
    """
    cumulative_protection = 0.0
    for i, level in enumerate(order_book.bids):
        rank = i + 1
        
        # 如果指定了 max_rank，超出范围就停止搜索
        if max_rank and rank > max_rank:
            break
        
        cumulative_protection += level.total
        
        if cumulative_protection >= min_protection:
            # 修正逻辑：买1特殊处理
            if rank == 1:
                # 买1满足保护 -> 挂在买1价 - 0.001
                target_price = level.price - 0.001
            else:
                # 买2及以下 -> 匹配该档位价格
                target_price = level.price
            
            return target_price, rank
    
    return None
```

### 举例说明

#### 场景 1：买1满足保护

订单簿（真实数据格式）：
```
买1: 0.3520 @ $800
买2: 0.3510 @ $400
买3: 0.3500 @ $300
```

**执行结果**：
- 遍历到买1，累计保护 = $800 >= $500 ✓
- rank = 1，触发买1特殊逻辑
- **挂单价格 = 0.3520 - 0.001 = 0.3510**
- **挂单档位 = 买2**（因为 0.3510 就是买2的价格）
- **前方保护 = $800**（整个买1档位都在我们前面）

#### 场景 2：买2满足保护

订单簿：
```
买1: 0.3520 @ $300
买2: 0.3510 @ $400
买3: 0.3500 @ $300
```

**执行结果**：
- 遍历到买1，累计保护 = $300 < $500 ✗
- 遍历到买2，累计保护 = $700 >= $500 ✓
- rank = 2，不触发买1特殊逻辑
- **挂单价格 = 0.3510**（买2价）
- **挂单档位 = 买2**
- **前方保护 = $300**（买1的金额）

---

## 二、初始下单逻辑 (`place_order`)

### 核心实现

```python
def place_order(topic_id):
    # 获取订单簿
    order_book = fetch_orderbook(...)
    
    # 首次下单：限制在 check_bid_position 内寻找安全位置
    calc_res = calculate_safe_price(order_book, max_rank=self.max_rank)
    
    if not calc_res:
        logger.warning(f"无法在限制档位 {self.max_rank} 内找到安全价格")
        return False
    
    price, rank = calc_res
    
    # 下单
    trader.place_order(price=price, ...)
```

### 举例说明

假设 `check_bid_position = 5`，订单簿如下：
```
买1: 0.3520 @ $800
买2: 0.3510 @ $400
买3: 0.3500 @ $300
买4: 0.3490 @ $200
买5: 0.3480 @ $100
```

**执行结果**：
- 搜索范围：买1 到 买5
- 买1累计保护 $800 >= $500 ✓
- rank = 1，触发买1特殊逻辑
- **挂单价格 = 0.3520 - 0.001 = 0.3510**
- **挂单档位 = 买2**（35.1¢，在限制内）
- **前方保护 = $800**

---

## 三、订单调整逻辑 (`check_and_adjust_order`)

### 触发条件

#### 触发器 A：保护不足（全局扫描）

```python
current_rank, current_protection = _get_rank_and_protection(...)

if current_protection < min_protection:
    needs_adjust = True
    reason = "保护不足"
    
    # 全局扫描（不限档位）
    calc_res = calculate_safe_price(order_book)  # 无 max_rank 限制
```

**举例**：
- 当前挂在 0.3500（买3价位），前方保护 $400
- 买1、买2被吃掉，前方保护变为 $0
- **触发全局扫描**，可能移动到买10甚至更深
- 无论移动到哪里，如果是买1满足保护，依然会挂在 **买1价 - 0.001**

> [!IMPORTANT]
> 如果我们的订单被成交，必须在 log 文件中记录成交信息（价格、金额、时间等）。

#### 触发器 B：档位超标（范围内补位）

```python
elif current_rank > self.max_rank:
    needs_adjust = True
    reason = "档位超标"
    
    # 在 [1, max_rank] 范围内寻找最佳位置
    calc_res = calculate_safe_price(order_book, max_rank=self.max_rank)
```

**举例**：
- **前提条件**：当前挂单档位 >= 买6（超出 check_bid_position = 5）
- 当前挂在 0.3460（买6价位），`check_bid_position = 5`
- 买6 > 5，触发档位超标
- **扫描买1-买5**，找到第一个满足保护的位置
- 如果买1满足保护，挂单价格 = **买1价 - 0.001**

---

## 四、保护金额计算逻辑

### 核心实现

```python
def _get_rank_and_protection(order_book, side, price):
    rank = 1
    # 调用 get_protection_amount，会减去自己的订单金额
    protection = order_book.get_protection_amount(side, price, self.order_amount)
    
    for level in order_book.bids:
        if level.price > price + 0.00001:
            rank += 1
        else:
            break
    
    return rank, protection
```

### 举例（基于修正后的价格）

订单簿：
```
买1: 0.3520 @ $800
买2: 0.3510 @ $400
```

我们的挂单：**0.3510** @ $30

**计算结果**：
- **我们的挂单价位**：0.3510（35.1¢）
- **前方保护**：$800（整个买1档位，因为 0.3510 < 0.3520）

---

## 五、核心逻辑总结

### 价格策略

1. **买1满足保护** → 挂单价格 = `买1价 - 0.001`
   - 挂单档位：买2（因为买1价 - 0.001 通常等于买2价）
   - 前方保护：整个买1档位的金额
   
2. **买2及以下满足保护** → 挂单价格 = `该档位价格`
   - 挂单档位：该档位
   - 前方保护：该档位之前所有档位的累计金额

### 档位限制

- `check_bid_position` **仅用于首次下单**
- 调整订单时：
  - **保护不足** → 全局扫描（无档位限制）
  - **档位超标**（当前档位 > check_bid_position）→ 在 [1, check_bid_position] 范围内补位

### 关键特性

✅ **永不直接挂买1价**：即使买1满足保护，也会挂在 `买1价 - 0.001`（低0.1¢）

✅ **最优情况**：挂在买1价 - 0.001 的价位，前方有整个买1档位保护

✅ **档位超标触发条件明确**：只有当前档位 > check_bid_position 时才触发补位

---

## 六、完整流程示例

### 示例 1：首次下单

**配置**：
- `check_bid_position = 5`
- `min_protection = 500`

**订单簿**：
```
买1: 0.3520 @ $800
买2: 0.3510 @ $400
买3: 0.3500 @ $300
```

**执行流程**：
1. 搜索范围：买1-买5
2. 买1累计保护 $800 >= $500 ✓
3. rank = 1，触发买1特殊逻辑
4. **下单：0.3510 @ $30**（35.1¢）
5. **挂单档位：买2**
6. **前方保护：$800**

### 示例 2：档位超标触发补位

**当前状态**：
- 挂单：0.3460 @ $30（买6价位）
- `check_bid_position = 5`

**订单簿变化**：
```
买1: 0.3520 @ $800
买2: 0.3510 @ $400
买3: 0.3500 @ $300
买4: 0.3490 @ $200
买5: 0.3480 @ $100
买6: 0.3460 @ $50  <- 我们在这里
```

**执行流程**：
1. 检测到：买6 > 5，触发档位超标
2. 扫描买1-买5
3. 买1累计保护 $800 >= $500 ✓
4. **撤单并重新下单：0.3510 @ $30**（35.1¢）
5. **新挂单档位：买2**
6. **前方保护：$800**

---

## 七、设计原则

### 1. 安全优先

脚本的核心目标是**提供流动性而非成交**，所有逻辑都围绕这一原则设计：
- 买1特殊处理避免直接挂在最优价
- 保护金额机制确保前方有足够缓冲
- 双重触发器确保订单始终处于安全位置

### 2. 灵活应对

- **首次下单**：严格限制在 `check_bid_position` 范围内
- **保护不足**：全局扫描，不受档位限制（保命优先）
- **档位超标**：范围内补位，平衡安全与竞争力

### 3. 透明可控

- 详细的日志记录
- 清晰的触发条件
- 可配置的参数

---

**本文档完整描述了 Solo Market 脚本的执行逻辑和设计原理。**
