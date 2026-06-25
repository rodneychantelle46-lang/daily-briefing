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
             patch("src.utils.llm_client.time.sleep") as sleep, \
             patch.dict("os.environ", {}, clear=True):
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
             patch("src.utils.llm_client.time.sleep") as sleep, \
             patch.dict("os.environ", {}, clear=True):
            result = chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                api_key="key",
                base_url="https://api.example.com/v1",
                max_retries=1,
            )

        self.assertEqual(result, "done")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()

    def test_codex_failure_falls_back_to_openai(self):
        codex_failure = FakeResponse(status_code=503, text='{"error":"auth_unavailable"}')
        openai_success = FakeResponse(data={"choices": [{"message": {"content": "fallback ok"}}]})
        env = {
            "CODEX_API_KEY": "codex-key",
            "CODEX_BASE_URL": "https://codex.example.com/v1",
            "CODEX_MODEL": "codex-model",
            "OPENAI_API_KEY": "openai-key",
            "OPENAI_BASE_URL": "https://openai.example.com/v1",
            "OPENAI_MODEL": "openai-model",
        }
        with patch("src.utils.llm_client.requests.post", side_effect=[codex_failure, openai_success]) as post, \
             patch("src.utils.llm_client.time.sleep") as sleep, \
             patch.dict("os.environ", env, clear=True):
            result = chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                max_retries=0,
            )

        self.assertEqual(result, "fallback ok")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].args[0], "https://codex.example.com/v1/chat/completions")
        self.assertEqual(post.call_args_list[0].kwargs["json"]["model"], "codex-model")
        self.assertEqual(post.call_args_list[1].args[0], "https://openai.example.com/v1/chat/completions")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "openai-model")
        sleep.assert_not_called()

    def test_env_key_passed_as_explicit_is_not_retried_against_default_openai_url(self):
        codex_failure = FakeResponse(status_code=503, text='{"error":"auth_unavailable"}')
        env = {
            "CODEX_API_KEY": "codex-key",
            "CODEX_BASE_URL": "https://codex.example.com/v1",
            "CODEX_MODEL": "codex-model",
        }
        with patch("src.utils.llm_client.requests.post", return_value=codex_failure) as post, \
             patch("src.utils.llm_client.time.sleep") as sleep, \
             patch.dict("os.environ", env, clear=True):
            with self.assertRaises(requests.HTTPError):
                chat_completion(
                    messages=[{"role": "user", "content": "hi"}],
                    api_key="codex-key",
                    max_retries=0,
                )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://codex.example.com/v1/chat/completions")
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
