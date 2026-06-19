import unittest
from unittest.mock import Mock, patch

from src.publishers.telegram import (
    _sanitize_error,
    render_card_as_telegram_html,
    render_card_as_telegram_text,
    send_telegram_brief,
    send_telegram_text,
    split_telegram_text,
)


class TelegramPublisherTests(unittest.TestCase):
    def test_render_card_uses_plain_text_links(self):
        card = {
            "header": {"title": {"content": "☀️ 晨报 · 2026年06月18日"}},
            "elements": [
                {"tag": "markdown", "content": "**头条**\n1. [重要新闻](https://example.com/news)\n- 判断：值得看"},
                {"tag": "hr"},
            ],
        }

        text = render_card_as_telegram_text(card)

        self.assertIn("☀️ 晨报", text)
        self.assertIn("重要新闻\nhttps://example.com/news", text)
        self.assertNotIn("[重要新闻](", text)
        self.assertNotIn("**", text)

    def test_render_card_html_hides_raw_urls_behind_titles(self):
        card = {
            "header": {"title": {"content": "☀️ 晨报 · 2026年06月18日"}},
            "elements": [
                {"tag": "markdown", "content": "**━━ AI 科技 ━━**\n1. [重要新闻](https://example.com/news)\n- 判断：值得看"},
            ],
        }

        html = render_card_as_telegram_html(card)

        self.assertIn("<b>☀️ 晨报", html)
        self.assertIn('<a href="https://example.com/news">重要新闻</a>', html)
        self.assertIn("🤖 AI 科技", html)
        self.assertNotIn("[重要新闻](", html)
        self.assertNotIn("https://example.com/news\n", html)

    def test_send_brief_uses_telegram_html_mode(self):
        card = {
            "header": {"title": {"content": "☀️ 晨报"}},
            "elements": [{"tag": "markdown", "content": "1. [新闻](https://example.com/news)"}],
        }
        response = Mock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None

        with patch("src.publishers.telegram.requests.post", return_value=response) as post:
            ok = send_telegram_brief(card, {"bot_token": "token", "chat_id": "123"})

        self.assertTrue(ok)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertIn('<a href="https://example.com/news">新闻</a>', payload["text"])

    def test_split_keeps_chunks_under_limit(self):
        text = "\n\n".join(["段落" + ("x" * 120) for _ in range(20)])
        chunks = split_telegram_text(text, max_chars=500)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))

    def test_split_breaks_single_long_line(self):
        chunks = split_telegram_text("x" * 1200, max_chars=500)

        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))

    def test_send_posts_plain_messages(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None

        with patch("src.publishers.telegram.requests.post", return_value=response) as post:
            ok = send_telegram_text("hello", {"bot_token": "token", "chat_id": "123"})

        self.assertTrue(ok)
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "123")
        self.assertEqual(payload["text"], "hello")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertNotIn("parse_mode", payload)

    def test_send_uses_optional_proxy(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None

        with patch("src.publishers.telegram.requests.post", return_value=response) as post:
            ok = send_telegram_text(
                "hello",
                {"bot_token": "token", "chat_id": "123", "proxy": "http://127.0.0.1:7897"},
            )

        self.assertTrue(ok)
        self.assertEqual(
            post.call_args.kwargs["proxies"],
            {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"},
        )

    def test_sanitize_error_redacts_bot_token(self):
        error = RuntimeError("https://api.telegram.org/botsecret-token/sendMessage failed")

        sanitized = _sanitize_error(error, "secret-token")

        self.assertIn("<redacted>", sanitized)
        self.assertNotIn("secret-token", sanitized)


if __name__ == "__main__":
    unittest.main()
