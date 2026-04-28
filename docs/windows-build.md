# Windows 打包说明

本项目的 Windows 原生版本使用 PyInstaller 打包为免安装目录：

- `Kmoe Manga Downloader.exe`：图形界面，无控制台窗口
- `kmdr-cli.exe`：后台命令入口，由 GUI 自动调用

## 构建环境

在 Windows 上安装 Python 3.11、3.12 或 3.14 后，在仓库根目录执行：

```bat
scripts\build_windows.bat
```

如果仓库位于 WSL 路径，例如 `\\wsl.localhost\Debian\...`，也可以直接运行该脚本。脚本使用 `pushd` 进入目录，Windows 会临时映射 UNC 路径，避免 CMD 回退到 `C:\Windows`。

脚本会在 Windows 本地 `%TEMP%` 下创建隔离的构建虚拟环境，并把 PyInstaller 的临时构建目录放在本地磁盘，避免两个问题：

- 在 WSL 映射盘里清理 `build` 目录时出现 `PermissionError: [WinError 5] 拒绝访问`
- `pip install -e .` 触发动态版本号变化，导致 editable wheel 文件名校验失败

脚本会自动按以下顺序选择解释器：

1. `py -3.12`
2. `py -3.11`
3. `py -3.14`
4. 当前 `python`，但必须是 3.11、3.12 或 3.14

如果提示 `No suitable Python runtime found`，说明 Windows 没安装对应版本。推荐安装 Python 3.12，并勾选 `Add python.exe to PATH` 或安装 Python Launcher。

Python 3.14 可用于构建，但如果遇到第三方依赖或 PyInstaller 兼容性问题，优先回退到 Python 3.12。

脚本不会把当前项目安装进全局 Python 环境，也不依赖当前 conda 环境。

临时构建产物位于：

```text
%TEMP%\kmoe-manga-downloader-build\dist\Kmoe Manga Downloader\
```

脚本会自动复制一份到 Windows 本地桌面：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader\
```

把桌面上的整个 `Kmoe Manga Downloader` 目录压缩分发即可。目标机器不需要预装 Python 或依赖。

## 运行入口

双击桌面本地副本：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader\Kmoe Manga Downloader.exe
```

GUI 会自动调用同目录下的 `kmdr-cli.exe` 执行登录、搜索、下载等命令。

如果从 `\\wsl.localhost\...` 下运行出现 `failed to load python DLL`，请运行桌面的本地副本：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader\Kmoe Manga Downloader.exe
```

## 注意

- 不要只复制单个 exe；需要保留整个输出目录。
- 用户配置仍写入当前用户主目录下的 `.kmdr`。
- 如果脚本提示无法替换桌面目录，请先关闭正在运行的 `Kmoe Manga Downloader.exe` 后重试。
- 如果需要单文件 exe，可以后续单独做 `--onefile` 版本，但启动速度会变慢，且调试和依赖定位更困难。
