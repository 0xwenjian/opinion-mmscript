# Opinion 快速限价交易机器人

## 文件说明

- `trader.py` - 完整版交易机器人（带状态管理、风控）
- `fast_trader.py` - 极速简化版（最小延迟）
- `config.yaml` - 配置文件
- `start.bat` - Windows启动脚本

## 快速开始

### 1. 修改配置

编辑 `config.yaml`，设置 `topic_id`（从Opinion网站获取市场ID）

### 2. 运行

**方式一：使用配置文件**
```bash
python trader.py
```

**方式二：命令行参数（推荐快速交易）**
```bash
python fast_trader.py --topic 123 --buy 0.90 --sell 0.93 --amount 10
```

参数说明：
- `--topic/-t`: 市场ID（必填）
- `--buy/-b`: 买入价格，如0.90表示90%
- `--sell/-s`: 卖出价格，如0.93表示93%
- `--amount/-a`: 每次交易金额USD
- `--outcome/-o`: YES或NO（默认YES）
- `--interval/-i`: 循环间隔秒（默认0.5）

## 示例

```bash
# 90买入 93卖出 每次10美元
python fast_trader.py -t 12345 -b 0.90 -s 0.93 -a 10

# 更激进的策略
python fast_trader.py -t 12345 -b 0.88 -s 0.92 -a 20 -i 0.3
```

按 `Ctrl+C` 停止运行。
