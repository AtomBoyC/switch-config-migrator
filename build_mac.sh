#!/bin/bash
# ===== macOS 打包脚本：生成 dist/SwitchMigrator.app =====
# 需要 Python 3.9+（推荐 python.org 官方安装包，自带 tkinter）
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m PyInstaller --noconfirm --windowed --name SwitchMigrator main.py
echo
echo "打包完成: dist/SwitchMigrator.app"

