import unittest

from bs4 import BeautifulSoup

from kmdr.module.authenticator.utils import _page_snippet


class TestAuthenticatorStatusParsing(unittest.TestCase):
    def test_page_snippet_uses_title_and_body(self):
        soup = BeautifulSoup("<html><title>登录</title><body>请先登录后继续操作</body></html>", "html.parser")

        self.assertIn("登录", _page_snippet(soup))
        self.assertIn("请先登录", _page_snippet(soup))


if __name__ == "__main__":
    unittest.main()
