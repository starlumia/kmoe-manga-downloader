import os
import subprocess
import tkinter.messagebox as messagebox


CONDA_ENV = "kmdr"
CONDA_SH = "/home/starlumia/anaconda3/etc/profile.d/conda.sh"
CLI_PYTHON = "/home/starlumia/anaconda3/envs/kmdr/bin/python"


def parse_wsl_unc_path(path):
    normalized = os.path.normpath(path)
    prefix = "\\\\wsl.localhost\\"
    legacy_prefix = "\\\\wsl$\\"

    if normalized.startswith(prefix):
        rest = normalized[len(prefix) :]
    elif normalized.startswith(legacy_prefix):
        rest = normalized[len(legacy_prefix) :]
    else:
        return None, None

    parts = rest.split("\\", 1)
    if len(parts) != 2:
        return None, None

    distro, linux_path = parts
    return distro, "/" + linux_path.replace("\\", "/")


def launch_with_wsl():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    distro, project_dir = parse_wsl_unc_path(root_dir)

    if not distro or not project_dir:
        messagebox.showerror(
            "启动失败",
            "请从 Windows 资源管理器中的 \\\\wsl.localhost\\... 路径双击本文件，或使用 .bat 查看详细错误。",
        )
        return

    shell_command = (
        f"source {quote_bash(CONDA_SH)} && "
        f"conda activate {quote_bash(CONDA_ENV)} && "
        "export PYTHONPATH=\"$PWD/src\" && "
        f"export KMDR_CLI_PYTHON={quote_bash(CLI_PYTHON)} && "
        "export LANG=C.UTF-8 && "
        "export LC_ALL=C.UTF-8 && "
        "export KMDR_GUI_FONT_FAMILY='Noto Sans SC' && "
        "export KMDR_GUI_FONT_SIZE=16 && "
        "export KMDR_GUI_SCALE=1.7 && "
        "/usr/bin/python3 -m kmdr.gui"
    )

    completed = subprocess.run(
        ["wsl.exe", "-d", distro, "--cd", project_dir, "bash", "-lc", shell_command],
        shell=False,
    )

    if completed.returncode != 0:
        messagebox.showerror(
            "启动失败",
            f"无法在 WSL 发行版 {distro} 中启动 GUI。请确认已安装 python3-tk，并用 .bat 查看详细错误。",
        )


def quote_bash(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    launch_with_wsl()
