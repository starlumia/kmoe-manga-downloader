# Linux / WSL 启动说明

当前 Linux / WSL 版本保留为源码启动方式，适合开发和本机使用。

## WSL 双击入口

从 Windows 资源管理器打开 WSL 路径后双击：

```text
Kmoe Manga Downloader.pyw
```

如果需要查看错误信息，双击：

```text
Kmoe Manga Downloader.bat
```

该入口会进入 WSL 项目目录，并使用当前配置的 conda 环境：

```text
/home/starlumia/anaconda3/envs/kmdr
```

## Linux 桌面入口

`kmoe-manga-downloader.desktop` 会激活同一个 conda 环境后启动 GUI。

## 字体说明

如果 WSLg 下中文显示异常，建议安装系统 Tk 和中文字体：

```bash
sudo apt update
sudo apt install -y python3-tk fonts-noto-cjk fonts-wqy-microhei fonts-wqy-zenhei fontconfig libxft2
fc-cache -fv
```
