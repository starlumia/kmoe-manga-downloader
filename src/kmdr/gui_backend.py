import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from kmdr import __version__
from kmdr.core.console import OutputMode, _set_output_mode, _update_verbose_setting
from kmdr.core.constants import API_ROUTE
from kmdr.core.encoder import SafeJSONEncoder
from kmdr.core.error import KmdrError, ValidationError
from kmdr.core.runtime import Configurer, base_url_var
from kmdr.core.session import KmdrSessionManager
from kmdr.core.structure import BookInfo, Credential, VolInfo
from kmdr.core.utils import SharedAwaitable
from kmdr.module.authenticator.CookieAuthenticator import CookieAuthenticator
from kmdr.module.authenticator.LoginAuthenticator import LoginAuthenticator
from kmdr.module.cataloger.utils import extract_search_results
from kmdr.module.downloader.DirectDownloader import DirectDownloader
from kmdr.module.downloader.FailoverDownloader import FailoverDownloader
from kmdr.module.downloader.ReferViaDownloader import ReferViaDownloader
from kmdr.module.lister.BookUrlLister import BookUrlLister
from kmdr.module.picker.ArgsFilterPicker import ArgsFilterPicker

ProgressCallback = Callable[..., None]


@dataclass(frozen=True)
class GuiDownloadOptions:
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


def success_payload(data: Any) -> dict[str, Any]:
    return {"type": "result", "code": 0, "msg": "success", "data": _safe_data(data)}


def error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "type": "result",
        "code": getattr(exc, "code", 50),
        "msg": str(exc) or exc.__class__.__name__,
        "data": None,
    }


def _safe_data(data: Any) -> Any:
    return json.loads(json.dumps(data, cls=SafeJSONEncoder, ensure_ascii=False))


def _option_is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _parse_int(value: object, default: int, field: str) -> int:
    if _option_is_empty(value):
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{field} 必须是整数: {value}", field=field) from exc
    return parsed


def _parse_float(value: object, default: Optional[float], field: str) -> Optional[float]:
    if _option_is_empty(value):
        return default
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{field} 必须是数字: {value}", field=field) from exc


def _parse_optional_int(value: object, field: str) -> Optional[int]:
    if _option_is_empty(value):
        return None
    return _parse_int(value, 0, field)


def _string_option(value: object, default: Optional[str] = "") -> Optional[str]:
    if _option_is_empty(value):
        return default
    return str(value).strip()


def _volume_summary(volume: VolInfo) -> dict[str, Any]:
    type_code = {
        "VOLUME": "vol",
        "EXTRA": "extra",
        "SERIALIZED": "seri",
    }[volume.vol_type.name]

    return {
        "id": volume.id,
        "index": volume.index,
        "name": volume.name,
        "type": type_code,
        "type_label": volume.vol_type.value,
        "pages": volume.pages,
        "size": volume.size,
        "is_last": volume.is_last,
        "extra_info": volume.extra_info,
    }


class GuiBackend:
    """GUI 专用业务层，直接复用网络解析和下载模块，不经过 CLI/main/argparse。"""

    def __init__(self, progress_callback: Optional[ProgressCallback] = None):
        self._configurer = Configurer()
        self._progress_callback = progress_callback
        _set_output_mode(OutputMode.LOG)
        _update_verbose_setting(False)

    def version(self) -> dict[str, Any]:
        return success_payload({"version": __version__})

    async def login(self, username: str, password: str) -> dict[str, Any]:
        self._reset_runtime_base_url()
        async with await KmdrSessionManager().session():
            cred = await LoginAuthenticator(username=username, password=password, show_quota=True, auto_save=True).authenticate()
            return success_payload(self._credential_status_data(cred))

    async def status(self, proxy: str = "") -> dict[str, Any]:
        self._reset_runtime_base_url()
        async with await KmdrSessionManager(proxy=_string_option(proxy, None)).session():
            cred = await CookieAuthenticator(auto_save=True, command="status", show_quota=True).authenticate()
            return success_payload(self._credential_status_data(cred))

    async def search(self, keyword: str, page: str = "1") -> dict[str, Any]:
        page_number = _parse_int(page, 1, "page")
        self._reset_runtime_base_url()

        async with await KmdrSessionManager().session() as session:
            cred = await CookieAuthenticator(auto_save=True).authenticate()
            url = API_ROUTE.SEARCH.format(keyword=quote(keyword), page=page_number)
            async with session.get(url, cookies=cred.cookies) as response:
                response.raise_for_status()
                books, total_pages = extract_search_results(await response.text())

        return success_payload(
            {
                "total_pages": total_pages,
                "page": page_number,
                "count": len(books),
                "books": books,
            }
        )

    async def parse_volumes(self, options: GuiDownloadOptions) -> dict[str, Any]:
        plan = await self._download_or_explain(options, force_explain=True)
        return success_payload(plan)

    async def explain_download(self, options: GuiDownloadOptions) -> dict[str, Any]:
        plan = await self._download_or_explain(options, force_explain=True)
        return success_payload(plan)

    async def download(self, options: GuiDownloadOptions) -> dict[str, Any]:
        summary = await self._download_or_explain(options, force_explain=False)
        return success_payload(summary)

    def list_config(self) -> dict[str, Any]:
        config = self._configurer.config
        return success_payload(
            {
                "base_url": self._configurer.base_url,
                "option": self._configurer.option,
                "username": config.username,
                "has_cookie": bool(config.cookie),
                "credential_pool_count": len(config.cred_pool or []),
            }
        )

    def set_base_url(self, base_url: str) -> dict[str, Any]:
        normalized = _string_option(base_url)
        if not normalized:
            raise ValidationError("镜像站基础 URL 不能为空。", field="base_url")

        self._configurer.set_base_url(normalized)
        self._configurer.update()
        base_url_var.set(normalized)
        return self.list_config()

    def set_download_defaults(self, assignments: Iterable[str]) -> dict[str, Any]:
        from kmdr.module.configurer.option_validate import validate

        updated: dict[str, Any] = {}
        for assignment in assignments:
            if "=" not in assignment:
                raise ValidationError(f"配置格式错误: {assignment}，应为 key=value。", field="assignment")
            key, value = assignment.split("=", 1)
            key = key.strip()
            value = value.strip()
            parsed = validate(key, value)
            self._configurer.set_option(key, parsed)
            updated[key] = parsed

        self._configurer.update()
        payload = (self.list_config()["data"] or {}) | {"updated": updated}
        return success_payload(payload)

    async def _download_or_explain(self, options: GuiDownloadOptions, force_explain: bool) -> dict[str, Any]:
        normalized = self._normalize_download_options(options, force_explain=force_explain)
        self._reset_runtime_base_url()

        async with await KmdrSessionManager(
            proxy=normalized["proxy"],
            book_url=normalized["book_url"],
            fake_ua=normalized["fake_ua"],
        ).session():
            cred, book, volumes = await self._prepare_download_data(normalized)
            picked = ArgsFilterPicker(
                volume=normalized["volume"],
                vol_type=normalized["vol_type"],
                max_size=normalized["max_size"],
                limit=normalized["limit"],
            ).pick(volumes)

            downloader = self._create_downloader(normalized)
            result = await downloader.download(cred, book, picked)

            if normalized["explain"] and result is not None:
                result.setdefault("volumes", [_volume_summary(volume) for volume in picked])
            return result or {"book": book.name, "total": len(picked), "completed": 0, "failed": 0, "skipped": 0}

    async def _prepare_download_data(self, options: dict[str, Any]) -> tuple[Credential, BookInfo, list[VolInfo]]:
        authenticator = CookieAuthenticator(auto_save=True)
        lister = BookUrlLister(book_url=options["book_url"])

        auth_task = SharedAwaitable(authenticator.authenticate())
        list_task: Awaitable[tuple[BookInfo, list[VolInfo]]] = lister.list(awaitable_cred=auth_task)

        cred, (book, volumes) = await asyncio.gather(auth_task, list_task)
        return cred, book, volumes

    def _create_downloader(self, options: dict[str, Any]):
        common = {
            "dest": options["dest"],
            "format": options["format"],
            "callback": options["callback"],
            "retry": options["retry"],
            "num_workers": options["num_workers"],
            "explain": options["explain"],
            "vip": options["vip"],
            "disable_multi_part": options["disable_multi_part"],
            "progress_callback": self._progress_callback,
        }

        if options["use_pool"]:
            return FailoverDownloader(
                method=options["method"],
                per_cred_ratio=options["per_cred_ratio"],
                try_multi_part=options["try_multi_part"],
                **common,
            )

        if options["method"] == 2:
            return DirectDownloader(**common)

        return ReferViaDownloader(try_multi_part=options["try_multi_part"], **common)

    def _normalize_download_options(self, options: GuiDownloadOptions, force_explain: bool) -> dict[str, Any]:
        saved_options = self._configurer.option

        def configured(name: str, current: object, default: object = "") -> object:
            if not _option_is_empty(current):
                return current
            return saved_options.get(name, default)

        book_url = _string_option(options.book_url)
        if not book_url:
            raise ValidationError("漫画详情 URL 不能为空。", field="book_url")

        return {
            "book_url": book_url,
            "dest": _string_option(configured("dest", options.dest, "."), "."),
            "volume": _string_option(options.volume, "all"),
            "vol_type": _string_option(options.vol_type, "all"),
            "format": _string_option(configured("format", options.book_format, "epub"), "epub"),
            "method": _parse_int(options.method, 1, "method"),
            "proxy": _string_option(configured("proxy", options.proxy, ""), None),
            "retry": _parse_int(configured("retry", options.retry, 3), 3, "retry"),
            "callback": _string_option(configured("callback", options.callback, ""), None),
            "num_workers": _parse_int(configured("num_workers", options.num_workers, 8), 8, "num_workers"),
            "max_size": _parse_float(options.max_size, None, "max_size"),
            "limit": _parse_optional_int(options.limit, "limit"),
            "per_cred_ratio": _parse_float(options.per_cred_ratio, 1.0, "per_cred_ratio") or 1.0,
            "vip": options.vip,
            "disable_multi_part": options.disable_multi_part,
            "try_multi_part": options.try_multi_part,
            "fake_ua": options.fake_ua,
            "use_pool": options.use_pool,
            "explain": force_explain or options.explain,
        }

    def _reset_runtime_base_url(self) -> None:
        base_url_var.set(self._configurer.base_url)

    @staticmethod
    def _credential_status_data(cred: Credential) -> dict[str, Any]:
        data = _safe_data(cred)
        data["base_url"] = base_url_var.get()
        data["quota_remaining"] = cred.quota_remaining
        return data


class GuiBackendRunner:
    def __init__(self, progress_callback: Optional[ProgressCallback] = None):
        self._progress_callback = progress_callback
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

    def run(self, operation: Callable[[GuiBackend], Awaitable[dict[str, Any]] | dict[str, Any]]) -> dict[str, Any]:
        self._loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(self._loop)
            backend = GuiBackend(progress_callback=self._progress_callback)
            self._task = self._loop.create_task(self._run_operation(backend, operation))
            return self._loop.run_until_complete(self._task)
        finally:
            try:
                self._cancel_pending_tasks(self._loop)
            finally:
                asyncio.set_event_loop(None)
                self._loop.close()
                self._loop = None
                self._task = None

    def terminate(self) -> None:
        if self._loop is None or self._task is None or self._task.done():
            return
        self._loop.call_soon_threadsafe(self._task.cancel)

    async def _run_operation(
        self,
        backend: GuiBackend,
        operation: Callable[[GuiBackend], Awaitable[dict[str, Any]] | dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            result = operation(backend)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                return await result
            return result
        except asyncio.CancelledError:
            return {"type": "result", "code": 130, "msg": "操作已取消", "data": None}
        except KmdrError as exc:
            return error_payload(exc)
        except Exception as exc:
            return error_payload(exc)

    @staticmethod
    def _cancel_pending_tasks(loop: asyncio.AbstractEventLoop) -> None:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if not pending:
            return

        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
