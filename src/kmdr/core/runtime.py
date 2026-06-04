import json
import os
from contextvars import ContextVar
from typing import Any, Optional

from rich.progress import (
    BarColumn,
    DownloadColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .constants import BASE_URL
from .encoder import KmdrJSONEncoder
from .error import InitializationError
from .structure import Config, Credential
from .utils import singleton

TRUE_UA = "kmdr/1.0 (https://github.com/chrisis58/kmoe-manga-downloader)"


progress_definition = (
    TextColumn("[blue]{task.fields[filename]}", justify="left"),
    TextColumn("{task.fields[status]}", justify="right"),
    TextColumn("{task.percentage:>3.1f}%"),
    BarColumn(bar_width=None),
    "[progress.percentage]",
    DownloadColumn(),
    "[",
    TransferSpeedColumn(),
    ",",
    TimeRemainingColumn(),
    "]",
)

session_var = ContextVar("session")


@singleton
class Configurer:
    def __init__(self):
        self.__filename = ".kmdr"

        if not os.path.exists(os.path.join(os.path.expanduser("~"), self.__filename)):
            self._config: Config = Config()
            self.update()
        else:
            with open(os.path.join(os.path.expanduser("~"), self.__filename)) as f:
                config_data = json.load(f)
            self._config: Config = Config.from_dict(config_data)

        if self._config is None:
            raise InitializationError("无法加载配置文件。")

    @property
    def config(self) -> "Config":
        return self._config

    @property
    def cookie(self) -> Optional[dict]:
        return self._config.cookie

    @cookie.setter
    def cookie(self, value: Optional[dict[str, str]]):
        self._config.cookie = value

    @property
    def option(self) -> dict:
        if self._config.option is None:
            self._config.option = {}
        return self._config.option

    @property
    def base_url(self) -> str:
        if self._config.base_url is None:
            return BASE_URL.DEFAULT.value
        return self._config.base_url

    def set_base_url(self, value: str):
        self._config.base_url = value

    def update(self):
        config_dir = os.path.expanduser("~")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, self.__filename), "w") as f:
            json.dump(
                self._config.__dict__,
                f,
                cls=KmdrJSONEncoder,
                indent=4,
                ensure_ascii=False,
            )

    def clear(self, key: str):
        if key == "all":
            self._config = Config()
        elif key == "cookie":
            self._config.cookie = None
            self._config.username = None
        elif key == "option":
            self._config.option = None
        else:
            raise KeyError(f"[red]对应配置不存在: {key}。可用配置项：all, cookie, option[/red]")

    def set_option(self, key: str, value: Any):
        if self._config.option is None:
            self._config.option = {}

        self._config.option[key] = value

    def unset_option(self, key: str):
        if self._config.option is None or key not in self._config.option:
            return

        del self._config.option[key]

    def save_credential(self, cred: Credential, as_primary: bool = False) -> None:
        """
        保存凭证到配置文件中的凭证池中。<br/>
        可能对凭证池不可见，如果要确保凭证池更新，应刷新凭证池实例。

        :param cred: 要保存的凭证对象
        :param as_primary: 是否将该凭证设置为主凭证
        """

        if as_primary:
            self._config.cookie = cred.cookies
            self._config.username = cred.username

        if self._config.cred_pool is None:
            self._config.cred_pool = []

        for idx, c in enumerate(self._config.cred_pool):
            if c.username == cred.username:
                self._config.cred_pool[idx] = cred
                self.update()
                return

        self._config.cred_pool.append(cred)
        self.update()


def __combine_args(dest, option: dict):
    if option is None:
        return dest

    for key, value in option.items():
        if hasattr(dest, key) and getattr(dest, key) is None:
            setattr(dest, key, value)
    return dest


def combine_args(dest):
    option = Configurer().option

    if option is None:
        return dest

    return __combine_args(dest, option)


base_url_var = ContextVar("base_url", default=BASE_URL.DEFAULT.value)
