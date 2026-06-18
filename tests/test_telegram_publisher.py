import unittest
from unittest.mock import Mock, patch

from src.publishers.telegram import (
    render_card_as_telegram_text,
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


if __name__ == "__main__":
    unittest.main()
