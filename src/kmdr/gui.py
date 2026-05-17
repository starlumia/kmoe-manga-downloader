import base64
import hashlib
import hmac
import json
import ntpath
import os
import queue
import shlex
import subprocess
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable, Optional


def _default_python_executable() -> str:
    configured = os.environ.get("KMDR_CLI_PYTHON", "").strip()
    if configured:
        return configured

    bundled_cli = _bundled_cli_executable()
    if bundled_cli:
        return bundled_cli

    executable = sys.executable
    if os.name == "nt" and executable.lower().endswith("pythonw.exe"):
        python_exe = os.path.join(os.path.dirname(executable), "python.exe")
        if os.path.exists(python_exe):
            return python_exe
    return executable


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    if getattr(sys, "frozen", False):
        return env

    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pythonpath = env.get("PYTHONPATH")
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join([src_dir, pythonpath])
    else:
        env["PYTHONPATH"] = src_dir

    return env


def _subprocess_creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _bundled_cli_executable() -> Optional[str]:
    if not getattr(sys, "frozen", False):
        return None

    path_module = ntpath if os.name == "nt" else os.path
    executable_dir = path_module.dirname(sys.executable)
    executable_name = "kmdr-cli.exe" if os.name == "nt" else "kmdr-cli"
    candidate = path_module.join(executable_dir, executable_name)
    if os.path.exists(candidate):
        return candidate

    return None


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def _get_preferred_font_family(available_families: set[str]) -> Optional[str]:
    configured = os.environ.get("KMDR_GUI_FONT_FAMILY", "").strip()
    candidates = [
        configured,
        "Noto Sans SC",
        "Microsoft YaHei",
        "WenQuanYi Micro Hei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Droid Sans Fallback",
        "SimHei",
    ]

    for candidate in candidates:
        if candidate and candidate in available_families:
            return candidate

    return None


def _mask_sensitive_args(args: list[str]) -> list[str]:
    masked = list(args)
    in_login_command = "login" in masked

    for idx, value in enumerate(masked[:-1]):
        if value == "--password" or (in_login_command and value == "-p"):
            masked[idx + 1] = "******"

    return masked


def _parse_toolcall_line(line: str) -> Optional[dict]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict) and payload.get("type") in {"progress", "result"}:
        return payload
    return None


def _format_volume_selection(indexes: Iterable[int]) -> str:
    sorted_indexes = sorted(set(indexes))
    if not sorted_indexes:
        return "all"

    ranges = []
    start = prev = sorted_indexes[0]

    for index in sorted_indexes[1:]:
        if index == prev + 1:
            prev = index
            continue

        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = index

    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def _gui_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".kmdr-gui")


def _load_gui_config() -> dict[str, object]:
    try:
        with open(_gui_config_path(), encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return config if isinstance(config, dict) else {}


def _save_gui_config(config: dict[str, object]) -> None:
    with open(_gui_config_path(), "w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)


def _gui_secret_key_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".kmdr-gui.key")


def _load_or_create_gui_secret_key() -> bytes:
    key_path = _gui_secret_key_path()
    try:
        with open(key_path, "rb") as file:
            encoded_key = file.read().strip()
        key = base64.urlsafe_b64decode(encoded_key)
        if len(key) >= 32:
            return key
    except (OSError, ValueError):
        pass

    key = os.urandom(32)
    with open(key_path, "wb") as file:
        file.write(base64.urlsafe_b64encode(key))

    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    return key


def _local_secret_key() -> bytes:
    return hashlib.sha256(_load_or_create_gui_secret_key() + b"kmdr-gui-local-secret-v1").digest()


def _xor_bytes(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1

    return bytes(value ^ mask for value, mask in zip(data, output))


def _protect_local_bytes(data: bytes) -> bytes:
    key = _local_secret_key()
    nonce = os.urandom(16)
    ciphertext = _xor_bytes(data, key, nonce)
    tag = hmac.new(key, b"kmdr-gui-auth-v1" + nonce + ciphertext, hashlib.sha256).digest()
    return nonce + tag + ciphertext


def _unprotect_local_bytes(data: bytes) -> bytes:
    if len(data) < 48:
        raise ValueError("本机密文数据不完整。")

    key = _local_secret_key()
    nonce = data[:16]
    tag = data[16:48]
    ciphertext = data[48:]
    expected_tag = hmac.new(key, b"kmdr-gui-auth-v1" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("本机密文校验失败。")

    return _xor_bytes(ciphertext, key, nonce)


def _protect_windows_bytes(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    in_blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DataBlob()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 加密失败。")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _unprotect_windows_bytes(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    in_blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 解密失败。")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _login_secret_scheme() -> str:
    return "win-dpapi-v1" if os.name == "nt" else "local-key-v1"


def _encrypt_login_secret(username: str, password: str) -> dict[str, str]:
    payload = json.dumps({"username": username, "password": password}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    scheme = _login_secret_scheme()
    if scheme == "win-dpapi-v1":
        protected = _protect_windows_bytes(payload)
    else:
        protected = _protect_local_bytes(payload)

    return {"scheme": scheme, "payload": base64.b64encode(protected).decode("ascii")}


def _decrypt_login_secret(secret: object) -> Optional[tuple[str, str]]:
    if not isinstance(secret, dict):
        return None

    scheme = secret.get("scheme")
    payload = secret.get("payload")
    if not isinstance(scheme, str) or not isinstance(payload, str):
        return None

    try:
        protected = base64.b64decode(payload.encode("ascii"), validate=True)
        if scheme == "win-dpapi-v1" and os.name == "nt":
            decrypted = _unprotect_windows_bytes(protected)
        elif scheme == "local-key-v1":
            decrypted = _unprotect_local_bytes(protected)
        else:
            return None
        data = json.loads(decrypted.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    username = data.get("username")
    password = data.get("password")
    if isinstance(username, str) and isinstance(password, str):
        return username, password
    return None


def _saved_login_from_config(config: dict[str, object]) -> Optional[tuple[str, str]]:
    saved_login = _decrypt_login_secret(config.get("login_secret"))
    if saved_login is not None:
        return saved_login

    username = config.get("login_username")
    password = config.get("login_password")
    if isinstance(username, str) and isinstance(password, str):
        return username, password
    return None


def _has_legacy_plain_login(config: dict[str, object]) -> bool:
    return isinstance(config.get("login_username"), str) or isinstance(config.get("login_password"), str)


def _config_with_encrypted_login(config: dict[str, object], username: str, password: str) -> dict[str, object]:
    updated = dict(config)
    updated["remember_login"] = True
    updated["login_secret"] = _encrypt_login_secret(username, password)
    updated.pop("login_username", None)
    updated.pop("login_password", None)
    return updated


@dataclass(frozen=True)
class DownloadOptions:
    book_url: str
    dest: str = ""
    volume: str = "all"
    vol_type: str = "all"
    book_format: str = "epub"
    method: str = "1"
    proxy: str = ""
    retry: str = ""
    callback: str = ""
    num_workers: str = ""
    max_size: str = ""
    limit: str = ""
    per_cred_ratio: str = ""
    vip: bool = False
    disable_multi_part: bool = False
    try_multi_part: bool = False
    fake_ua: bool = False
    use_pool: bool = False
    explain: bool = False


class KmdrCommandBuilder:
    def __init__(self, python_executable: Optional[str] = None, module: str = "kmdr.main"):
        self._python_executable = python_executable or _default_python_executable()
        self._module = module

    def version(self) -> list[str]:
        return [*self._base(), "version"]

    def login(self, username: str, password: str) -> list[str]:
        return [*self._base(), "login", "-u", username, "-p", password]

    def status(self, proxy: str = "") -> list[str]:
        args = [*self._base(), "status"]
        self._append_optional(args, "-p", proxy)
        return args

    def search(self, keyword: str, page: str = "1", minimal: bool = False) -> list[str]:
        args = [*self._base(), "search", keyword]
        self._append_optional(args, "-p", page)
        if minimal:
            args.append("--minimal")
        return args

    def download(self, options: DownloadOptions) -> list[str]:
        args = [*self._base(), "download"]
        self._append_optional(args, "-d", options.dest)
        self._append_optional(args, "-l", options.book_url)
        self._append_optional(args, "-v", options.volume)
        self._append_optional(args, "-t", options.vol_type)
        self._append_optional(args, "-f", options.book_format)
        self._append_optional(args, "-m", options.method)
        self._append_optional(args, "-p", options.proxy)
        self._append_optional(args, "-r", options.retry)
        self._append_optional(args, "-c", options.callback)
        self._append_optional(args, "--num-workers", options.num_workers)
        self._append_optional(args, "--max-size", options.max_size)
        self._append_optional(args, "--limit", options.limit)
        self._append_optional(args, "--per-cred-ratio", options.per_cred_ratio)

        self._append_flag(args, "--vip", options.vip)
        self._append_flag(args, "--disable-multi-part", options.disable_multi_part)
        self._append_flag(args, "--try-multi-part", options.try_multi_part)
        self._append_flag(args, "--fake-ua", options.fake_ua)
        self._append_flag(args, "--use-pool", options.use_pool)
        self._append_flag(args, "--explain", options.explain)
        return args

    def config_list(self) -> list[str]:
        return [*self._base(), "config", "--list-option"]

    def config_set_base_url(self, base_url: str) -> list[str]:
        return [*self._base(), "config", "--base-url", base_url]

    def config_set(self, assignments: Iterable[str]) -> list[str]:
        return [*self._base(), "config", "--set", *list(assignments)]

    def _base(self) -> list[str]:
        executable_name = ntpath.basename(self._python_executable) if "\\" in self._python_executable else os.path.basename(self._python_executable)
        if executable_name.lower() in {"kmdr-cli.exe", "kmdr-cli"}:
            return [self._python_executable, "--mode", "toolcall"]
        return [self._python_executable, "-m", self._module, "--mode", "toolcall"]

    @staticmethod
    def _append_optional(args: list[str], flag: str, value: Optional[str]) -> None:
        if value is None:
            return

        normalized = str(value).strip()
        if normalized:
            args.extend([flag, normalized])

    @staticmethod
    def _append_flag(args: list[str], flag: str, enabled: bool) -> None:
        if enabled:
            args.append(flag)


class KmdrDesktopApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._filedialog = filedialog
        self._messagebox = messagebox

        self._root = root
        self._builder = KmdrCommandBuilder()
        self._events: queue.Queue = queue.Queue()
        self._process: Optional[subprocess.Popen] = None
        self._worker: Optional[threading.Thread] = None
        self._result_handler: Optional[Callable[[dict], None]] = None
        self._search_results: list[dict] = []
        self._parsed_volumes: list[dict] = []
        self._gui_config = _load_gui_config()
        self._active_scroll_canvas = None
        self._scroll_canvases = []
        self._wheel_priority_widgets = []
        self._style = None

        self._configure_root()
        self._build_ui()
        self._root.after(100, self._poll_events)

    def _configure_root(self) -> None:
        self._root.title("Kmoe Manga Downloader")
        self._root.geometry("1180x820")
        self._root.minsize(900, 620)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

        style = self._ttk.Style()
        try:
            style.theme_use("clam")
        except self._tk.TclError:
            pass

        self._style = style
        self._configure_fonts(style)

    def _configure_fonts(self, style) -> None:
        from tkinter import font

        font_size = _get_env_int("KMDR_GUI_FONT_SIZE", 16)
        scaling = _get_env_float("KMDR_GUI_SCALE", 1.7)
        preferred_family = _get_preferred_font_family(set(font.families(self._root)))
        font_config = {"size": font_size}
        if preferred_family:
            font_config["family"] = preferred_family

        try:
            self._root.tk.call("tk", "scaling", scaling)
        except self._tk.TclError:
            pass

        self._ui_font = font.nametofont("TkDefaultFont")
        self._ui_font.configure(**font_config)

        self._text_font = font.nametofont("TkTextFont")
        self._text_font.configure(**font_config)

        self._fixed_font = font.nametofont("TkFixedFont")
        self._fixed_font.configure(**font_config)

        self._heading_font = self._ui_font.copy()
        self._heading_font.configure(weight="bold")

        for font_name in ("TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                font.nametofont(font_name).configure(**font_config)
            except self._tk.TclError:
                pass

        self._root.option_add("*Font", self._ui_font)
        self._root.option_add("*Text.Font", self._fixed_font)
        self._root.option_add("*Entry.Font", self._ui_font)
        self._root.option_add("*Listbox.Font", self._ui_font)
        self._root.option_add("*TCombobox*Listbox.font", self._ui_font)

        rowheight = max(34, int(font_size * 2.6))
        style.configure(".", font=self._ui_font)
        style.configure("TLabel", font=self._ui_font)
        style.configure("TButton", font=self._ui_font, padding=(14, 9))
        style.configure("Primary.TButton", font=self._heading_font, padding=(22, 12))
        style.configure("TEntry", font=self._ui_font, padding=(8, 6))
        style.configure("TCombobox", font=self._ui_font, padding=(8, 6))
        style.configure("TCheckbutton", font=self._ui_font, padding=(6, 6))
        style.configure("TLabelframe.Label", font=self._heading_font)
        style.configure("TNotebook.Tab", font=self._ui_font, padding=(18, 11))
        style.configure("Treeview", font=self._ui_font, rowheight=rowheight)
        style.configure("Treeview.Heading", font=self._heading_font)

        self._font_size = font_size

    def _build_ui(self) -> None:
        ttk = self._ttk

        main = ttk.Frame(self._root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._notebook = ttk.Notebook(main)
        self._notebook.grid(row=0, column=0, sticky="nsew")

        self._build_download_tab()
        self._build_search_tab()
        self._build_account_tab()
        self._build_config_tab()

        self._root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self._root.bind_all("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self._root.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self._root.bind_all("<Button-5>", self._on_mousewheel, add="+")
        self._root.bind_all("<Shift-Button-4>", self._on_shift_mousewheel, add="+")
        self._root.bind_all("<Shift-Button-5>", self._on_shift_mousewheel, add="+")

        controls = ttk.Frame(main)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        controls.columnconfigure(0, weight=1)

        self._status_var = self._tk.StringVar(value="就绪")
        ttk.Label(controls, textvariable=self._status_var).grid(row=0, column=0, sticky="w")

        ttk.Label(controls, text="界面字号").grid(row=0, column=1, sticky="e", padx=(8, 6))
        self._font_size_var = self._tk.StringVar(value=str(self._font_size))
        font_size_box = ttk.Combobox(
            controls,
            textvariable=self._font_size_var,
            values=("10", "12", "14", "16", "18", "20", "22"),
            width=6,
            state="readonly",
        )
        font_size_box.grid(row=0, column=2, sticky="e", padx=(0, 8))
        font_size_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_font_size())

        self._global_download_button = ttk.Button(
            controls,
            text="DOWNLOAD / 开始下载",
            command=self._start_download,
            style="Primary.TButton",
        )
        self._global_download_button.grid(row=0, column=3, sticky="e", padx=(0, 8))

        self._stop_button = ttk.Button(controls, text="停止当前任务", command=self._stop_current_process, state="disabled")
        self._stop_button.grid(row=0, column=4, sticky="e")

        log_frame = ttk.LabelFrame(main, text="运行日志", padding=8)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self._log_text = self._tk.Text(log_frame, height=8, wrap="word", state="disabled", font=self._fixed_font)
        self._log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=log_scroll.set)
        self._migrate_legacy_login_secret()

    def _build_download_tab(self) -> None:
        ttk = self._ttk
        frame = self._add_scrollable_tab("下载")

        for idx in range(4):
            frame.columnconfigure(idx, weight=1)
        frame.rowconfigure(16, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="下载任务", font=self._heading_font).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="DOWNLOAD / 开始下载", command=self._start_download, style="Primary.TButton").grid(row=0, column=1, sticky="e")

        self._download_book_url = self._tk.StringVar()
        self._download_dest = self._tk.StringVar(value=os.getcwd())
        self._download_volume = self._tk.StringVar(value="all")
        self._download_vol_type = self._tk.StringVar(value="all")
        self._download_format = self._tk.StringVar(value="epub")
        self._download_method = self._tk.StringVar(value="1")
        self._download_workers = self._tk.StringVar(value="8")
        self._download_retry = self._tk.StringVar(value="3")
        self._download_proxy = self._tk.StringVar()
        self._download_max_size = self._tk.StringVar()
        self._download_limit = self._tk.StringVar()
        self._download_callback = self._tk.StringVar()
        self._download_per_cred_ratio = self._tk.StringVar()
        self._download_use_pool = self._tk.BooleanVar(value=False)
        self._download_vip = self._tk.BooleanVar(value=False)
        self._download_try_multi_part = self._tk.BooleanVar(value=False)
        self._download_disable_multi_part = self._tk.BooleanVar(value=False)
        self._download_fake_ua = self._tk.BooleanVar(value=False)

        self._add_labeled_entry(frame, "漫画详情 URL", self._download_book_url, 1, 0, columnspan=4)
        self._add_labeled_entry(frame, "保存目录", self._download_dest, 3, 0, columnspan=3)
        ttk.Button(frame, text="选择目录", command=self._choose_download_dest).grid(row=4, column=3, sticky="ew", padx=(8, 0), pady=4)

        self._add_labeled_entry(frame, "卷选择", self._download_volume, 5, 0)
        self._add_labeled_combobox(frame, "卷类型", self._download_vol_type, 5, 1, values=("vol", "extra", "seri", "all"))
        self._add_labeled_combobox(frame, "格式", self._download_format, 5, 2, values=("epub", "mobi"))
        self._add_labeled_combobox(frame, "下载方式", self._download_method, 5, 3, values=("1", "2"))

        self._add_labeled_entry(frame, "并发数", self._download_workers, 7, 0)
        self._add_labeled_entry(frame, "重试次数", self._download_retry, 7, 1)
        self._add_labeled_entry(frame, "最大体积 MB", self._download_max_size, 7, 2)
        self._add_labeled_entry(frame, "数量限制", self._download_limit, 7, 3)

        self._add_labeled_entry(frame, "代理", self._download_proxy, 9, 0, columnspan=2)
        self._add_labeled_entry(frame, "每账号并发比例", self._download_per_cred_ratio, 9, 2)
        self._add_labeled_entry(frame, "完成回调", self._download_callback, 11, 0, columnspan=4)

        flags = ttk.Frame(frame)
        flags.grid(row=13, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        for idx in range(5):
            flags.columnconfigure(idx, weight=1)

        ttk.Checkbutton(flags, text="启用凭证池", variable=self._download_use_pool).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(flags, text="使用 VIP 链接", variable=self._download_vip).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(flags, text="尝试分片", variable=self._download_try_multi_part).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(flags, text="禁用分片", variable=self._download_disable_multi_part).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(flags, text="随机 UA", variable=self._download_fake_ua).grid(row=0, column=4, sticky="w")

        actions = ttk.Frame(frame)
        actions.grid(row=14, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        actions.columnconfigure(2, weight=1)
        ttk.Button(actions, text="预估下载计划", command=self._explain_download).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="DOWNLOAD / 开始下载", command=self._start_download, style="Primary.TButton").grid(row=0, column=1)

        progress_frame = ttk.Frame(frame)
        progress_frame.grid(row=15, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        progress_frame.columnconfigure(0, weight=1)
        self._download_progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self._download_progress.grid(row=0, column=0, sticky="ew")

        volume_frame = ttk.LabelFrame(frame, text="已解析卷列表", padding=8)
        volume_frame.grid(row=16, column=0, columnspan=4, sticky="nsew", pady=(14, 0))
        volume_frame.columnconfigure(0, weight=1)
        volume_frame.rowconfigure(1, weight=1)

        volume_actions = ttk.Frame(volume_frame)
        volume_actions.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        volume_actions.columnconfigure(4, weight=1)
        ttk.Button(volume_actions, text="解析卷列表", command=self._parse_download_volumes).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(volume_actions, text="应用选中卷", command=self._apply_selected_volumes).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(volume_actions, text="全选", command=self._select_all_parsed_volumes).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(volume_actions, text="清空选择", command=self._clear_volume_selection).grid(row=0, column=3, padx=(0, 8))

        self._volume_tree = ttk.Treeview(
            volume_frame,
            columns=("index", "type", "name", "pages", "size", "extra"),
            show="headings",
            selectmode="extended",
            height=12,
        )
        self._volume_tree.heading("index", text="卷号")
        self._volume_tree.heading("type", text="类型")
        self._volume_tree.heading("name", text="卷名")
        self._volume_tree.heading("pages", text="页数")
        self._volume_tree.heading("size", text="大小 MB")
        self._volume_tree.heading("extra", text="状态")
        self._volume_tree.column("index", width=80, anchor="center")
        self._volume_tree.column("type", width=110, anchor="center")
        self._volume_tree.column("name", width=360, anchor="w")
        self._volume_tree.column("pages", width=80, anchor="e")
        self._volume_tree.column("size", width=90, anchor="e")
        self._volume_tree.column("extra", width=150, anchor="w")
        self._volume_tree.grid(row=1, column=0, sticky="nsew")

        volume_scroll = ttk.Scrollbar(volume_frame, orient="vertical", command=self._volume_tree.yview)
        volume_scroll.grid(row=1, column=1, sticky="ns")
        self._volume_tree.configure(yscrollcommand=volume_scroll.set)
        self._wheel_priority_widgets.append(self._volume_tree)

    def _add_scrollable_tab(self, title: str):
        ttk = self._ttk
        outer = ttk.Frame(self._notebook)
        self._notebook.add(outer, text=title)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = self._tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        vertical_scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        horizontal_scroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")

        frame = ttk.Frame(canvas, padding=12)
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            width = max(canvas.winfo_width(), frame.winfo_reqwidth())
            canvas.itemconfigure(window_id, width=width)

        frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_scroll_region)
        canvas.bind("<Enter>", lambda _event: self._activate_scroll_canvas(canvas))
        frame.bind("<Enter>", lambda _event: self._activate_scroll_canvas(canvas))
        canvas.bind("<Leave>", lambda _event: self._deactivate_scroll_canvas_when_outside(canvas))
        frame.bind("<Leave>", lambda _event: self._deactivate_scroll_canvas_when_outside(canvas))

        self._scroll_canvases.append((canvas, frame, window_id))
        return frame

    def _activate_scroll_canvas(self, canvas) -> None:
        self._active_scroll_canvas = canvas

    def _deactivate_scroll_canvas_when_outside(self, canvas) -> None:
        if self._active_scroll_canvas is canvas:
            self._active_scroll_canvas = None

    def _find_scroll_canvas(self, widget):
        for canvas, frame, _window_id in self._scroll_canvases:
            if widget is canvas or self._is_descendant(widget, frame):
                return canvas
        return self._active_scroll_canvas

    def _find_priority_scroll_widget(self, widget):
        for priority_widget in self._wheel_priority_widgets:
            if widget is priority_widget or self._is_descendant(widget, priority_widget):
                return priority_widget
        return None

    def _is_descendant(self, widget, parent) -> bool:
        while widget is not None:
            if widget is parent:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _wheel_units(self, event) -> int:
        if getattr(event, "num", None) == 4:
            return -3
        if getattr(event, "num", None) == 5:
            return 3

        delta = getattr(event, "delta", 0)
        if delta == 0:
            return 0

        steps = abs(delta) // 120
        if steps < 1:
            steps = 1
        return -steps if delta > 0 else steps

    def _on_mousewheel(self, event):
        units = self._wheel_units(event)
        if units == 0:
            return None

        priority_widget = self._find_priority_scroll_widget(event.widget)
        if priority_widget is not None:
            priority_widget.yview_scroll(units, "units")
            return "break"

        canvas = self._find_scroll_canvas(event.widget)
        if canvas is not None:
            canvas.yview_scroll(units, "units")
            return "break"

        return None

    def _on_shift_mousewheel(self, event):
        units = self._wheel_units(event)
        if units == 0:
            return None

        canvas = self._find_scroll_canvas(event.widget)
        if canvas is not None:
            canvas.xview_scroll(units, "units")
            return "break"

        return None

    def _build_search_tab(self) -> None:
        ttk = self._ttk
        frame = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(frame, text="搜索")

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        self._search_keyword = self._tk.StringVar()
        self._search_page = self._tk.StringVar(value="1")

        form = ttk.Frame(frame)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="关键词").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(form, textvariable=self._search_keyword).grid(row=0, column=1, sticky="ew")
        ttk.Label(form, text="页码").grid(row=0, column=2, sticky="w", padx=(10, 8))
        ttk.Entry(form, textvariable=self._search_page, width=8).grid(row=0, column=3, sticky="w")
        ttk.Button(form, text="搜索", command=self._start_search).grid(row=0, column=4, padx=(10, 0))

        self._search_tree = ttk.Treeview(frame, columns=("name", "author", "status", "url"), show="headings", height=10)
        self._search_tree.heading("name", text="书名")
        self._search_tree.heading("author", text="作者")
        self._search_tree.heading("status", text="状态")
        self._search_tree.heading("url", text="链接")
        self._search_tree.column("name", width=280, anchor="w")
        self._search_tree.column("author", width=160, anchor="w")
        self._search_tree.column("status", width=90, anchor="w")
        self._search_tree.column("url", width=360, anchor="w")
        self._search_tree.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self._search_tree.bind("<Double-1>", lambda _event: self._use_selected_search_result())

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="使用选中链接下载", command=self._use_selected_search_result).grid(row=0, column=0)

    def _build_account_tab(self) -> None:
        ttk = self._ttk
        frame = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(frame, text="账户")

        for idx in range(2):
            frame.columnconfigure(idx, weight=1)

        saved_login = _saved_login_from_config(self._gui_config) if self._gui_config.get("remember_login") else None
        remember_login = saved_login is not None
        saved_username = saved_login[0] if saved_login else ""
        saved_password = saved_login[1] if saved_login else ""

        self._login_username = self._tk.StringVar(value=saved_username)
        self._login_password = self._tk.StringVar(value=saved_password)
        self._remember_login = self._tk.BooleanVar(value=remember_login)
        self._status_proxy = self._tk.StringVar()

        self._add_labeled_entry(frame, "用户名", self._login_username, 0, 0)

        ttk.Label(frame, text="密码").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Entry(frame, textvariable=self._login_password, show="*").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)

        ttk.Checkbutton(
            frame,
            text="记住账号密码（加密保存）",
            variable=self._remember_login,
            command=self._on_remember_login_changed,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 4))

        self._add_labeled_entry(frame, "状态检查代理", self._status_proxy, 3, 0, columnspan=2)

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="登录并保存 Cookie", command=self._start_login).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="查看账户状态", command=self._start_status).grid(row=0, column=1)
        ttk.Button(actions, text="清除已保存账号", command=lambda: self._forget_saved_login(show_message=True, clear_fields=True)).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )

        self._account_text = self._tk.Text(frame, height=12, wrap="word", state="disabled", font=self._fixed_font)
        self._account_text.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        frame.rowconfigure(6, weight=1)

    def _build_config_tab(self) -> None:
        ttk = self._ttk
        frame = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(frame, text="配置")

        for idx in range(3):
            frame.columnconfigure(idx, weight=1)

        self._config_base_url = self._tk.StringVar()
        self._config_dest = self._tk.StringVar()
        self._config_proxy = self._tk.StringVar()
        self._config_workers = self._tk.StringVar()
        self._config_retry = self._tk.StringVar()
        self._config_format = self._tk.StringVar()

        self._add_labeled_entry(frame, "镜像站基础 URL", self._config_base_url, 0, 0, columnspan=2)
        ttk.Button(frame, text="保存镜像站", command=self._set_base_url).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)

        self._add_labeled_entry(frame, "默认保存目录", self._config_dest, 2, 0, columnspan=2)
        ttk.Button(frame, text="选择目录", command=self._choose_config_dest).grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=4)
        self._add_labeled_entry(frame, "默认代理", self._config_proxy, 4, 0)
        self._add_labeled_entry(frame, "默认并发数", self._config_workers, 4, 1)
        self._add_labeled_entry(frame, "默认重试次数", self._config_retry, 4, 2)
        self._add_labeled_combobox(frame, "默认格式", self._config_format, 6, 0, values=("", "epub", "mobi"))

        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="保存下载默认项", command=self._set_download_defaults).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="查看当前配置", command=self._list_config).grid(row=0, column=1)

        self._config_text = self._tk.Text(frame, height=12, wrap="word", state="disabled", font=self._fixed_font)
        self._config_text.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        frame.rowconfigure(9, weight=1)

    def _add_labeled_entry(self, parent, label: str, variable, row: int, column: int, columnspan: int = 1) -> None:
        ttk = self._ttk
        ttk.Label(parent, text=label).grid(row=row, column=column, columnspan=columnspan, sticky="w", padx=(0, 0 if column == 0 else 8))
        ttk.Entry(parent, textvariable=variable).grid(
            row=row + 1,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=(0, 0 if column == 0 else 8),
            pady=4,
        )

    def _add_labeled_combobox(self, parent, label: str, variable, row: int, column: int, values: tuple[str, ...]) -> None:
        ttk = self._ttk
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 8, 0))
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row + 1,
            column=column,
            sticky="ew",
            padx=(0 if column == 0 else 8, 0),
            pady=4,
        )

    def _apply_font_size(self) -> None:
        if self._style is None:
            return

        os.environ["KMDR_GUI_FONT_SIZE"] = self._font_size_var.get()
        self._configure_fonts(self._style)
        self._log_text.configure(font=self._fixed_font)
        self._account_text.configure(font=self._fixed_font)
        self._config_text.configure(font=self._fixed_font)

    def _on_remember_login_changed(self) -> None:
        if not self._remember_login.get():
            self._forget_saved_login()

    def _migrate_legacy_login_secret(self) -> None:
        if not self._gui_config.get("remember_login") or not _has_legacy_plain_login(self._gui_config):
            return

        username = self._login_username.get()
        password = self._login_password.get()
        if not username or not password:
            return

        try:
            config = _config_with_encrypted_login(self._gui_config, username, password)
            _save_gui_config(config)
        except (OSError, ValueError) as exc:
            self._append_log(f"[GUI] 迁移已保存账号到密文失败：{exc}")
            return

        self._gui_config = config
        self._append_log("[GUI] 已将旧版明文账号配置迁移为密文。")

    def _persist_login_preference(self, username: str, password: str) -> None:
        if not self._remember_login.get():
            self._forget_saved_login()
            return

        try:
            config = _config_with_encrypted_login(self._gui_config, username, password)
            _save_gui_config(config)
        except (OSError, ValueError) as exc:
            self._append_log(f"[GUI] 保存账号密码失败：{exc}")
            return

        self._gui_config = config
        self._append_log("[GUI] 已将账号密码加密保存到本机 GUI 配置。")

    def _forget_saved_login(self, show_message: bool = False, clear_fields: bool = False) -> None:
        config = dict(self._gui_config)
        config["remember_login"] = False
        config.pop("login_secret", None)
        config.pop("login_username", None)
        config.pop("login_password", None)

        try:
            _save_gui_config(config)
        except OSError as exc:
            self._append_log(f"[GUI] 清除已保存账号失败：{exc}")
            return

        self._gui_config = config
        self._remember_login.set(False)
        if clear_fields:
            self._login_username.set("")
            self._login_password.set("")
        if show_message:
            self._messagebox.showinfo("已清除", "已清除本机保存的账号密码。")

    def _choose_download_dest(self) -> None:
        selected = self._filedialog.askdirectory(initialdir=self._download_dest.get() or os.getcwd())
        if selected:
            self._download_dest.set(selected)

    def _choose_config_dest(self) -> None:
        selected = self._filedialog.askdirectory(initialdir=self._config_dest.get() or os.getcwd())
        if selected:
            self._config_dest.set(selected)

    def _start_login(self) -> None:
        username = self._login_username.get().strip()
        password = self._login_password.get()
        if not username or not password:
            self._messagebox.showwarning("缺少登录信息", "请填写用户名和密码。")
            return

        def handle_login_result(payload: dict) -> None:
            self._render_account_result(payload)
            if payload.get("code") == 0:
                self._persist_login_preference(username, password)

        self._start_command("登录", self._builder.login(username=username, password=password), handle_login_result)

    def _start_status(self) -> None:
        self._start_command("账户状态", self._builder.status(proxy=self._status_proxy.get()), self._render_account_result)

    def _start_search(self) -> None:
        keyword = self._search_keyword.get().strip()
        if not keyword:
            self._messagebox.showwarning("缺少关键词", "请填写搜索关键词。")
            return

        self._start_command(
            "搜索",
            self._builder.search(keyword=keyword, page=self._search_page.get()),
            self._render_search_result,
        )

    def _use_selected_search_result(self) -> None:
        selected = self._search_tree.selection()
        if not selected:
            self._messagebox.showinfo("未选择条目", "请先在搜索结果中选择一本漫画。")
            return

        item_id = selected[0]
        values = self._search_tree.item(item_id, "values")
        if len(values) < 4:
            return

        self._download_book_url.set(values[3])
        self._notebook.select(0)
        self._parse_download_volumes()

    def _start_download(self) -> None:
        options = self._collect_download_options(explain=False)
        if options is None:
            return

        self._download_progress["value"] = 0
        self._start_command("下载", self._builder.download(options), self._render_download_result)

    def _explain_download(self) -> None:
        options = self._collect_download_options(explain=True)
        if options is None:
            return

        self._download_progress["value"] = 0
        self._start_command("预估下载计划", self._builder.download(options), self._render_download_result)

    def _parse_download_volumes(self) -> None:
        book_url = self._download_book_url.get().strip()
        if not book_url:
            self._messagebox.showwarning("缺少漫画链接", "请先选择或填写漫画详情 URL。")
            return

        options = DownloadOptions(
            book_url=book_url,
            dest=self._download_dest.get(),
            volume="all",
            vol_type="all",
            book_format=self._download_format.get(),
            method=self._download_method.get(),
            proxy=self._download_proxy.get(),
            retry=self._download_retry.get(),
            callback="",
            num_workers=self._download_workers.get(),
            max_size="",
            limit="",
            per_cred_ratio=self._download_per_cred_ratio.get(),
            vip=self._download_vip.get(),
            disable_multi_part=self._download_disable_multi_part.get(),
            try_multi_part=self._download_try_multi_part.get(),
            fake_ua=self._download_fake_ua.get(),
            use_pool=self._download_use_pool.get(),
            explain=True,
        )

        self._download_progress["value"] = 0
        self._clear_parsed_volumes()
        self._start_command("解析卷列表", self._builder.download(options), self._render_volume_parse_result)

    def _collect_download_options(self, explain: bool) -> Optional[DownloadOptions]:
        if self._selected_parsed_volumes() and not self._apply_selected_volumes(show_message=False):
            return None

        book_url = self._download_book_url.get().strip()
        volume = self._download_volume.get().strip()
        if not book_url:
            self._messagebox.showwarning("缺少漫画链接", "请填写漫画详情 URL。")
            return None
        if not volume:
            self._messagebox.showwarning("缺少卷选择", "请填写卷选择，例如 all、1、1-3。")
            return None

        return DownloadOptions(
            book_url=book_url,
            dest=self._download_dest.get(),
            volume=volume,
            vol_type=self._download_vol_type.get(),
            book_format=self._download_format.get(),
            method=self._download_method.get(),
            proxy=self._download_proxy.get(),
            retry=self._download_retry.get(),
            callback=self._download_callback.get(),
            num_workers=self._download_workers.get(),
            max_size=self._download_max_size.get(),
            limit=self._download_limit.get(),
            per_cred_ratio=self._download_per_cred_ratio.get(),
            vip=self._download_vip.get(),
            disable_multi_part=self._download_disable_multi_part.get(),
            try_multi_part=self._download_try_multi_part.get(),
            fake_ua=self._download_fake_ua.get(),
            use_pool=self._download_use_pool.get(),
            explain=explain,
        )

    def _clear_parsed_volumes(self) -> None:
        self._parsed_volumes = []
        self._volume_tree.delete(*self._volume_tree.get_children())

    def _render_parsed_volumes(self, volumes: list[dict]) -> None:
        self._volume_tree.delete(*self._volume_tree.get_children())

        for idx, volume in enumerate(volumes):
            size = volume.get("size")
            if isinstance(size, (int, float)):
                size_text = f"{size:.2f}"
            else:
                size_text = ""

            self._volume_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    volume.get("index", ""),
                    volume.get("type_label") or volume.get("type", ""),
                    volume.get("name", ""),
                    volume.get("pages", ""),
                    size_text,
                    volume.get("extra_info", ""),
                ),
            )

    def _selected_parsed_volumes(self) -> list[dict]:
        selected = []
        for item_id in self._volume_tree.selection():
            try:
                selected.append(self._parsed_volumes[int(item_id)])
            except (ValueError, IndexError):
                continue
        return selected

    def _apply_selected_volumes(self, show_message: bool = True) -> bool:
        selected = self._selected_parsed_volumes()
        if not selected:
            if show_message:
                self._messagebox.showinfo("未选择卷", "请先在已解析卷列表中选择一个或多个卷。")
            return True

        selected_keys = {(volume.get("type"), volume.get("index")) for volume in selected}
        selected_indexes = {volume.get("index") for volume in selected if isinstance(volume.get("index"), int)}
        selected_types = {volume.get("type") for volume in selected if volume.get("type")}

        if not selected_indexes or not selected_types:
            if show_message:
                self._messagebox.showwarning("卷信息异常", "选中的卷缺少卷号或类型，无法生成下载参数。")
            return False

        if len(selected_types) == 1:
            vol_type = next(iter(selected_types))
        else:
            implied_keys = {(volume.get("type"), volume.get("index")) for volume in self._parsed_volumes if volume.get("index") in selected_indexes}
            if implied_keys != selected_keys:
                if show_message:
                    self._messagebox.showwarning("暂不支持混合选择", "混合选择会包含未选中的同卷号条目。请一次选择同一卷类型。")
                return False
            vol_type = "all"

        self._download_vol_type.set(vol_type)
        self._download_volume.set(_format_volume_selection(index for index in selected_indexes if isinstance(index, int)))

        if show_message:
            self._status_var.set(f"已应用 {len(selected)} 个卷到下载参数")
        return True

    def _select_all_parsed_volumes(self) -> None:
        children = self._volume_tree.get_children()
        if children:
            self._volume_tree.selection_set(children)

    def _clear_volume_selection(self) -> None:
        self._volume_tree.selection_remove(self._volume_tree.selection())

    def _set_base_url(self) -> None:
        base_url = self._config_base_url.get().strip()
        if not base_url:
            self._messagebox.showwarning("缺少镜像站", "请填写镜像站基础 URL。")
            return
        self._start_command("保存镜像站", self._builder.config_set_base_url(base_url), self._render_config_result)

    def _set_download_defaults(self) -> None:
        assignments = []
        for key, variable in (
            ("dest", self._config_dest),
            ("proxy", self._config_proxy),
            ("num_workers", self._config_workers),
            ("retry", self._config_retry),
            ("format", self._config_format),
        ):
            value = variable.get().strip()
            if value:
                assignments.append(f"{key}={value}")

        if not assignments:
            self._messagebox.showwarning("缺少配置项", "请至少填写一个下载默认项。")
            return

        self._start_command("保存下载默认项", self._builder.config_set(assignments), self._render_config_result)

    def _list_config(self) -> None:
        self._start_command("查看当前配置", self._builder.config_list(), self._render_config_result)

    def _start_command(self, label: str, args: list[str], result_handler: Callable[[dict], None]) -> None:
        if self._process is not None:
            self._messagebox.showinfo("任务运行中", "请等待当前任务结束，或先停止当前任务。")
            return

        self._result_handler = result_handler
        self._status_var.set(f"{label}运行中...")
        self._stop_button.configure(state="normal")
        self._append_log("> " + " ".join(shlex.quote(item) for item in _mask_sensitive_args(args)))

        self._worker = threading.Thread(target=self._run_process, args=(label, args), daemon=True)
        self._worker.start()

    def _run_process(self, label: str, args: list[str]) -> None:
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=_subprocess_env(),
                creationflags=_subprocess_creation_flags(),
            )

            assert self._process.stdout is not None
            for line in self._process.stdout:
                self._events.put(("line", line.rstrip("\n")))

            returncode = self._process.wait()
            self._events.put(("done", label, returncode))
        except Exception as exc:
            self._events.put(("error", label, str(exc)))

    def _poll_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "line":
                self._handle_output_line(event[1])
            elif kind == "done":
                self._handle_process_done(event[1], event[2])
            elif kind == "error":
                self._append_log(f"[{event[1]}] {event[2]}")
                self._status_var.set(f"{event[1]}失败")
                self._process = None
                self._stop_button.configure(state="disabled")

        self._root.after(100, self._poll_events)

    def _handle_output_line(self, line: str) -> None:
        if line:
            self._append_log(line)

        payload = _parse_toolcall_line(line)
        if not payload:
            return

        if payload["type"] == "progress":
            self._handle_progress(payload)
        elif payload["type"] == "result" and self._result_handler:
            self._result_handler(payload)

    def _handle_progress(self, payload: dict) -> None:
        status = payload.get("status", "running")
        volume = payload.get("volume", "")
        percentage = payload.get("percentage")
        if isinstance(percentage, (int, float)):
            self._download_progress["value"] = percentage
            self._status_var.set(f"下载中 {volume} {percentage}%")
        else:
            self._status_var.set(f"下载状态: {status}")

    def _handle_process_done(self, label: str, returncode: int) -> None:
        self._process = None
        self._stop_button.configure(state="disabled")

        if returncode == 0:
            self._status_var.set(f"{label}完成")
        else:
            self._status_var.set(f"{label}退出，代码 {returncode}")
        self._append_log(f"[{label}] 进程结束，退出代码 {returncode}")

    def _render_account_result(self, payload: dict) -> None:
        self._render_json_to_text(self._account_text, payload)

    def _render_config_result(self, payload: dict) -> None:
        self._render_json_to_text(self._config_text, payload)

    def _render_search_result(self, payload: dict) -> None:
        self._search_tree.delete(*self._search_tree.get_children())
        data = payload.get("data") or {}
        books = data.get("books") or []
        self._search_results = books

        for idx, book in enumerate(books):
            self._search_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    book.get("name", ""),
                    book.get("author", ""),
                    book.get("status", ""),
                    book.get("url", ""),
                ),
            )

        self._status_var.set(f"搜索完成，共 {len(books)} 条结果")

    def _render_volume_parse_result(self, payload: dict) -> None:
        self._render_json_to_log(payload)

        if payload.get("code") != 0:
            self._status_var.set(f"解析卷列表失败: {payload.get('msg', '未知错误')}")
            return

        data = payload.get("data") or {}
        volumes = data.get("volumes") or data.get("to_download") or []
        self._parsed_volumes = volumes
        self._render_parsed_volumes(volumes)
        self._status_var.set(f"卷列表解析完成，共 {len(volumes)} 卷")

    def _render_download_result(self, payload: dict) -> None:
        self._render_json_to_log(payload)
        data = payload.get("data") or {}
        if "completed" in data and "total" in data:
            self._status_var.set(f"下载完成: {data.get('completed')}/{data.get('total')}，失败 {data.get('failed', 0)}")
            self._download_progress["value"] = 100
        elif "to_download" in data:
            self._status_var.set(f"预估完成: 待下载 {len(data.get('to_download') or [])} 卷")

    def _render_json_to_text(self, text_widget, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("end", content)
        text_widget.configure(state="disabled")

    def _render_json_to_log(self, payload: dict) -> None:
        self._append_log(json.dumps(payload, ensure_ascii=False, indent=2))

    def _append_log(self, text: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _stop_current_process(self) -> None:
        if self._process is None:
            return

        self._process.terminate()
        self._append_log("[GUI] 已请求停止当前任务。")

    def _on_close(self) -> None:
        if self._process is not None:
            should_close = self._messagebox.askyesno("任务运行中", "当前任务仍在运行，是否停止任务并退出？")
            if not should_close:
                return
            self._process.terminate()
        self._root.destroy()


def entry_point() -> None:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("无法启动图形界面：当前 Python 未安装 Tkinter。") from exc

    root = tk.Tk()
    KmdrDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    entry_point()
