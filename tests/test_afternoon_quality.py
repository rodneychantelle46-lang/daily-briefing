import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.afternoon as afternoon


OK_TIP = {
    "title": "好内容",
    "content": "这是一段有效内容，足够长，且不是 fallback。",
    "try_this": "试一下",
    "topic": "Token",
    "links": [{"title": "相关阅读", "url": "https://example.com/read"}],
    "generation_status": "ok",
    "generation_error": "",
}

DEGRADED_TIP = {
    "title": "心理学/经济学技巧",
    "content": "今日心理学/经济学技巧生成失败，先跳过这块，不硬凑废话。",
    "try_this": "等下一次自动生成。",
    "topic": "生成失败",
    "links": [],
    "generation_status": "degraded",
    "generation_error": "missing_api_key",
}

OK_REPO = {
    "name": "owner/repo",
    "url": "https://github.com/owner/repo",
    "summary": "这是一个有效项目摘要，说明项目用途和价值。",
    "why": "技术方向清晰",
    "use_case": "可以借鉴工程结构",
    "deep_read_status": "ok",
    "generation_status": "ok",
    "generation_error": "",
}


class AfternoonQualityTests(unittest.TestCase):
    def test_degraded_tip_blocks_send_by_default(self):
        config = {
            "llm": {"model": "gpt-5.5", "api_key": "key", "base_url": ""},
            "publisher": {"telegram": {}},
        }
        with patch.object(afternoon, "load_dotenv"), \
             patch.object(afternoon, "load_config", return_value=config), \
             patch.object(afternoon, "already_sent", return_value=False), \
             patch.object(afternoon, "generate_tip", side_effect=[OK_TIP, DEGRADED_TIP, OK_TIP]), \
             patch.object(afternoon, "fetch_trending_repos", return_value=[OK_REPO]), \
             patch.object(afternoon, "summarize_github_repos", return_value=[OK_REPO]), \
             patch.object(afternoon, "write_afternoon_artifact") as artifact, \
             patch.object(afternoon, "send_telegram_brief") as send:
            with self.assertRaises(SystemExit) as cm:
                afternoon.main()

        self.assertEqual(cm.exception.code, 2)
        send.assert_not_called()
        artifact.assert_called_once()
        self.assertTrue(artifact.call_args.kwargs["send_blocked"])
        self.assertIn("降级", artifact.call_args.kwargs["aborted_reason"])

    def test_quality_summary_counts_degraded_and_links(self):
        card = {
            "header": {"title": {"content": "📬 午报 · 2026年05月13日"}},
            "elements": [
                {"tag": "markdown", "content": "正文"},
                {"tag": "hr"},
            ],
        }
        degraded_repo = {
            "name": "GitHub Trending",
            "url": "",
            "generation_status": "degraded",
            "generation_error": "github_summary_failed",
            "deep_read_status": "degraded",
        }

        summary = afternoon.build_afternoon_quality_summary(
            card,
            [OK_TIP, DEGRADED_TIP],
            [OK_REPO, degraded_repo],
            send_blocked=True,
            aborted_reason="blocked",
        )

        self.assertEqual(summary["tip_count"], 2)
        self.assertEqual(summary["tip_degraded_count"], 1)
        self.assertEqual(summary["github_repo_count"], 2)
        self.assertEqual(summary["github_degraded_count"], 1)
        self.assertEqual(summary["fallback_degraded_count"], 2)
        self.assertEqual(summary["tip_link_count"], 1)
        self.assertEqual(summary["github_link_count"], 1)
        self.assertTrue(summary["send_blocked"])
        self.assertEqual(summary["aborted_reason"], "blocked")
        self.assertEqual(summary["card"]["markdown_blocks"], 1)
        self.assertIn("missing_api_key", summary["generation_errors"])

    def test_write_afternoon_artifact_includes_quality_summary(self):
        card = {
            "header": {"title": {"content": "📬 午报 · 2026年05月13日"}},
            "elements": [{"tag": "markdown", "content": "正文"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(afternoon, "PROJECT_ROOT", Path(tmpdir)):
                path = afternoon.write_afternoon_artifact(
                    card,
                    [DEGRADED_TIP],
                    [OK_REPO],
                    "2026年05月13日",
                    send_blocked=True,
                    aborted_reason="blocked",
                )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("quality_summary", payload)
        self.assertTrue(payload["quality_summary"]["send_blocked"])
        self.assertEqual(payload["quality_summary"]["tip_degraded_count"], 1)

    def test_dry_run_skips_telegram_and_sent_marker(self):
        config = {
            "llm": {"model": "gpt-5.5", "api_key": "key", "base_url": ""},
            "publisher": {"telegram": {}},
        }
        with patch.dict(os.environ, {"DAILY_BRIEFING_DRY_RUN": "1"}, clear=False), \
             patch.object(afternoon, "load_dotenv"), \
             patch.object(afternoon, "load_config", return_value=config), \
             patch.object(afternoon, "already_sent", return_value=False), \
             patch.object(afternoon, "generate_tip", side_effect=[OK_TIP, OK_TIP, OK_TIP]), \
             patch.object(afternoon, "fetch_trending_repos", return_value=[OK_REPO]), \
             patch.object(afternoon, "summarize_github_repos", return_value=[OK_REPO]), \
             patch.object(afternoon, "write_afternoon_artifact") as artifact, \
             patch.object(afternoon, "send_telegram_brief") as send, \
             patch.object(afternoon, "mark_sent") as mark_sent:
            afternoon.main()

        send.assert_not_called()
        mark_sent.assert_not_called()
        artifact.assert_called_once()
        self.assertTrue(artifact.call_args.kwargs["dry_run"])

    def test_env_can_explicitly_allow_degraded_afternoon(self):
        with patch.dict(os.environ, {"ALLOW_DEGRADED_AFTERNOON": "true"}):
            self.assertTrue(afternoon._allow_degraded_afternoon({"llm": {}}))
        with patch.dict(os.environ, {"ALLOW_DEGRADED_AFTERNOON": "false"}):
            self.assertFalse(afternoon._allow_degraded_afternoon({"llm": {"allow_degraded_afternoon": True}}))


if __name__ == "__main__":
    unittest.main()
