import os
import unittest
from unittest.mock import patch

from src.morning import _is_llm_degraded
from src.processors import llm_selector
from src.processors.llm_selector import select_articles
from src.publishers.feishu import build_morning_card


class MorningDegradedTests(unittest.TestCase):
    def test_no_key_uses_source_balanced_degraded_candidates(self):
        topics = {
            "今日头条": "南京审计大学通报",
            "量子位": "OpenClaw生态安全报告",
            "微博热搜": "演唱会官宣新场次",
            "百度热搜": "新能源汽车销量榜",
            "IT之家": "手机系统更新推送",
        }
        articles = []
        for source, topic in topics.items():
            for rank in range(1, 4):
                articles.append({
                    "title": f"{topic} {rank}",
                    "url": f"https://example.com/{source}/{rank}",
                    "source": source,
                    "rank": rank,
                    "source_type": "hotlist",
                })

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            selected = select_articles(articles, category="全行业", count=5, api_key="")

        self.assertEqual(len(selected), 5)
        self.assertGreaterEqual(len({a["source"] for a in selected}), 5)
        self.assertTrue(all(a.get("selection_status") == "degraded" for a in selected))
        self.assertTrue(all("模型不可用" not in a.get("takeaway", "") for a in selected))
        self.assertTrue(all(_is_llm_degraded(a) for a in selected))

    def test_card_shows_global_degraded_warning_once(self):
        card = build_morning_card(
            general_news=[{"title": "A", "url": "https://example.com/a", "source": "今日头条"}],
            interest_news={},
            date_str="2026年05月13日",
            llm_degraded=True,
        )
        content = "\n".join(e.get("content", "") for e in card["elements"])
        self.assertIn("⚠️ 选稿模型异常", content)
        self.assertEqual(content.count("选稿模型异常"), 1)

    def test_select_articles_hydrates_by_index_without_urls_in_prompt(self):
        articles = []
        for i in range(1, 4):
            articles.append({
                "title": f"标题{i}",
                "url": f"https://example.com/{i}",
                "source": f"来源{i}",
                "rank": i,
                "source_type": "hotlist",
            })

        captured = {}

        def fake_chat_completion(*, messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return "[{\"index\": 2, \"reason\": \"优先看第二条\", \"takeaway\": \"先看这条\"}]"

        with patch.object(llm_selector, "cluster_related_articles", side_effect=lambda items: items), \
             patch.object(llm_selector, "chat_completion", side_effect=fake_chat_completion):
            selected = select_articles(articles, category="全行业", count=1, api_key="key", base_url="https://api.example.com")

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["title"], "标题2")
        self.assertEqual(selected[0]["url"], "https://example.com/2")
        self.assertIn("index", captured["prompt"])
        self.assertNotIn("https://example.com/2", captured["prompt"])
        self.assertNotIn("https://example.com/1", captured["prompt"])

    def test_prepare_candidates_respects_cap(self):
        articles = []
        for i in range(1, 101):
            articles.append({
                "title": f"标题{i}",
                "url": f"https://example.com/{i}",
                "source": f"来源{i % 10}",
                "rank": i,
                "source_type": "hotlist",
            })

        candidates = llm_selector._prepare_candidates(articles, limit=45, per_source_first_pass=4)
        self.assertLessEqual(len(candidates), 45)


if __name__ == "__main__":
    unittest.main()
