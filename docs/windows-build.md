# Windows 打包说明

本项目的 Windows 原生版本使用 PyInstaller 打包为免安装目录：

- `Kmoe Manga Downloader.exe`：图形界面，无控制台窗口，内置 GUI 后端

## 构建环境

在 Windows 上安装 Python 3.11、3.12 或 3.14 后，在仓库根目录执行：

```bat
scripts\build_windows.bat
```

脚本会在 Windows 本地创建隔离环境，并把 PyInstaller 的临时构建目录放在本地磁盘，避免两个问题：

- 在 WSL 映射盘里清理 `build` 目录时出现 `PermissionError: [WinError 5] 拒绝访问`
- `pip install -e .` 触发动态版本号变化，导致 editable wheel 文件名校验失败

构建依赖环境会持久保存在：

```text
%LOCALAPPDATA%\KmoeMangaDownloader\build-env\pyXY\
```

其中 `pyXY` 对应当前使用的 Python 版本，例如 `py314`。首次构建、依赖锁文件变化，或显式重建环境时才会安装依赖；普通重复打包会复用这个环境，不再每次下载依赖。

强制重建依赖环境：

```bat
scripts\build_windows.bat --rebuild-env
```

脚本会自动按以下顺序选择解释器：

1. `py -3.14`
2. `py -3.12`
3. `py -3.11`
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
%USERPROFILE%\Desktop\Kmoe Manga Downloader yyyyMMdd-HHmmss\
```

把桌面上的整个带时间戳目录压缩分发即可。目标机器不需要预装 Python 或依赖。脚本不会覆盖或删除桌面上已有的 `Kmoe Manga Downloader` 目录，避免误删用户下载内容。

## 运行入口

双击桌面本地副本：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader yyyyMMdd-HHmmss\Kmoe Manga Downloader.exe
```

GUI 直接内置后端执行业务流程，不需要同目录存在 `kmdr-cli.exe`。

如果从 `\\wsl.localhost\...` 下运行出现 `failed to load python DLL`，请运行桌面的本地副本：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader yyyyMMdd-HHmmss\Kmoe Manga Downloader.exe
```

## 注意

- 目录版不要只复制单个 exe；需要保留整个输出目录。
- 用户配置仍写入当前用户主目录下的 `.kmdr`。
- GUI 默认下载目录是 `%USERPROFILE%\Downloads\Kmoe Manga Downloads`，不要把下载内容放进程序目录。
- 如果需要单文件 exe，可以使用 `KmoeMangaDownloader-windows-onefile.spec` 另行构建；单文件版启动速度会变慢，调试和依赖定位也更困难。
