#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台多账号启动器 (Windows/Mac/Linux 通用)
"""
import os
import subprocess
import sys
import time
from pathlib import Path

def run_multi():
    root_dir = Path(__file__).parent.parent
    accounts_dir = root_dir / "accounts"
    
    if not accounts_dir.exists():
        print(f"❌ 错误: 未找到目录 {accounts_dir}")
        print("请创建 accounts/acc1, accounts/acc2 等文件夹，并放入 .env 和 config.yaml")
        return

    print("🚀 正在启动多账号监控...")
    
    # 获取所有账户目录
    acc_dirs = [d for d in accounts_dir.iterdir() if d.is_dir()]
    
    processes = []
    
    for acc_dir in acc_dirs:
        env_file = acc_dir / ".env"
        config_file = acc_dir / "config.yaml"
        
        if not env_file.exists() or not config_file.exists():
            print(f"⚠️  跳过 {acc_dir.name}: 缺少 .env 或 config.yaml")
            continue
            
        print(f"✅ 启动账号: {acc_dir.name}")
        
        # 构造执行命令
        # Windows 不需要 caffeinate
        cmd = [
            sys.executable, 
            "solomarket.py", 
            "--env", str(env_file), 
            "--config", str(config_file)
        ]
        
        # 在不同系统上防休眠的策略不同
        # 如果是 Mac，我们可以尝试带上 caffeinate (如果系统有的话)
        if sys.platform == "darwin":
            try:
                subprocess.run(["which", "caffeinate"], capture_output=True, check=True)
                cmd = ["caffeinate", "-i"] + cmd
            except:
                pass

        # 启动子进程
        # 使用 Popen 让它在后台运行
        process = subprocess.Popen(
            cmd,
            cwd=str(root_dir),
            # Windows 下使用 creationflags 使其不弹出一堆黑窗口（可选）
            # creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        )
        processes.append((acc_dir.name, process.pid))
        time.sleep(1) # 略微错开启动时间

    print("\n" + "━" * 40)
    print("🎉 所有账号尝试启动完毕！")
    for name, pid in processes:
        print(f"   - {name} (PID: {pid})")
    print("━" * 40)
    print("查看日志: 请前往 log/ 目录")
    if sys.platform == "win32":
        print("停止所有机器人: 请在任务管理器中结束 python 进程，或关闭此窗口")
    else:
        print("停止所有机器人: run 'pkill -f solomarket.py'")

if __name__ == "__main__":
    run_multi()
