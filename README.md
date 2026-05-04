# Daily Briefing 📰

一个个性化的日报推送系统。每天自动抓取资讯、AI 生成知识卡片，通过飞书推送。

基于 GitHub Actions 运行，零成本、零运维。Fork 后修改配置即可使用。

## 功能

### 早报（每日 7:20，北京时间）
- **全行业资讯 ×5** — RSS 源 + NewsNow 多平台热榜 + 知乎热榜，GPT 智能选稿
- **兴趣领域资讯 ×5** — 按你配置的关键词筛选（如 AI、企业招聘）
- **播客推荐** — 小宇宙最新单集 + 收听链接
- **天气** — 和风天气 API
- **每日一句** — 名人名言 / 网络热梗

### 午报（每日 12:00）
- **AI 技巧** — GPT 生成可直接尝试的实用技巧 + 延伸阅读链接
- **心理学/经济学** — 每日一个知识卡片 + 当天可用的小动作
- **品牌洞察** — 商业策略分析 + 可迁移方法论

## 快速开始

### 1. Fork 本仓库

### 2. 配置 GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 | 获取方式 |
|--------|------|---------|
| `OPENAI_API_KEY` | OpenAI API Key | https://platform.openai.com/api-keys |
| `FEISHU_APP_ID` | 飞书应用 App ID | 飞书开放平台应用 |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret | 飞书开放平台应用 |
| `FEISHU_RECEIVE_ID` | 接收人的 open_id / chat_id | 可用 `tools/get_open_id.py` 获取 |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址（可选） | 自建/中转接口时填写 |
| `QWEATHER_API_KEY` | 和风天气 API Key | https://dev.qweather.com （免费） |

### 3. 修改配置

编辑 `config/config.yaml`：

```yaml
user:
  city: "你的城市"

interests:
  - name: "你的兴趣1"
    keywords: ["关键词1", "关键词2"]
  - name: "你的兴趣2"
    keywords: ["关键词3", "关键词4"]
```

编辑 `config/rss_sources.yaml` 添加/删除 RSS 源。

如需调整热榜平台，编辑 `config/config.yaml` 的 `hotlists.sources`。当前借鉴 TrendRadar 的 NewsNow 聚合思路，默认启用今日头条、百度、微博、澎湃、华尔街见闻、财联社、凤凰网、抖音、B站热搜；没有引入 TrendRadar 的数据库、MCP、多渠道推送等重型模块。

早报会自动做跨平台同话题合并、来源质量评分，并上传候选审计 artifact，方便复盘“抓了什么、选了什么、为什么选”。

午报保留原有 AI 技巧、心理学/经济学、品牌洞察、GitHub 热门项目四块，但提示词会强制输出标题、正文和“试一下”；GitHub 热门项目会先做候选质量排序，再生成“看点/可用”点评。

### 4. 手动测试

在 GitHub Actions 页面，手动触发 `Morning Briefing` 或 `Afternoon Briefing` 工作流。

### 5. 自动运行

配置完成后，GitHub Actions 每天自动运行：
- 早报：北京时间 7:20
- 午报：北京时间 12:00

## 项目结构

```
daily-briefing/
├── config/
│   ├── config.yaml          # 主配置
│   ├── rss_sources.yaml     # RSS 源清单
│   └── quotes.json          # 名言库
├── src/
│   ├── fetchers/            # 数据获取
│   │   ├── rss_fetcher.py
│   │   ├── newsnow_fetcher.py
│   │   ├── zhihu_fetcher.py
│   │   ├── weather_fetcher.py
│   │   ├── quote_fetcher.py
│   │   └── podcast_fetcher.py
│   ├── processors/          # 数据处理
│   │   ├── llm_selector.py  # GPT 选稿
│   │   └── llm_generator.py # GPT 生成内容
│   ├── publishers/          # 推送
│   │   └── feishu.py
│   ├── utils/
│   │   ├── dedup.py         # 去重
│   │   ├── source_quality.py # 来源质量评分
│   │   ├── topic_cluster.py  # 同话题聚类
│   │   └── logger.py
│   ├── morning.py           # 早报入口
│   └── afternoon.py         # 午报入口
├── .github/workflows/       # GitHub Actions
├── data/                    # 运行时数据
└── docs/                    # 设计文档
```

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 创建 .env 文件
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 运行早报
python src/morning.py

# 运行午报
python src/afternoon.py
```

## 成本

| 项目 | 费用 |
|------|------|
| GitHub Actions | 免费 |
| OpenAI 兼容模型 | 取决于所用模型 |
| 和风天气 API | 免费 |
| 飞书应用 | 免费 |
| **总计** | **~$0.3/月** |

## 技术栈

- **Python 3.11+**
- **OpenAI 兼容模型** — 选稿 + 内容生成
- **feedparser** — RSS 解析
- **NewsNow-compatible API** — 多平台热榜聚合
- **GitHub Actions** — 定时任务
- **飞书应用 / Webhook** — 消息推送

## License

MIT
