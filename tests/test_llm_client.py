import unittest
from unittest.mock import patch

import requests

from src.utils.llm_client import chat_completion


class FakeResponse:
    def __init__(self, status_code=200, text="", data=None, url="https://api.example.com/v1/chat/completions"):
        self.status_code = status_code
        self.text = text
        self._data = data or {"choices": [{"message": {"content": "ok"}}]}
        self.url = url

    def json(self):
        return self._data


class LlmClientTests(unittest.TestCase):
    def test_http_400_fails_fast_and_includes_body(self):
        response = FakeResponse(status_code=400, text='{"error":"prompt too long"}')
        with patch("src.utils.llm_client.requests.post", return_value=response) as post, \
             patch("src.utils.llm_client.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError) as cm:
                chat_completion(
                    messages=[{"role": "user", "content": "hi"}],
                    api_key="key",
                    base_url="https://api.example.com/v1",
                    max_retries=2,
                )

        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("400 Client Error", str(cm.exception))
        self.assertIn("prompt too long", str(cm.exception))

    def test_timeout_retries_then_succeeds(self):
        response = FakeResponse(data={"choices": [{"message": {"content": "done"}}]})
        with patch("src.utils.llm_client.requests.post", side_effect=[requests.Timeout("slow"), response]) as post, \
             patch("src.utils.llm_client.time.sleep") as sleep:
            result = chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                api_key="key",
                base_url="https://api.example.com/v1",
                max_retries=1,
            )

        self.assertEqual(result, "done")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
