import re
from typing import Optional, Union

from aiohttp import ClientSession
from bs4 import BeautifulSoup, Tag
from rich.console import Console
from yarl import URL

from kmdr.core.console import debug, info, is_interactive
from kmdr.core.constants import API_ROUTE
from kmdr.core.error import LoginError
from kmdr.core.structure import Credential, CredentialStatus, QuotaInfo
from kmdr.core.utils import async_retry, extract_cookies

NICKNAME_ID = "div_nickname_display"

VIP_ID = "div_user_vip"
NOR_ID = "div_user_nor"
LV1_ID = "div_user_lv1"

PATTERN_USER_RESET = r"Lv\d+\s*額度\s*:\s*每月\s*(\d+)\s*日"
PATTERN_USER_TOTAL = r"Lv\d+\s*每月額度\s*:\s*([\d.]+)\s*M"
PATTERN_USER_USED = r"本月已用免費額度\s*:\s*([\d.]+)\s*M"
PATTERN_NAV_LEVEL = r"\bLv\s*(\d+)\b"

PATTERN_VIP_RESET = r"VIP\s*額度\s*:\s*每月\s*(\d+)\s*日"
PATTERN_VIP_TOTAL = r"VIP\s*每月額度\s*:\s*([\d.]+)\s*M"
PATTERN_VIP_USED = r"本月已經用VIP額度\s*:\s*([\d.]+)\s*M"
PATTERN_PROFILE_ID = r"/u/(\d+)/"


@async_retry()
async def check_status(
    session: ClientSession,
    console: Console,
    username: str,
    cookies: dict[str, str],
    show_quota: bool = False,
) -> Credential:
    async with session.get(url=API_ROUTE.PROFILE, cookies=cookies) as response:
        response.raise_for_status()

        if response.history and any(resp.status in (301, 302, 307) for resp in response.history) and URL(response.url).path == API_ROUTE.LOGIN:
            raise LoginError(
                "凭证已失效，请重新登录。",
                ["kmdr config -c cookie", "kmdr login -u <username>"],
            )

        cookies = {**cookies, **extract_cookies(response)}

        # 如果后续有性能问题，可以先考虑使用 lxml 进行解析
        soup = BeautifulSoup(await response.text(), "html.parser")

        if _looks_like_login_page(soup, response):
            raise LoginError(
                "登录后仍然进入登录页，Cookie 未生效或当前镜像登录状态异常。请确认账号密码正确，或在配置页切换镜像站后重新登录。",
                ["清除已保存账号后重新登录", "在配置页切换镜像站，例如 https://mox.moe 或 https://kxx.moe"],
            )

        logged_in_page = _looks_like_logged_in_page(soup, response)

        var_define = extract_user_state_vars(soup)
        is_vip = _parse_optional_int(var_define.get("is_vip"))
        user_level = _parse_optional_int(var_define.get("user_level"))
        if user_level is None:
            user_level = _extract_user_level(soup)
        debug("解析到用户状态: is_vip=", is_vip, ", user_level=", user_level)

        nickname_node = soup.find("div", id=NICKNAME_ID)
        if isinstance(nickname_node, Tag):
            nickname = nickname_node.text.strip().split(" ")[0].replace("\xa0", "")
        elif logged_in_page:
            nickname = _fallback_nickname(soup, username, user_level)
        else:
            raise LoginError(
                "无法解析账户状态页：没有找到昵称区域。当前镜像可能返回了异常页面，或站点页面结构已经变化。",
                [
                    f"当前响应地址: {response.url}",
                    f"页面片段: {_page_snippet(soup)}",
                    "尝试在配置页切换镜像站后重新登录",
                ],
            )

        user_quota, vip_quota = extract_quota(soup)
        quota_known = _has_quota_data(soup)
        quota_node = _find_quota_node(soup, is_vip, user_level)
        if isinstance(quota_node, Tag):
            raw_quota = quota_node.text.strip().replace("\xa0", "")
            quota_known = True
        elif logged_in_page:
            raw_quota = (
                _format_quota_summary(user_quota, vip_quota)
                if quota_known
                else "当前账户页面未提供额度详情，下载时将由服务器判断额度。"
            )
        else:
            raise LoginError(
                "无法解析账户状态页：没有找到额度区域。当前镜像可能返回了异常页面，或站点页面结构已经变化。",
                [
                    f"当前响应地址: {response.url}",
                    f"页面片段: {_page_snippet(soup)}",
                    "尝试在配置页切换镜像站后重新登录",
                ],
            )

        if show_quota:
            if is_interactive():
                info(f"\n当前登录为 [bold cyan]{nickname}[/bold cyan]\n\n{raw_quota}")
            else:
                info(f"当前登录为 {nickname}")

        total_remaining = user_quota.total - user_quota.used + (vip_quota.total - vip_quota.used if vip_quota else 0.0)
        debug(f"用户 {username} 当前剩余额度: {total_remaining:.2f} MB")

        return Credential(
            username=username,
            nickname=nickname,
            cookies=cookies,
            user_quota=user_quota,
            vip_quota=vip_quota,
            level=user_level or 0,
            status=CredentialStatus.ACTIVE if not quota_known or total_remaining > 0.1 else CredentialStatus.QUOTA_EXCEEDED,
        )


def extract_var_define(script_text) -> dict[str, str]:
    var_define = {}
    for line in script_text.splitlines():
        line = line.strip()
        if line.startswith("var ") and "=" in line:
            var_name, var_value = line[4:].split("=", 1)
            var_value = var_value.strip().strip(";").strip('"')
            if var_name and var_value:
                var_define[var_name.strip()] = var_value
    debug("解析到变量定义: ", var_define)
    return var_define


def extract_user_state_vars(soup: BeautifulSoup) -> dict[str, str]:
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue

        script_text = script.get_text("\n", strip=False)
        if "is_vip" not in script_text and "user_level" not in script_text:
            continue

        var_define = extract_var_define(script_text)
        if "is_vip" in var_define or "user_level" in var_define:
            return var_define

    return {}


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def _looks_like_login_page(soup: BeautifulSoup, response) -> bool:
    if URL(response.url).path == API_ROUTE.LOGIN:
        return True

    login_form = soup.find("form", attrs={"action": API_ROUTE.LOGIN_DO})
    return isinstance(login_form, Tag)


def _looks_like_logged_in_page(soup: BeautifulSoup, response) -> bool:
    if _looks_like_login_page(soup, response):
        return False

    text = soup.get_text(" ", strip=True)
    return bool(
        re.search(PATTERN_NAV_LEVEL, text)
        and ("我的設置" in text or "帳號信息" in text or "我的主頁" in text)
    )


def _page_snippet(soup: BeautifulSoup, limit: int = 160) -> str:
    title = soup.find("title")
    title_text = title.get_text(" ", strip=True) if isinstance(title, Tag) else ""
    body_text = soup.get_text(" ", strip=True)
    text = " ".join(part for part in (title_text, body_text) if part)
    return text[:limit]


def _extract_user_level(soup: BeautifulSoup) -> Optional[int]:
    match = re.search(PATTERN_NAV_LEVEL, soup.get_text(" ", strip=True))
    return int(match.group(1)) if match else None


def _fallback_nickname(soup: BeautifulSoup, username: str, user_level: Optional[int] = None) -> str:
    profile_id = _extract_profile_id(soup)
    if profile_id:
        return f"用户{profile_id}"

    if username and username != "__FROM_COOKIE__":
        return username

    if user_level is not None:
        return f"Lv{user_level}用户"

    return "已登录用户"


def _extract_profile_id(soup: BeautifulSoup) -> Optional[str]:
    for link in soup.find_all("a", href=True):
        href = link.get("href")
        if not isinstance(href, str):
            continue

        match = re.search(PATTERN_PROFILE_ID, href)
        if match:
            return match.group(1)

    match = re.search(PATTERN_PROFILE_ID, soup.get_text(" ", strip=True))
    return match.group(1) if match else None


def _find_quota_node(soup: BeautifulSoup, is_vip: Optional[int] = None, user_level: Optional[int] = None) -> Optional[Tag]:
    expected = soup.find("div", id=__resolve_quota_id(is_vip, user_level))
    if isinstance(expected, Tag) and expected.get_text(strip=True):
        return expected

    for quota_id in (VIP_ID, LV1_ID, NOR_ID):
        node = soup.find("div", id=quota_id)
        if isinstance(node, Tag) and node.get_text(strip=True):
            return node

    return None


def _has_quota_data(soup: BeautifulSoup) -> bool:
    raw_text = soup.get_text(separator=" ", strip=True)
    return bool(re.search(PATTERN_USER_TOTAL, raw_text) or re.search(PATTERN_USER_USED, raw_text) or re.search(PATTERN_VIP_TOTAL, raw_text))


def _format_quota_summary(user_quota: QuotaInfo, vip_quota: Optional[QuotaInfo]) -> str:
    summary = f"Lv额度：每月 {user_quota.total:.2f}M，已用 {user_quota.used:.2f}M"
    if vip_quota is not None:
        summary += f"\nVIP额度：每月 {vip_quota.total:.2f}M，已用 {vip_quota.used:.2f}M"
    return summary


def extract_quota(soup: BeautifulSoup) -> tuple[QuotaInfo, Union[QuotaInfo, None]]:
    is_vip = False
    vip_div = soup.find("div", id=VIP_ID)

    if isinstance(vip_div, Tag):
        style = vip_div.get("style", "")
        if isinstance(style, list):
            style = " ".join(style)

        style_str = style.lower().replace(" ", "") if style else ""

        if "display:none" not in style_str:
            is_vip = True

    raw_text = soup.get_text(separator=" ", strip=True)

    user_quota = QuotaInfo(
        reset_day=_extract_int(PATTERN_USER_RESET, raw_text, default=1),
        total=_extract_float(PATTERN_USER_TOTAL, raw_text, default=0.0),
        used=_extract_float(PATTERN_USER_USED, raw_text, default=0.0),
    )

    vip_quota = None
    if is_vip:
        vip_quota = QuotaInfo(
            reset_day=_extract_int(PATTERN_VIP_RESET, raw_text, default=1),
            total=_extract_float(PATTERN_VIP_TOTAL, raw_text, default=0.0),
            used=_extract_float(PATTERN_VIP_USED, raw_text, default=0.0),
        )

    return user_quota, vip_quota


def _extract_int(pattern: str, text: str, default: int = 0) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else default


def _extract_float(pattern: str, text: str, default: float = 0.0) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else default


def __resolve_quota_id(is_vip: Optional[int] = None, user_level: Optional[int] = None):
    if is_vip is not None and is_vip >= 1:
        return VIP_ID

    if user_level is not None and user_level <= 1:
        return LV1_ID

    return NOR_ID
