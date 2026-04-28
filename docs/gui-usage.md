# 图形界面使用指南

这份指南说明当前 fork 新增的图形界面、Windows 免安装包和 Linux / WSL 启动方式。

## 功能范围

图形界面是对现有 `kmdr` 命令行能力的封装，不改变原有下载核心。当前支持：

- 登录并保存 Cookie
- 查看账户状态
- 搜索漫画并把选中结果填入下载页
- 输入漫画详情 URL 后下载
- 预估下载计划
- 设置常用下载默认项
- 调整界面字号
- 查看运行日志和下载进度

## Windows 免安装版本

Windows 版本会打包成一个目录，目标机器不需要预装 Python、conda 或项目依赖。

### 构建环境

在 Windows 上安装 Python 3.11、3.12 或 3.14。推荐 Python 3.12。

安装 Python 时建议勾选：

```text
Add python.exe to PATH
```

或者安装 Python Launcher，让 `py -3.12`、`py -3.11`、`py -3.14` 可用。

### 打包

在仓库根目录运行：

```bat
scripts\build_windows.bat
```

脚本会自动：

1. 选择可用的 Python 3.12、3.11 或 3.14。
2. 安装当前项目和 PyInstaller。
3. 生成 Windows GUI 与后台 CLI。
4. 把可运行目录复制到桌面。

构建产物位于：

```text
dist\Kmoe Manga Downloader\
```

如果仓库在 WSL 路径下，脚本还会复制一份到：

```text
%USERPROFILE%\Desktop\Kmoe Manga Downloader\
```

### 运行

双击：

```text
Kmoe Manga Downloader.exe
```

不要只复制单个 exe。分发时需要保留整个目录：

```text
Kmoe Manga Downloader\
```

其中：

- `Kmoe Manga Downloader.exe` 是图形界面入口
- `kmdr-cli.exe` 是 GUI 后台调用的命令入口

如果从 `\\wsl.localhost\...` 直接运行出现 `failed to load python DLL`，请改用桌面上的本地副本。

### Windows 字号

Windows GUI 默认字号为 `12`，默认缩放为 `1.2`。

界面右下区域提供字号选择：

```text
10, 12, 14, 16, 18, 20, 22
```

如果在高分屏或远程桌面里显示过大，可以选择 `10` 或 `12`。

## Linux / WSL 版本

Linux / WSL 版本保留为源码启动方式，适合开发和本机使用。

### WSL 双击入口

从 Windows 资源管理器打开 WSL 路径后，可以双击：

```text
Kmoe Manga Downloader.pyw
```

如果需要查看错误输出，可以双击：

```text
Kmoe Manga Downloader.bat
```

该入口会使用当前配置的 conda 环境：

```text
/home/starlumia/anaconda3/envs/kmdr
```

### Linux 桌面入口

可以使用：

```text
kmoe-manga-downloader.desktop
```

该入口同样会激活 `kmdr` conda 环境后启动 GUI。

### Linux / WSL 字体

Linux / WSL 默认字号比 Windows 更大，便于在 WSLg 下阅读。

如果中文显示乱码，安装中文字体和 Tk 依赖：

```bash
sudo apt update
sudo apt install -y python3-tk fonts-noto-cjk fonts-wqy-microhei fonts-wqy-zenhei fontconfig libxft2
fc-cache -fv
```

## GUI 操作流程

### 1. 登录

打开 `账户` 页：

1. 输入用户名和密码。
2. 点击 `登录并保存 Cookie`。
3. 登录成功后可以点击 `查看账户状态` 检查配额。

Cookie 会写入当前用户的 `.kmdr` 配置目录，后续下载可复用。

### 2. 搜索

打开 `搜索` 页：

1. 输入关键词。
2. 点击 `搜索`。
3. 在结果表格中选中一本漫画。
4. 点击 `使用选中链接下载`，或双击结果行。

选中漫画的详情 URL 会自动填入 `下载` 页。

### 3. 下载

打开 `下载` 页：

1. 填写 `漫画详情 URL`。
2. 选择 `保存目录`。
3. 设置 `卷选择`，例如 `all`、`1`、`1-3`、`1,3,5`。
4. 根据需要选择卷类型、格式、下载方式、并发数和重试次数。
5. 点击 `DOWNLOAD / 开始下载`。

下载日志会显示在窗口底部，进度条会跟随下载状态更新。

### 4. 预估下载计划

在 `下载` 页填写参数后，点击：

```text
预估下载计划
```

程序会调用命令行的计划输出能力，只查看将要下载的内容，不直接下载文件。

### 5. 配置默认项

打开 `配置` 页，可以保存：

- 镜像站基础 URL
- 默认保存目录
- 默认代理
- 默认并发数
- 默认重试次数
- 默认格式

保存后，这些配置会被后续命令和 GUI 下载任务复用。

## 常见问题

### 打包后目标机器还需要 Python 吗

不需要。Windows 免安装目录已经包含 Python 运行时和项目依赖。

### 能不能只发一个 exe

当前版本不要只发单个 exe。请压缩并分发整个 `Kmoe Manga Downloader` 目录。

### 为什么 Windows 下有两个 exe

GUI 是无控制台窗口程序，后台 CLI 负责执行原有 `kmdr` 命令。拆成两个 exe 可以让 GUI 稳定读取后台输出和进度。

### 配置和 Cookie 保存在哪里

仍沿用原项目逻辑，写入当前用户主目录下的 `.kmdr`。

### 下载按钮在哪里

当前界面有三个下载入口：

- `下载` 页顶部
- `下载` 页参数区底部
- 主窗口底部全局栏

三个按钮都会执行同一个下载任务。
