import unittest
from unittest.mock import patch

from src.utils import web_searcher


class FakeResponse:
    def __init__(self, text="", data=None, status_code=200):
        self.text = text
        self._data = data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._data


class WebSearcherRelevanceTests(unittest.TestCase):
    def test_network_ip_query_is_domain_specific(self):
        query = web_searcher.construct_related_search_query("IP", domain="cs_ai_learning", platform="zhihu")
        self.assertIn("计算机网络", query)
        self.assertIn("IP地址", query)
        self.assertIn("TCP/IP", query)
        self.assertIn("-知识产权", query)

    def test_network_ip_rejects_intellectual_property_result(self):
        self.assertFalse(web_searcher.is_relevant_search_result("IP", "知识产权 IP 授权与商标保护", "cs_ai_learning"))
        self.assertTrue(web_searcher.is_relevant_search_result("IP", "IP地址和TCP/IP协议入门", "cs_ai_learning"))

    def test_zhihu_search_filters_irrelevant_ip_results(self):
        html = """
        <html><body>
          <h2><a href="https://www.zhihu.com/question/1">知识产权 IP 授权怎么做</a></h2>
          <h2><a href="https://www.zhihu.com/question/2">IP地址和TCP/IP协议是什么</a></h2>
        </body></html>
        """
        with patch.object(web_searcher.requests, "get", return_value=FakeResponse(text=html)) as get:
            results = web_searcher.search_zhihu("IP", count=1, domain="cs_ai_learning")

        self.assertEqual(len(results), 1)
        self.assertIn("TCP/IP", results[0]["title"])
        self.assertIn("计算机网络", get.call_args.kwargs["params"]["q"])

    def test_bilibili_search_filters_irrelevant_ip_results(self):
        data = {
            "data": {
                "result": [
                    {"bvid": "BV1", "title": '<em class="keyword">IP</em> 知识产权和商标保护'},
                    {"bvid": "BV2", "title": '计算机网络 <em class="keyword">IP</em> 地址详解'},
                ]
            }
        }
        with patch.object(web_searcher.requests, "get", return_value=FakeResponse(data=data)):
            results = web_searcher.search_bilibili("IP", count=1, domain="cs_ai_learning")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.bilibili.com/video/BV2")
        self.assertIn("计算机网络", results[0]["title"])


if __name__ == "__main__":
    unittest.main()
