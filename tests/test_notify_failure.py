import os
import unittest
from unittest.mock import patch

from src.notify_failure import _github_run_url, build_failure_card, main


class NotifyFailureTests(unittest.TestCase):
    def test_github_run_url_from_actions_env(self):
        env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "12345",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_github_run_url(), "https://github.com/owner/repo/actions/runs/12345")

    def test_failure_card_contains_reason_and_run_link(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "12345", "GITHUB_WORKFLOW": "Morning Briefing"}, clear=True):
            card = build_failure_card("晨报", "LLM timeout", "https://example.com/run")

        content = "\n".join(e.get("content", "") for e in card["elements"])
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("晨报失败告警", card["header"]["title"]["content"])
        self.assertIn("LLM timeout", content)
        self.assertIn("https://example.com/run", content)
        self.assertIn("Morning Briefing", content)

    def test_main_skips_when_feishu_config_missing(self):
        with patch.dict(os.environ, {}, clear=True), patch("src.notify_failure.send_feishu_card") as send:
            self.assertEqual(main(), 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
