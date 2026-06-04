import base64
import hashlib
import hmac
import json
import os
import queue
import threading
from collections.abc import Iterable
from types import ModuleType
from typing import Callable, Optional

from kmdr.gui_backend import GuiBackend, GuiBackendRunner
from kmdr.gui_backend import GuiDownloadOptions as DownloadOptions


# Tk is still used for OS dialogs, message boxes, variables, clipboard,
# native context menus, and plain Text output widgets. customtkinter does
# not provide platform-native replacements for the first group, and CTkTextbox
# changes enough Text behavior that logs and JSON panes intentionally stay on Tk Text.
def _load_customtkinter() -> Optional[ModuleType]:
    try:
        import customtkinter
    except ImportError:
        return None

    return customtkinter


def _configure_customtkinter(customtkinter: Optional[ModuleType]) -> None:
    if customtkinter is None:
        return

    try:
        customtkinter.set_appearance_mode(os.environ.get("KMDR_GUI_APPEARANCE", "System"))
    except ValueError:
        customtkinter.set_appearance_mode("System")

    try:
        customtkinter.set_default_color_theme(os.environ.get("KMDR_GUI_COLOR_THEME", "blue"))
    except ValueError:
        customtkinter.set_default_color_theme("blue")


def _appearance_colors(customtkinter: Optional[ModuleType]) -> dict[str, str]:
    mode = "Light"
    if customtkinter is not None:
        try:
            mode = customtkinter.get_appearance_mode()
        except AttributeError:
            mode = "Light"

    if mode == "Dark":
        return {
            "bg": "#242424",
            "panel": "#2b2b2b",
            "field": "#343638",
            "fg": "#dce4ee",
            "muted": "#a9b1bd",
            "border": "#3f444a",
            "accent": "#1f6aa5",
            "row": "#2b2b2b",
            "row_alt": "#303234",
        }

    return {
        "bg": "#f5f7fb",
        "panel": "#ffffff",
        "field": "#ffffff",
        "fg": "#1f2937",
        "muted": "#4b5563",
        "border": "#d0d7de",
        "accent": "#1f6aa5",
        "row": "#ffffff",
        "row_alt": "#f8fafc",
    }


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


def _default_download_dest() -> str:
    home = os.path.expanduser("~")
    if home and home != "~":
        return os.path.join(home, "Downloads", "Kmoe Manga Downloads")
    return os.getcwd()


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


class KmdrDesktopApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        self._tk = tk
        self._ctk = _load_customtkinter()
        if self._ctk is None:
            raise RuntimeError("无法启动图形界面：当前环境未安装 customtkinter。")
        _configure_customtkinter(self._ctk)
        self._filedialog = filedialog
        self._messagebox = messagebox

        self._root = root
        self._events: queue.Queue = queue.Queue()
        self._backend_runner: Optional[GuiBackendRunner] = None
        self._command_running = False
        self._worker: Optional[threading.Thread] = None
        self._result_handler: Optional[Callable[[dict], None]] = None
        self._search_results: list[dict] = []
        self._parsed_volumes: list[dict] = []
        self._gui_config = _load_gui_config()
        self._search_row_widgets: dict[str, object] = {}
        self._selected_search_items: set[str] = set()
        self._volume_row_widgets: dict[str, object] = {}
        self._selected_volume_items: set[str] = set()
        self._pages: dict[str, object] = {}
        self._nav_buttons: dict[str, object] = {}
        self._active_page = ""
        self._colors = _appearance_colors(self._ctk)

        self._configure_root()
        self._build_ui()
        self._root.after(100, self._poll_events)

    def _configure_root(self) -> None:
        self._root.title("Kmoe Manga Downloader")
        self._root.configure(fg_color=self._colors["bg"])
        self._root.geometry("1180x820")
        self._root.minsize(900, 620)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)

        self._configure_fonts()

    def _configure_fonts(self) -> None:
        from tkinter import font

        font_size = _get_env_int("KMDR_GUI_FONT_SIZE", 18)
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

        self._font_size = font_size

    def _font_tuple(self, bold: bool = False, fixed: bool = False) -> tuple:
        source = self._fixed_font if fixed else self._ui_font
        family = source.actual("family")
        size = source.actual("size")
        if bold:
            return family, size, "bold"
        return family, size

    def _frame(self, parent, **kwargs):
        kwargs.pop("padding", None)
        return self._ctk.CTkFrame(parent, fg_color=kwargs.pop("fg_color", "transparent"), **kwargs)

    def _label_frame(self, parent, text: str, padding: int = 0):
        frame = self._ctk.CTkFrame(parent)
        if text:
            label = self._ctk.CTkLabel(frame, text=text, font=self._font_tuple(bold=True), anchor="w")
            label.grid(row=0, column=0, sticky="ew", padx=padding, pady=(padding, 0))
            frame._kmdr_content_start_row = 1
        else:
            frame._kmdr_content_start_row = 0
        return frame

    def _content_row(self, parent, row: int) -> int:
        return row + int(getattr(parent, "_kmdr_content_start_row", 0))

    def _label(self, parent, text: Optional[str] = None, textvariable=None, font=None, **kwargs):
        options = dict(kwargs)
        options.setdefault("anchor", "w")
        if font is not None:
            options["font"] = font if isinstance(font, tuple) else self._font_tuple(bold=font is self._heading_font)
        if textvariable is not None:
            return self._ctk.CTkLabel(parent, textvariable=textvariable, **options)
        return self._ctk.CTkLabel(parent, text=text or "", **options)

    def _button(self, parent, text: str, command: Callable, style: Optional[str] = None, state: str = "normal", **kwargs):
        options = dict(kwargs)
        options.pop("style", None)
        height = 38 if style == "Primary.TButton" else 34
        font = self._font_tuple(bold=style == "Primary.TButton")
        return self._ctk.CTkButton(parent, text=text, command=command, state=state, height=height, font=font, **options)

    def _entry(self, parent, textvariable, show: Optional[str] = None, width: Optional[int] = None, **kwargs):
        options = dict(kwargs)
        if width is not None:
            options["width"] = max(80, width * 12)
        if show is not None:
            options["show"] = show
        return self._ctk.CTkEntry(parent, textvariable=textvariable, font=self._font_tuple(), height=34, **options)

    def _combobox(self, parent, textvariable, values: tuple[str, ...], state: str = "readonly", width: Optional[int] = None):
        ctk_state = "readonly" if state == "readonly" else state
        combo_width = 120 if width is None else max(90, width * 12)
        return self._ctk.CTkComboBox(
            parent,
            variable=textvariable,
            values=list(values),
            state=ctk_state,
            width=combo_width,
            height=34,
            font=self._font_tuple(),
        )

    def _checkbutton(self, parent, text: str, variable, command: Optional[Callable] = None):
        return self._ctk.CTkCheckBox(parent, text=text, variable=variable, command=command, font=self._font_tuple())

    def _scrollable_frame(self, parent, **kwargs):
        return self._ctk.CTkScrollableFrame(parent, fg_color=kwargs.pop("fg_color", "transparent"), **kwargs)

    def _progressbar(self, parent):
        progressbar = self._ctk.CTkProgressBar(parent)
        progressbar.set(0)
        return progressbar

    def _set_progress(self, value: float) -> None:
        self._download_progress.set(max(0.0, min(1.0, value / 100.0)))

    def _textbox(self, parent, height: int, state: str):
        # Tk Text is kept for stable disabled-state log rendering and clipboard-friendly JSON output.
        # CTkTextbox is a themed wrapper but changes scrolling/state behavior enough to keep this primitive for now.
        return self._tk.Text(
            parent,
            height=height,
            wrap="word",
            state=state,
            font=self._fixed_font,
            background=self._colors["field"],
            foreground=self._colors["fg"],
            insertbackground=self._colors["fg"],
            relief="flat",
            borderwidth=1,
        )

    def _build_ui(self) -> None:
        main = self._frame(self._root)
        main.grid(row=0, column=0, sticky="nsew")
        self._main = main
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=3)
        main.rowconfigure(2, weight=2)
        main.rowconfigure(3, weight=1)

        self._nav_frame = self._ctk.CTkFrame(main, fg_color=self._colors["panel"], width=148, corner_radius=0)
        self._nav_frame.grid(row=0, column=0, rowspan=4, sticky="nsw")
        self._nav_frame.grid_propagate(False)
        self._nav_frame.columnconfigure(0, weight=1)

        workspace = self._frame(main)
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)
        self._workspace = workspace

        self._page_container = self._frame(workspace)
        self._page_container.grid(row=0, column=0, sticky="nsew")
        self._page_container.columnconfigure(0, weight=1)
        self._page_container.rowconfigure(0, weight=1)

        self._build_nav_button("下载", 0)
        self._build_nav_button("搜索", 1)
        self._build_nav_button("账户", 2)
        self._build_nav_button("配置", 3)

        self._build_download_tab()
        self._build_search_tab()
        self._build_account_tab()
        self._build_config_tab()

        controls = self._frame(main)
        controls.grid(row=1, column=1, sticky="ew", pady=(8, 8))
        controls.columnconfigure(0, weight=1)

        self._status_var = self._tk.StringVar(value="就绪")
        self._label(controls, textvariable=self._status_var).grid(row=0, column=0, sticky="w")

        self._label(controls, text="界面字号").grid(row=0, column=1, sticky="e", padx=(8, 6))
        self._font_size_var = self._tk.StringVar(value=str(self._font_size))
        font_size_box = self._combobox(
            controls,
            textvariable=self._font_size_var,
            values=("14", "16", "18", "20", "22", "24", "26"),
            width=6,
            state="readonly",
        )
        font_size_box.grid(row=0, column=2, sticky="e", padx=(0, 8))
        font_size_box.configure(command=lambda _value: self._apply_font_size())

        self._global_download_button = self._button(
            controls,
            text="DOWNLOAD / 开始下载",
            command=self._start_download,
            style="Primary.TButton",
        )
        self._global_download_button.grid(row=0, column=3, sticky="e", padx=(0, 8))

        self._stop_button = self._button(controls, text="停止当前任务", command=self._stop_current_process, state="disabled")
        self._stop_button.grid(row=0, column=4, sticky="e")

        self._build_volume_panel(main)

        log_frame = self._label_frame(main, text="运行日志", padding=8)
        log_frame.grid(row=3, column=1, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(self._content_row(log_frame, 0), weight=1)

        self._log_text = self._textbox(log_frame, height=10, state="disabled")
        self._log_text.grid(
            row=self._content_row(log_frame, 0),
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )
        self._show_page("下载")
        self._migrate_legacy_login_secret()
        self._load_initial_backend_config()

    def _build_nav_button(self, title: str, row: int) -> None:
        button = self._ctk.CTkButton(
            self._nav_frame,
            text=title,
            command=lambda page=title: self._show_page(page),
            anchor="w",
            height=42,
            font=self._font_tuple(bold=True),
            fg_color="transparent",
            text_color=self._colors["fg"],
            hover_color=self._colors["row_alt"],
        )
        button.grid(row=row, column=0, sticky="ew", padx=10, pady=(10 if row == 0 else 0, 8))
        self._nav_buttons[title] = button

    def _make_page(self, title: str):
        page = self._frame(self._page_container)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self._pages[title] = page
        return page

    def _show_page(self, title: str) -> None:
        for page_title, page in self._pages.items():
            if page_title == title:
                page.grid()
            else:
                page.grid_remove()

        volume_frame = getattr(self, "_volume_frame", None)
        if title == "下载":
            self._workspace.grid(row=0, column=1, sticky="nsew")
            self._main.rowconfigure(0, weight=3)
            self._main.rowconfigure(2, weight=2)
            if volume_frame is not None:
                volume_frame.grid()
        else:
            self._workspace.grid(row=0, column=1, sticky="nsew")
            self._main.rowconfigure(0, weight=5)
            self._main.rowconfigure(2, weight=0)
            if volume_frame is not None:
                volume_frame.grid_remove()
        self._main.rowconfigure(3, weight=1)

        self._active_page = title
        for page_title, button in self._nav_buttons.items():
            if page_title == title:
                button.configure(fg_color=self._colors["accent"], text_color="#ffffff", hover_color=self._colors["accent"])
            else:
                button.configure(fg_color="transparent", text_color=self._colors["fg"], hover_color=self._colors["row_alt"])

    def _build_download_tab(self) -> None:
        frame = self._add_scrollable_tab("下载")

        for idx in range(4):
            frame.columnconfigure(idx, weight=1)
        frame.rowconfigure(16, weight=1)

        header = self._frame(frame)
        header.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        self._label(header, text="下载任务", font=self._heading_font).grid(row=0, column=0, sticky="w")
        self._button(header, text="DOWNLOAD / 开始下载", command=self._start_download, style="Primary.TButton").grid(row=0, column=1, sticky="e")

        self._download_book_url = self._tk.StringVar()
        self._download_dest = self._tk.StringVar(value=_default_download_dest())
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
        self._button(frame, text="选择目录", command=self._choose_download_dest).grid(row=4, column=3, sticky="ew", padx=(8, 0), pady=4)

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

        flags = self._frame(frame)
        flags.grid(row=13, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        for idx in range(5):
            flags.columnconfigure(idx, weight=1)

        self._checkbutton(flags, text="启用凭证池", variable=self._download_use_pool).grid(row=0, column=0, sticky="w")
        self._checkbutton(flags, text="使用 VIP 链接", variable=self._download_vip).grid(row=0, column=1, sticky="w")
        self._checkbutton(flags, text="尝试分片", variable=self._download_try_multi_part).grid(row=0, column=2, sticky="w")
        self._checkbutton(flags, text="禁用分片", variable=self._download_disable_multi_part).grid(row=0, column=3, sticky="w")
        self._checkbutton(flags, text="随机 UA", variable=self._download_fake_ua).grid(row=0, column=4, sticky="w")

        actions = self._frame(frame)
        actions.grid(row=14, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        actions.columnconfigure(2, weight=1)
        self._button(actions, text="预估下载计划", command=self._explain_download).grid(row=0, column=0, padx=(0, 8))
        self._button(actions, text="DOWNLOAD / 开始下载", command=self._start_download, style="Primary.TButton").grid(row=0, column=1)

        progress_frame = self._frame(frame)
        progress_frame.grid(row=15, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        progress_frame.columnconfigure(0, weight=1)
        self._download_progress = self._progressbar(progress_frame)
        self._download_progress.grid(row=0, column=0, sticky="ew")

    def _build_volume_panel(self, parent) -> None:
        self._volume_frame = self._label_frame(parent, text="已解析卷列表", padding=8)
        self._volume_frame.grid(row=2, column=1, sticky="nsew")
        self._volume_frame.columnconfigure(0, weight=1)
        self._volume_frame.rowconfigure(self._content_row(self._volume_frame, 1), weight=1)

        volume_actions = self._frame(self._volume_frame)
        volume_actions.grid(
            row=self._content_row(self._volume_frame, 0),
            column=0,
            sticky="ew",
            padx=8,
            pady=(8, 8),
        )
        volume_actions.columnconfigure(4, weight=1)
        self._button(volume_actions, text="解析卷列表", command=self._parse_download_volumes).grid(row=0, column=0, padx=(0, 8))
        self._button(volume_actions, text="应用选中卷", command=self._apply_selected_volumes).grid(row=0, column=1, padx=(0, 8))
        self._button(volume_actions, text="全选", command=self._select_all_parsed_volumes).grid(row=0, column=2, padx=(0, 8))
        self._button(volume_actions, text="清空选择", command=self._clear_volume_selection).grid(row=0, column=3, padx=(0, 8))

        self._volume_table = self._frame(self._volume_frame, fg_color=self._colors["field"])
        self._volume_table.grid(
            row=self._content_row(self._volume_frame, 1),
            column=0,
            sticky="nsew",
            padx=8,
            pady=(0, 8),
        )
        self._volume_table.columnconfigure(0, weight=1)
        self._volume_table.rowconfigure(1, weight=1)

        self._build_volume_table_header()
        self._volume_rows_frame = self._scrollable_frame(self._volume_table, height=320, fg_color=self._colors["field"])
        self._volume_rows_frame.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        for idx, (_key, weight, _title) in enumerate(self._volume_columns):
            self._volume_rows_frame.columnconfigure(idx, weight=weight, minsize=54)

    def _add_scrollable_tab(self, title: str):
        tab = self._make_page(title)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        frame = self._scrollable_frame(tab)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        return frame

    def _build_table_header(self, parent, columns: tuple[tuple[str, int, str], ...]) -> None:
        for idx, (_key, weight, title) in enumerate(columns):
            parent.columnconfigure(idx, weight=weight, minsize=60)
            label = self._ctk.CTkLabel(parent, text=title, font=self._font_tuple(bold=True), anchor="w", fg_color=self._colors["panel"])
            label.grid(row=0, column=idx, sticky="ew", padx=0, pady=(0, 1), ipady=6)

    def _build_volume_table_header(self) -> None:
        self._volume_columns = (
            ("index", 1, "卷号"),
            ("type", 1, "类型"),
            ("name", 4, "卷名"),
            ("pages", 1, "页数"),
            ("size", 1, "大小 MB"),
            ("extra", 2, "状态"),
        )
        self._volume_header = self._frame(self._volume_table, fg_color=self._colors["panel"])
        self._volume_header.grid(row=0, column=0, sticky="ew", padx=1, pady=(1, 0))
        self._build_table_header(self._volume_header, self._volume_columns)

    def _row_color(self, selected: bool, index: int) -> str:
        if selected:
            return self._colors["accent"]
        return self._colors["row_alt"] if index % 2 else self._colors["row"]

    def _bind_row_click(self, widget, callback: Callable) -> None:
        widget.bind("<Button-1>", callback)
        for child in widget.winfo_children():
            self._bind_row_click(child, callback)

    def _bind_cells(self, cells: list, sequence: str, callback: Callable) -> None:
        for cell in cells:
            cell.bind(sequence, callback)

    def _bind_widget_tree(self, widget, sequence: str, callback: Callable) -> None:
        widget.bind(sequence, callback)
        for child in widget.winfo_children():
            self._bind_widget_tree(child, sequence, callback)

    def _configure_search_card_tree(self, widget, row_color: str, text_color: str, muted_color: str) -> None:
        role = getattr(widget, "_kmdr_role", "")
        try:
            if role == "card":
                widget.configure(fg_color=row_color)
            elif role == "muted":
                widget.configure(text_color=muted_color)
            elif role:
                widget.configure(text_color=text_color)
        except self._tk.TclError:
            pass

        for child in widget.winfo_children():
            self._configure_search_card_tree(child, row_color, text_color, muted_color)

    def _build_search_tab(self) -> None:
        frame = self._make_page("搜索")

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        self._search_keyword = self._tk.StringVar()
        self._search_page = self._tk.StringVar(value="1")

        form = self._frame(frame)
        form.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        form.columnconfigure(1, weight=1)

        self._label(form, text="关键词").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._entry(form, textvariable=self._search_keyword).grid(row=0, column=1, sticky="ew")
        self._label(form, text="页码").grid(row=0, column=2, sticky="w", padx=(10, 8))
        self._entry(form, textvariable=self._search_page, width=8).grid(row=0, column=3, sticky="w")
        self._button(form, text="搜索", command=self._start_search).grid(row=0, column=4, padx=(10, 0))

        self._search_rows_frame = self._scrollable_frame(frame, height=360)
        self._search_rows_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(10, 0))
        self._search_rows_frame.columnconfigure(0, weight=1)
        self._search_rows_frame.bind("<Control-c>", lambda _event: self._copy_selected_search_result("row"))

        self._search_result_menu = self._tk.Menu(self._root, tearoff=False)
        self._search_result_menu.add_command(label="复制书名", command=lambda: self._copy_selected_search_result("name"))
        self._search_result_menu.add_command(label="复制作者", command=lambda: self._copy_selected_search_result("author"))
        self._search_result_menu.add_command(label="复制链接", command=lambda: self._copy_selected_search_result("url"))
        self._search_result_menu.add_command(label="复制整行", command=lambda: self._copy_selected_search_result("row"))
        self._search_result_menu.add_separator()
        self._search_result_menu.add_command(label="搜索同作者", command=self._search_selected_author)

        actions = self._frame(frame)
        actions.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 12))
        self._button(actions, text="使用选中链接下载", command=self._use_selected_search_result).grid(row=0, column=0)
        self._button(actions, text="复制书名", command=lambda: self._copy_selected_search_result("name")).grid(row=0, column=1, padx=(8, 0))
        self._button(actions, text="复制作者", command=lambda: self._copy_selected_search_result("author")).grid(row=0, column=2, padx=(8, 0))
        self._button(actions, text="复制链接", command=lambda: self._copy_selected_search_result("url")).grid(row=0, column=3, padx=(8, 0))
        self._button(actions, text="搜索同作者", command=self._search_selected_author).grid(row=0, column=4, padx=(8, 0))

    def _build_account_tab(self) -> None:
        frame = self._make_page("账户")

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

        self._label(frame, text="密码").grid(row=0, column=1, sticky="w", padx=(8, 12), pady=(12, 0))
        self._entry(frame, textvariable=self._login_password, show="*").grid(row=1, column=1, sticky="ew", padx=(8, 12), pady=4)

        self._checkbutton(
            frame,
            text="记住账号密码（加密保存）",
            variable=self._remember_login,
            command=self._on_remember_login_changed,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 4))

        self._add_labeled_entry(frame, "状态检查代理", self._status_proxy, 3, 0, columnspan=2)

        actions = self._frame(frame)
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 0))
        self._button(actions, text="登录并保存 Cookie", command=self._start_login).grid(row=0, column=0, padx=(0, 8))
        self._button(actions, text="查看账户状态", command=self._start_status).grid(row=0, column=1)
        self._button(actions, text="清除已保存账号", command=lambda: self._forget_saved_login(show_message=True, clear_fields=True)).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )

        self._account_text = self._textbox(frame, height=12, state="disabled")
        self._account_text.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=12, pady=(12, 12))
        frame.rowconfigure(6, weight=1)

    def _build_config_tab(self) -> None:
        frame = self._make_page("配置")

        for idx in range(3):
            frame.columnconfigure(idx, weight=1)

        self._config_base_url = self._tk.StringVar()
        self._config_dest = self._tk.StringVar()
        self._config_proxy = self._tk.StringVar()
        self._config_workers = self._tk.StringVar()
        self._config_retry = self._tk.StringVar()
        self._config_format = self._tk.StringVar()

        self._add_labeled_entry(frame, "镜像站基础 URL", self._config_base_url, 0, 0, columnspan=2)
        self._button(frame, text="保存镜像站", command=self._set_base_url).grid(row=1, column=2, sticky="ew", padx=(8, 12), pady=4)

        self._add_labeled_entry(frame, "默认保存目录", self._config_dest, 2, 0, columnspan=2)
        self._button(frame, text="选择目录", command=self._choose_config_dest).grid(row=3, column=2, sticky="ew", padx=(8, 12), pady=4)
        self._add_labeled_entry(frame, "默认代理", self._config_proxy, 4, 0)
        self._add_labeled_entry(frame, "默认并发数", self._config_workers, 4, 1)
        self._add_labeled_entry(frame, "默认重试次数", self._config_retry, 4, 2)
        self._add_labeled_combobox(frame, "默认格式", self._config_format, 6, 0, values=("", "epub", "mobi"))

        actions = self._frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 0))
        self._button(actions, text="保存下载默认项", command=self._set_download_defaults).grid(row=0, column=0, padx=(0, 8))
        self._button(actions, text="查看当前配置", command=self._list_config).grid(row=0, column=1)

        self._config_text = self._textbox(frame, height=12, state="disabled")
        self._config_text.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=12, pady=(12, 12))
        frame.rowconfigure(9, weight=1)

    def _add_labeled_entry(self, parent, label: str, variable, row: int, column: int, columnspan: int = 1) -> None:
        left_pad = 12 if column == 0 else 8
        right_pad = 12 if column + columnspan >= 3 else 0
        self._label(parent, text=label).grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="w",
            padx=(left_pad, right_pad),
            pady=(12, 0),
        )
        self._entry(parent, textvariable=variable).grid(
            row=row + 1,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=(left_pad, right_pad),
            pady=4,
        )

    def _add_labeled_combobox(self, parent, label: str, variable, row: int, column: int, values: tuple[str, ...]) -> None:
        left_pad = 12 if column == 0 else 8
        right_pad = 12 if column >= 3 else 0
        self._label(parent, text=label).grid(
            row=row,
            column=column,
            sticky="w",
            padx=(left_pad, right_pad),
            pady=(12, 0),
        )
        self._combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row + 1,
            column=column,
            sticky="ew",
            padx=(left_pad, right_pad),
            pady=4,
        )

    def _apply_font_size(self) -> None:
        os.environ["KMDR_GUI_FONT_SIZE"] = self._font_size_var.get()
        self._configure_fonts()
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

        self._start_backend_task("登录", lambda backend: backend.login(username=username, password=password), handle_login_result)

    def _start_status(self) -> None:
        self._start_backend_task("账户状态", lambda backend: backend.status(proxy=self._status_proxy.get()), self._render_account_result)

    def _start_search(self) -> None:
        keyword = self._search_keyword.get().strip()
        if not keyword:
            self._messagebox.showwarning("缺少关键词", "请填写搜索关键词。")
            return

        self._start_backend_task("搜索", lambda backend: backend.search(keyword=keyword, page=self._search_page.get()), self._render_search_result)

    def _use_selected_search_result(self) -> None:
        selected = self._selected_search_item_ids()
        if not selected:
            self._messagebox.showinfo("未选择条目", "请先在搜索结果中选择一本漫画。")
            return

        item_id = selected[0]
        values = self._search_row_values(item_id)
        if len(values) < 4:
            return

        self._download_book_url.set(values[3])
        self._show_page("下载")
        self._parse_download_volumes()

    def _selected_search_item_ids(self) -> list[str]:
        return sorted(self._selected_search_items, key=lambda item_id: int(item_id))

    def _search_row_values(self, item_id: str) -> tuple[str, str, str, str]:
        try:
            book = self._search_results[int(item_id)]
        except (ValueError, IndexError):
            return "", "", "", ""

        return (
            str(book.get("name", "")),
            str(book.get("author", "")),
            str(book.get("status", "")),
            str(book.get("url", "")),
        )

    def _selected_search_rows(self) -> list[tuple[str, str, str, str]]:
        return [self._search_row_values(item_id) for item_id in self._selected_search_item_ids()]

    def _show_search_result_menu(self, event):
        item_id = getattr(event.widget, "_kmdr_item_id", None)
        if item_id:
            self._select_search_item(item_id, additive=False)

        if not self._selected_search_items:
            return "break"

        try:
            self._search_result_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._search_result_menu.grab_release()
        return "break"

    def _copy_selected_search_result(self, field: str) -> str:
        rows = self._selected_search_rows()
        if not rows:
            self._messagebox.showinfo("未选择条目", "请先在搜索结果中选择一本漫画。")
            return "break"

        labels = {
            "name": "书名",
            "author": "作者",
            "status": "状态",
            "url": "链接",
            "row": "搜索结果",
        }
        column_indexes = {"name": 0, "author": 1, "status": 2, "url": 3}

        if field == "row":
            text = "\n".join("\t".join(row) for row in rows)
        else:
            index = column_indexes.get(field)
            if index is None:
                return "break"
            text = "\n".join(row[index] for row in rows if row[index])

        if not text:
            self._messagebox.showinfo("没有可复制内容", f"选中结果没有可复制的{labels.get(field, '内容')}。")
            return "break"

        self._root.clipboard_clear()
        self._root.clipboard_append(text)
        self._root.update_idletasks()
        self._status_var.set(f"已复制{labels.get(field, '内容')}")
        return "break"

    def _search_selected_author(self) -> None:
        rows = self._selected_search_rows()
        if not rows:
            self._messagebox.showinfo("未选择条目", "请先在搜索结果中选择一本漫画。")
            return

        author = rows[0][1].strip()
        if not author:
            self._messagebox.showinfo("缺少作者", "选中的搜索结果没有作者信息。")
            return

        self._search_keyword.set(author)
        self._search_page.set("1")
        self._start_search()

    def _select_search_item(self, item_id: str, additive: bool = False) -> None:
        if additive:
            if item_id in self._selected_search_items:
                self._selected_search_items.remove(item_id)
            else:
                self._selected_search_items.add(item_id)
        else:
            self._selected_search_items = {item_id}
        self._refresh_search_row_styles()

    def _refresh_search_row_styles(self) -> None:
        for item_id, card in self._search_row_widgets.items():
            selected = item_id in self._selected_search_items
            row_color = self._row_color(selected, int(item_id))
            text_color = "#ffffff" if selected else self._colors["fg"]
            muted_color = "#dbeafe" if selected else self._colors["muted"]
            border_color = self._colors["accent"] if selected else self._colors["border"]
            card.configure(fg_color=row_color, border_color=border_color)
            self._configure_search_card_tree(card, row_color, text_color, muted_color)

    def _make_search_row(self, item_id: str, values: tuple[str, str, str, str]) -> None:
        row_index = int(item_id)
        name, author, status, url = values

        card = self._ctk.CTkFrame(
            self._search_rows_frame,
            fg_color=self._row_color(False, row_index),
            border_width=1,
            border_color=self._colors["border"],
            corner_radius=8,
        )
        card.grid(row=row_index, column=0, sticky="ew", padx=2, pady=(0, 8))
        card.columnconfigure(0, weight=1)
        card._kmdr_item_id = item_id
        card._kmdr_role = "card"

        title_row = self._frame(card, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        title_row.columnconfigure(0, weight=1)
        title_row._kmdr_item_id = item_id

        name_label = self._ctk.CTkLabel(
            title_row,
            text=name or "未命名漫画",
            anchor="w",
            justify="left",
            font=self._font_tuple(bold=True),
            text_color=self._colors["fg"],
            wraplength=760,
        )
        name_label.grid(row=0, column=0, sticky="ew")
        name_label._kmdr_item_id = item_id
        name_label._kmdr_role = "primary"

        status_label = self._ctk.CTkLabel(
            title_row,
            text=status or "状态未知",
            anchor="e",
            justify="right",
            text_color=self._colors["muted"],
            width=96,
        )
        status_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        status_label._kmdr_item_id = item_id
        status_label._kmdr_role = "muted"

        author_label = self._ctk.CTkLabel(
            card,
            text=f"作者：{author}" if author else "作者：未知",
            anchor="w",
            justify="left",
            text_color=self._colors["muted"],
            wraplength=900,
        )
        author_label.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 0))
        author_label._kmdr_item_id = item_id
        author_label._kmdr_role = "muted"

        url_label = self._ctk.CTkLabel(
            card,
            text=url,
            anchor="w",
            justify="left",
            text_color=self._colors["muted"],
            wraplength=900,
        )
        url_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 10))
        url_label._kmdr_item_id = item_id
        url_label._kmdr_role = "muted"

        def update_wraplength(_event=None) -> None:
            body_width = max(260, card.winfo_width() - 32)
            name_label.configure(wraplength=max(240, body_width - 120))
            author_label.configure(wraplength=body_width)
            url_label.configure(wraplength=body_width)

        def click(event) -> str:
            additive = bool(getattr(event, "state", 0) & 0x0004)
            self._select_search_item(item_id, additive=additive)
            card.focus_set()
            return "break"

        def double_click(_event) -> str:
            self._select_search_item(item_id, additive=False)
            self._use_selected_search_result()
            return "break"

        self._bind_widget_tree(card, "<Button-1>", click)
        self._bind_widget_tree(card, "<Double-1>", double_click)
        self._bind_widget_tree(card, "<Button-3>", self._show_search_result_menu)
        self._bind_widget_tree(card, "<Control-c>", lambda _event: self._copy_selected_search_result("row"))
        card.bind("<Configure>", update_wraplength, add="+")
        update_wraplength()

        self._search_row_widgets[item_id] = card

    def _start_download(self) -> None:
        options = self._collect_download_options(explain=False)
        if options is None:
            return

        self._set_progress(0)
        self._start_backend_task("下载", lambda backend: backend.download(options), self._render_download_result)

    def _explain_download(self) -> None:
        options = self._collect_download_options(explain=True)
        if options is None:
            return

        self._set_progress(0)
        self._start_backend_task("预估下载计划", lambda backend: backend.explain_download(options), self._render_download_result)

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

        self._set_progress(0)
        self._clear_parsed_volumes()
        self._start_backend_task("解析卷列表", lambda backend: backend.parse_volumes(options), self._render_volume_parse_result)

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
        self._selected_volume_items.clear()
        for cells in self._volume_row_widgets.values():
            for cell in cells:
                cell.destroy()
        self._volume_row_widgets.clear()

    def _render_parsed_volumes(self, volumes: list[dict]) -> None:
        for cells in self._volume_row_widgets.values():
            for cell in cells:
                cell.destroy()
        self._volume_row_widgets.clear()
        self._selected_volume_items.clear()

        for idx, volume in enumerate(volumes):
            size = volume.get("size")
            if isinstance(size, (int, float)):
                size_text = f"{size:.2f}"
            else:
                size_text = ""

            self._make_volume_row(
                str(idx),
                (
                    str(volume.get("index", "")),
                    str(volume.get("type_label") or volume.get("type", "")),
                    str(volume.get("name", "")),
                    str(volume.get("pages", "")),
                    size_text,
                    str(volume.get("extra_info", "")),
                ),
            )

    def _selected_parsed_volumes(self) -> list[dict]:
        selected = []
        for item_id in sorted(self._selected_volume_items, key=lambda value: int(value)):
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
        self._selected_volume_items = set(self._volume_row_widgets)
        self._refresh_volume_row_styles()

    def _clear_volume_selection(self) -> None:
        self._selected_volume_items.clear()
        self._refresh_volume_row_styles()

    def _select_volume_item(self, item_id: str, additive: bool = True) -> None:
        if additive:
            if item_id in self._selected_volume_items:
                self._selected_volume_items.remove(item_id)
            else:
                self._selected_volume_items.add(item_id)
        else:
            self._selected_volume_items = {item_id}
        self._refresh_volume_row_styles()

    def _refresh_volume_row_styles(self) -> None:
        for item_id, cells in self._volume_row_widgets.items():
            selected = item_id in self._selected_volume_items
            row_color = self._row_color(selected, int(item_id))
            text_color = "#ffffff" if selected else self._colors["fg"]
            for cell in cells:
                cell.configure(fg_color=row_color, text_color=text_color)

    def _make_volume_row(self, item_id: str, values: tuple[str, str, str, str, str, str]) -> None:
        row_index = int(item_id)
        cells = []

        for idx, ((_, weight, _title), value) in enumerate(zip(self._volume_columns, values)):
            self._volume_rows_frame.columnconfigure(idx, weight=weight, minsize=54)
            label = self._ctk.CTkLabel(
                self._volume_rows_frame,
                text=value,
                anchor="w",
                wraplength=360,
                justify="left",
                fg_color=self._row_color(False, row_index),
            )
            label.grid(row=row_index, column=idx, sticky="nsew", padx=0, pady=(0, 1), ipady=7)
            label._kmdr_item_id = item_id
            cells.append(label)

        def click(event) -> str:
            self._select_volume_item(item_id, additive=True)
            return "break"

        self._bind_cells(cells, "<Button-1>", click)
        self._volume_row_widgets[item_id] = cells

    def _set_base_url(self) -> None:
        base_url = self._config_base_url.get().strip()
        if not base_url:
            self._messagebox.showwarning("缺少镜像站", "请填写镜像站基础 URL。")
            return
        self._start_backend_task("保存镜像站", lambda backend: backend.set_base_url(base_url), self._render_config_result)

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

        self._start_backend_task("保存下载默认项", lambda backend: backend.set_download_defaults(assignments), self._render_config_result)

    def _list_config(self) -> None:
        self._start_backend_task("查看当前配置", lambda backend: backend.list_config(), self._render_config_result)

    def _load_initial_backend_config(self) -> None:
        runner = GuiBackendRunner()
        payload = runner.run(lambda backend: backend.list_config())
        if payload.get("code") == 0:
            self._apply_config_payload(payload.get("data") or {})
            return

        self._append_log(f"[GUI] 读取当前配置失败：{payload.get('msg', '未知错误')}")

    def _apply_config_payload(self, data: dict) -> None:
        option = data.get("option") if isinstance(data.get("option"), dict) else {}

        self._config_base_url.set(str(data.get("base_url") or ""))
        self._config_dest.set(str(option.get("dest") or ""))
        self._config_proxy.set(str(option.get("proxy") or ""))
        self._config_workers.set(str(option.get("num_workers") or ""))
        self._config_retry.set(str(option.get("retry") or ""))
        self._config_format.set(str(option.get("format") or ""))

        if option.get("dest"):
            self._download_dest.set(str(option["dest"]))
        if option.get("proxy"):
            self._download_proxy.set(str(option["proxy"]))
            self._status_proxy.set(str(option["proxy"]))
        if option.get("num_workers"):
            self._download_workers.set(str(option["num_workers"]))
        if option.get("retry"):
            self._download_retry.set(str(option["retry"]))
        if option.get("format"):
            self._download_format.set(str(option["format"]))

    def _start_backend_task(self, label: str, operation: Callable[[GuiBackend], object], result_handler: Callable[[dict], None]) -> None:
        if self._command_running:
            self._messagebox.showinfo("任务运行中", "请等待当前任务结束，或先停止当前任务。")
            return

        self._command_running = True
        self._result_handler = result_handler
        self._status_var.set(f"{label}运行中...")
        self._stop_button.configure(state="normal")
        self._append_log(f"[GUI] {label}已开始。")

        self._worker = threading.Thread(target=self._run_backend_task, args=(label, operation), daemon=True)
        self._worker.start()

    def _run_backend_task(self, label: str, operation: Callable[[GuiBackend], object]) -> None:
        try:
            runner = GuiBackendRunner(progress_callback=lambda **payload: self._events.put(("progress", payload)))
            self._backend_runner = runner
            try:
                payload = runner.run(operation)
            finally:
                self._backend_runner = None
            self._events.put(("result", payload))
            returncode = int(payload.get("code", 50)) if isinstance(payload, dict) else 50
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
            if kind == "progress":
                self._handle_progress(event[1])
            elif kind == "result" and self._result_handler:
                self._result_handler(event[1])
            elif kind == "done":
                self._handle_task_done(event[1], event[2])
            elif kind == "error":
                self._append_log(f"[{event[1]}] {event[2]}")
                self._status_var.set(f"{event[1]}失败")
                self._backend_runner = None
                self._command_running = False
                self._stop_button.configure(state="disabled")

        self._root.after(100, self._poll_events)

    def _handle_progress(self, payload: dict) -> None:
        status = payload.get("status", "running")
        volume = payload.get("volume", "")
        percentage = payload.get("percentage")
        if isinstance(percentage, (int, float)):
            self._set_progress(percentage)
            self._status_var.set(f"下载中 {volume} {percentage}%")
        else:
            self._status_var.set(f"下载状态: {status}")

    def _handle_task_done(self, label: str, returncode: int) -> None:
        self._backend_runner = None
        self._command_running = False
        self._stop_button.configure(state="disabled")

        if returncode == 0:
            self._status_var.set(f"{label}完成")
        else:
            self._status_var.set(f"{label}退出，代码 {returncode}")
        self._append_log(f"[{label}] 任务结束，代码 {returncode}")

    def _render_account_result(self, payload: dict) -> None:
        self._render_json_to_text(self._account_text, payload)

    def _render_config_result(self, payload: dict) -> None:
        if payload.get("code") == 0:
            self._apply_config_payload(payload.get("data") or {})
        self._render_json_to_text(self._config_text, payload)

    def _render_search_result(self, payload: dict) -> None:
        for card in self._search_row_widgets.values():
            card.destroy()
        self._search_row_widgets.clear()
        self._selected_search_items.clear()

        data = payload.get("data") or {}
        books = data.get("books") or []
        self._search_results = books

        for idx, book in enumerate(books):
            self._make_search_row(
                str(idx),
                (
                    str(book.get("name", "")),
                    str(book.get("author", "")),
                    str(book.get("status", "")),
                    str(book.get("url", "")),
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
            self._set_progress(100)
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
        if not self._command_running:
            return

        if self._backend_runner is not None:
            self._backend_runner.terminate()
        self._append_log("[GUI] 已请求停止当前任务。")

    def _on_close(self) -> None:
        if self._command_running:
            should_close = self._messagebox.askyesno("任务运行中", "当前任务仍在运行，是否停止任务并退出？")
            if not should_close:
                return
            if self._backend_runner is not None:
                self._backend_runner.terminate()
        self._root.destroy()


def entry_point() -> None:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("无法启动图形界面：当前 Python 未安装 Tkinter。") from exc

    customtkinter = _load_customtkinter()
    _configure_customtkinter(customtkinter)
    root = customtkinter.CTk() if customtkinter is not None else tk.Tk()
    KmdrDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    entry_point()
