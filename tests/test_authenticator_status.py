import unittest

from bs4 import BeautifulSoup

from kmdr.module.authenticator.utils import (
    LV1_ID,
    NOR_ID,
    _extract_profile_id,
    _extract_user_level,
    _fallback_nickname,
    _find_quota_node,
    _looks_like_logged_in_page,
    _page_snippet,
    extract_user_state_vars,
)


class TestAuthenticatorStatusParsing(unittest.TestCase):
    def test_page_snippet_uses_title_and_body(self):
        soup = BeautifulSoup("<html><title>登录</title><body>请先登录后继续操作</body></html>", "html.parser")

        self.assertIn("登录", _page_snippet(soup))
        self.assertIn("请先登录", _page_snippet(soup))

    def test_user_state_vars_can_appear_after_other_scripts(self):
        soup = BeautifulSoup(
            """
            <html>
                <script language="javascript">var unrelated = 1;</script>
                <script>
                    var is_vip = 0;
                    var user_level = 3;
                </script>
            </html>
            """,
            "html.parser",
        )

        self.assertEqual(extract_user_state_vars(soup), {"is_vip": "0", "user_level": "3"})

    def test_find_quota_node_falls_back_to_available_quota_area(self):
        soup = BeautifulSoup(
            f"""
            <html>
                <div id="{LV1_ID}"></div>
                <div id="{NOR_ID}">Lv3 每月額度 : 1024M</div>
            </html>
            """,
            "html.parser",
        )

        node = _find_quota_node(soup, is_vip=0, user_level=1)

        self.assertIsNotNone(node)
        self.assertEqual(node.get("id"), NOR_ID)

    def test_settings_page_can_be_recognized_as_logged_in(self):
        soup = BeautifulSoup(
            """
            <html>
                <title>設置 [Kmoe]</title>
                <body>
                    搜索漫畫 Lv3 設置 ｜ 主頁 ｜ 閱讀器 ｜ 正版紙質書 ｜ 上傳漫畫
                    首頁 ＞ 我的設置 帳號信息 推送設置 提升等級 訂閱收藏 額度記錄
                    我的主頁： https://kxx.moe/u/10451555/ 訪問主頁
                </body>
            </html>
            """,
            "html.parser",
        )

        class Response:
            url = "https://koz.moe/my.php"

        self.assertTrue(_looks_like_logged_in_page(soup, Response()))
        self.assertEqual(_extract_user_level(soup), 3)
        self.assertEqual(_extract_profile_id(soup), "10451555")
        self.assertEqual(_fallback_nickname(soup, "__FROM_COOKIE__", 3), "用户10451555")


if __name__ == "__main__":
    unittest.main()
