# Kmoe Manga Downloader GUI

一个面向 Windows 用户的 Kmoe 漫画下载图形界面。

本项目基于原 `kmdr / Kmoe Manga Downloader` 终端应用开发。原项目提供了登录、搜索、卷信息解析、下载、断点重试、配置持久化等核心能力；这个 fork 在此基础上增加了更适合普通用户使用的 GUI、Windows 打包和 Release 单文件 exe。

感谢原 TUI/CLI 项目作者和维护者的工作。本项目不会替换原来的命令行能力，只是把常用流程封装成更容易点击操作的界面。

## 下载

推荐 Windows 用户直接下载 Release 中的单文件版本：

[Kmoe-Manga-Downloader.exe](https://github.com/starlumia/kmoe-manga-downloader/releases/download/v0.1.1/Kmoe-Manga-Downloader.exe)

也可以从 Release 页面选择其他文件：

[https://github.com/starlumia/kmoe-manga-downloader/releases/tag/v0.1.1](https://github.com/starlumia/kmoe-manga-downloader/releases/tag/v0.1.1)

Release 资产说明：

- `Kmoe-Manga-Downloader.exe`：单文件 GUI 版，推荐普通用户下载。
- `Kmoe-Manga-Downloader-Windows.zip`：目录版，适合需要保留完整打包目录的情况。
- `kmdr-cli.exe`：命令行版本，适合熟悉终端的用户。

单文件 exe 已包含 Python 运行时和项目依赖，目标机器不需要安装 Python、Poetry 或依赖包。首次启动可能略慢，因为程序会先解包到临时目录。

## GUI 操作流程

### 1. 打开程序

双击运行：

```text
Kmoe-Manga-Downloader.exe
```

主界面包含 `下载`、`搜索`、`账户`、`配置` 等页面。底部会显示运行状态和日志。

### 2. 登录账号

进入 `账户` 页面：

1. 输入 Kmoe 用户名和密码。
2. 可勾选 `记住账号密码（加密保存）`。
3. 点击 `登录并保存 Cookie`。
4. 登录成功后，可点击 `查看账户状态` 检查配额。

账号密码会以本机密文保存。Windows 下使用系统 DPAPI 保护，通常只能由当前 Windows 用户在本机解密。登录 Cookie 和下载配置仍沿用原 `kmdr` 的配置机制。

### 3. 搜索漫画

进入 `搜索` 页面：

1. 输入关键词。
2. 点击 `搜索`。
3. 在结果表格里选中漫画。
4. 双击结果行，或点击 `使用选中链接下载`。

程序会自动把漫画详情地址填入 `下载` 页面。

### 4. 解析卷列表

进入 `下载` 页面后：

1. 确认 `漫画详情 URL` 已填写。
2. 点击 `解析卷列表`。
3. 在 `已解析卷列表` 中勾选需要下载的卷。
4. 点击 `导入选中卷`。

导入后，`卷选择` 会自动变成类似 `1-3,5,8` 的格式，不需要再手动输入卷号。也可以点击 `全选`、`清空` 来快速调整选择。

### 5. 开始下载

在 `下载` 页面确认以下内容：

- `保存目录`
- `卷选择`
- `卷类型`
- `格式`
- `下载方式`
- `并发数`
- `重试次数`
- 是否使用代理、凭证池或其他高级选项

然后点击 `DOWNLOAD / 开始下载`。

下载进度和错误信息会显示在日志区域。窗口内容较多时，可以使用鼠标滚轮滚动页面；如果屏幕较小，也可以在底部调整 `界面字号`。

## 常用功能

- `预估下载计划`：只解析将要下载的内容，不真正下载。
- `配置` 页面：保存默认下载目录、代理、格式、并发数、重试次数和镜像站地址。
- `查看账户状态`：刷新当前账号信息和配额。
- `清除已保存账号`：删除 GUI 本地保存的账号密码。

## TUI / CLI 说明

原项目的终端能力仍然保留。如果你更熟悉命令行，可以继续使用：

```bash
kmdr login -u <username>
kmdr search <keyword>
kmdr download -l <book-url> -v 1-3
```

GUI 内部也是调用这些能力完成登录、搜索、解析和下载，所以两套入口共享同一套下载核心。

更多说明：

- [GUI 使用指南](docs/gui-usage.md)
- [Windows 打包说明](docs/windows-build.md)
- [Linux / WSL 启动说明](docs/linux-wsl.md)

## 开发

安装依赖：

```bash
poetry install --with dev
```

运行测试：

```bash
poetry run pytest
```

本地启动 GUI：

```bash
poetry run python -m kmdr.windows_gui
```

Windows 打包：

```bat
scripts\build_windows.bat
```

## 说明

本工具只负责调用用户账号可访问的 Kmoe 内容并保存到本地。请遵守目标站点规则和相关版权要求，仅用于个人学习、备份和测试。

## 致谢

感谢原 `kmdr / Kmoe Manga Downloader` TUI/CLI 项目提供稳定的核心下载能力。本 fork 的 GUI、Windows 打包、单文件 exe、卷列表选择和账号自动填充等功能都建立在原项目的基础之上。
