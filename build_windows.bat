@echo off
chcp 65001 >nul
REM ===== Windows 打包脚本：生成 dist\SwitchMigrator.exe =====
REM 需要先安装 Python 3.9+ (https://www.python.org ，安装时勾选 Add to PATH)
cd /d %~dp0
python -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m PyInstaller --noconfirm --onefile --windowed --name SwitchMigrator main.py
echo.
echo 打包完成: dist\SwitchMigrator.exe
pause
