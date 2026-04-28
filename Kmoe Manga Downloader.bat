@echo off
setlocal enabledelayedexpansion

set "KMDR_CONDA_ENV=kmdr"
set "KMDR_CONDA_SH=/home/starlumia/anaconda3/etc/profile.d/conda.sh"
set "KMDR_CLI_PYTHON=/home/starlumia/anaconda3/envs/kmdr/bin/python"
set "WIN_ROOT=%~dp0"
set "WIN_ROOT=%WIN_ROOT:~0,-1%"

for /f "tokens=2,* delims=\" %%A in ("%WIN_ROOT%") do (
    set "WSL_DISTRO=%%A"
    set "WSL_REST=%%B"
)

if not defined WSL_DISTRO (
    echo 当前脚本应从 WSL 的 \\wsl.localhost\ 目录双击运行。
    echo 当前路径: %WIN_ROOT%
    pause
    exit /b 1
)

set "WSL_PROJECT=/!WSL_REST:\=/!"

wsl.exe -d "!WSL_DISTRO!" --cd "!WSL_PROJECT!" bash -lc "source '!KMDR_CONDA_SH!' && conda activate '!KMDR_CONDA_ENV!' && export PYTHONPATH=$PWD/src && export KMDR_CLI_PYTHON='!KMDR_CLI_PYTHON!' && export LANG=C.UTF-8 && export LC_ALL=C.UTF-8 && export KMDR_GUI_FONT_FAMILY='Noto Sans SC' && export KMDR_GUI_FONT_SIZE=16 && export KMDR_GUI_SCALE=1.7 && /usr/bin/python3 -m kmdr.gui"
if errorlevel 1 (
    echo.
    echo WSL 启动失败。请确认:
    echo 1. WSL 发行版 "!WSL_DISTRO!" 可用
    echo 2. 系统 Python 已安装 tkinter: sudo apt install python3-tk
    echo 3. conda 环境 "!KMDR_CONDA_ENV!" 存在且已安装项目依赖
    pause
    exit /b 1
)

endlocal
