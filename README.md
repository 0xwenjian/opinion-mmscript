# Opinion 做市刷积分机器人

本项目是一个专为 Opinion 平台设计的做市机器人，旨在通过提供市场流动性来赚取平台积分。

## 📂 项目结构

### 核心程序
- **`solomarket.py`**: **（推荐）** 单市场/多市场深度监控策略。专注于在订单簿深处寻找安全空位挂单，避开成交风险。
- **`main.py`**: 传统的 Maker 做市策略主入口。
- **`config.yaml`**: 全局配置文件，包含市场 ID、保护金额、TG 通知等模式。

### 🛠 功能脚本 (`scripts/`)
- **`find_markets.py`**: 自动扫描全平台活跃市场，按流动性和价格推荐最适合刷分的市场 ID。**（配置前必运行）**
- **`cancel_orders.py`**: 一键紧急撤销所有账户挂单。
- **`sell_all.py`**: 一键清空账户所有 YES/NO 持仓。
- **`fetch_my_trades.py`**: 打印最近的详细成交记录，包含成交均价和手续费。
- **`diagnose_network.py`**: 诊断本地网络、代理以及与 Opinion API 的连接状态。

### 📚 文档说明 (`docs/`)
- **`solomarket说明.md`**: 详细拆解了 `solomarket.py` 的“买1插队”逻辑和自动补位触发器。

### 🧪 测试与开发 (`test/`)
- 包含模拟器（Simulation）、订单簿解析、以及下单逻辑的单元测试脚本。
---

## 🚀 快速开始

### 1. 环境准备
建议使用虚拟环境以确保依赖隔离：

**Mac / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境 (根据你的终端类型选择)
.\venv\Scripts\activate.ps1      # 如果是 PowerShell (推荐)
venv\Scripts\activate            # 如果是 CMD

# 3. 安装依赖
pip install -r requirements.txt
```

### 2. 配置账户
在项目根目录创建 `.env` 文件（或修改现有文件）：
```env
OPINION_PRIVATE_KEY=你的私钥
OPINION_APIKEY=你的APIKEY
OPINION_WALLET_ADDRESS=你的钱包地址
```

### 3. 筛选市场
运行脚本获取推荐的市场 ID：
```bash
python3 scripts/find_markets.py
```
将输出的 ID 填入 `config.yaml` 的 `topic_ids` 列表中。

### 4. 运行机器人

#### 方式 A：单账号启动
直接运行主脚本：
```bash
python3 solomarket.py
```

#### 方式 B：多账号启动
你可以为不同的账号准备不同的环境和配置文件，例如：
- 账号1: `python3 solomarket.py --env-file account_1.env --config-file account_1.config.yaml`
- 账号2: `python3 solomarket.py --env-file account_2.env --config-file account_2.config.yaml`
指令中的文件名可以根据你的实际需求自定义。

---

## 🛡 核心特性：Solo 监控逻辑

*   **安全位置探测**：自动计算前方买单的累计金额，确保你的订单前面始终有足够的“肉垫”（`min_protection_amount`）。
*   **买1避让机制**：如果买1深度足够，会自动在买2位置插队（`买1价 - 0.001`），既保证排名靠前又不承担直接撞单风险。
*   **全量错误报警**：脚本意外崩溃或网络断开时，会通过 Telegram 将错误堆栈和**钱包地址**第一时间推送到你手机。
*   **成交检测优化**：即使订单处于 `canceled` 状态，只要有 `filled_amount > 0` 也会立即报警，防止漏掉任何一笔非预期成交。

---

## ⚠️ 免责声明
本工具仅用于技术交流和参加平台积分活动。做市交易存在风险，尤其是在极端波动行情下，请务必根据个人风险承受能力设置 `order_amount` 和 `min_protection_amount`。
