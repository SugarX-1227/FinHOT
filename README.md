# FinHOT — 财经热点日报（A股交易参考）

> 每天自动采集多家财经快讯与政策信源，跨源去重、按重要性排序、按板块归类，生成盘前/盘后可读的 Markdown 日报。
> 参考 [AIHOT](https://github.com/KKKKhazix/AIHOT) 的"信源 → 打分 → 聚簇 → 日报"思路，针对 A 股场景做了轻量化。

- **不配 LLM 也能跑**：分类、打分、排序都有规则兜底，保证每天稳定出报告。
- **配了 LLM 更好用**：对排名靠前的条目做中文摘要、利好/利空判断、A 股板块映射，并写一段"盘前要点"。
- **信源可插拔**：加源/停源只改 `sources/sources.yaml`；任何一个源挂了只影响它自己。

日报在 [`reports/`](reports/)，最新一期见 [`reports/latest.md`](reports/latest.md)。

## 信源

| 类别 | 信源 |
|---|---|
| 7x24 快讯 | 财联社电报、华尔街见闻、金十数据、东方财富、新浪财经、同花顺、证券时报、第一财经 |
| 官方政策 | 中国政府网·最新政策、美联储新闻稿 |
| 海外媒体 | CNBC、MarketWatch（英文 RSS） |
| 行情快照 | 美股三大指数、中概金龙、恒指、日经、A50 期指、美元指数、离岸人民币、美债、黄金、原油、铜 |

> 快讯接口是各网站网页端公开使用的 JSON 接口，网站改版可能导致某个源失效。
> 日报页脚的"信源状态"和 `python -m finhot sources` 可以随时看到哪个源坏了。

## 快速开始

```bash
pip install -r requirements.txt

python -m finhot sources            # 信源健康检查：每个源抓一页，看看是否正常
python -m finhot collect --hours 3  # 只采集，在终端打印去重排序后的前 30 条
python -m finhot run                # 完整流程：采集过去 24 小时 → reports/YYYY-MM-DD.md
python -m finhot run --no-llm       # 不调用 LLM
```

### 启用 LLM（可选）

任意 OpenAI 兼容接口均可，默认按 DeepSeek 配置：

```bash
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1   # 可选，默认即此
export LLM_MODEL=deepseek-chat                    # 可选
export LLM_MAX_ITEMS=80                           # 可选，最多分析多少条（预算熔断）
export LLM_MAX_CALLS=10                           # 可选，单次运行最多调用次数
```

### GitHub Actions 自动运行

`.github/workflows/daily.yml` 已配置：

- 北京时间 **07:30** 盘前版（过去 24 小时）
- 工作日 **15:35** 盘后版（过去 8 小时）
- 也可以在 Actions 页面手动触发（可指定小时数、是否用 LLM）

生成的日报和数据会自动 commit 回仓库。要启用 LLM，在仓库 **Settings → Secrets and variables → Actions** 中：
- Secrets 添加 `LLM_API_KEY`
- Variables 添加 `LLM_BASE_URL`、`LLM_MODEL`（不用 DeepSeek 时才需要）

> GitHub 的定时任务可能延迟几分钟到几十分钟，属正常现象。

## 处理流程

```
sources.yaml ─► 并发采集（按时间窗口自动翻页，单源失败隔离）
            ─► 跨源去重聚簇（同一事件多家报道合并，记录"等N家"）
            ─► 规则分类（政策/宏观/海外/市场/行业/公司）+ 重要性打分
            ─►（可选）LLM：摘要 / 利好利空 / 板块映射 / 盘前要点
            ─► 外盘行情快照
            ─► reports/YYYY-MM-DD.md  +  data/YYYY-MM-DD/items.jsonl、meta.json
```

打分规则见 `finhot/scoring.py`（关键词表可以直接改）；LLM 提示词在 `prompts/`。
`data/` 下保留每天的结构化条目（含分数、类别、LLM 映射），方便以后做"高分新闻 → 次日板块表现"的回测和校准。

## 目录

```
finhot/
  fetchers/     信源适配器（flash.py 快讯类，official.py 政策与 RSS）
  collect.py    并发采集
  dedup.py      去重聚簇
  scoring.py    规则分类与打分
  llm.py        可选 LLM 增强
  market.py     行情快照
  report.py     日报渲染
  pipeline.py   串起完整流程
sources/sources.yaml   信源与行情品种配置
prompts/               LLM 提示词
reports/               生成的日报
data/                  结构化数据（data/raw/ 不入库）
tests/                 离线单元测试（pytest）
docs/DESIGN.md         设计文档
```

## 新增一个信源

- **RSS**：在 `sources/sources.yaml` 加一段 `type: rss` 的配置即可。
- **其他接口**：在 `finhot/fetchers/` 写一个带 `@register("xxx")` 的函数，按页 `yield` `NewsItem` 列表（新→旧），然后在 yaml 里配置 `type: xxx`。可参考 `flash.py` 中现成的适配器。

## Roadmap

- [x] 多源采集 + 翻页 + 单源隔离
- [x] 跨源去重、规则分类打分
- [x] LLM 摘要 / 板块映射 / 盘前要点（可选）
- [x] GitHub Actions 定时生成并提交日报
- [ ] 周报、热度统计（高频板块/关键词）
- [ ] 接入涨停池/龙虎榜/北向资金，做"新闻 × 资金"交叉验证
- [ ] 经济数据日历、新股申购、限售解禁等"今日关注"
- [ ] 可选：做成网站

## 免责声明

本项目仅为个人信息整理工具，所有内容来自公开新闻并由程序/AI 自动生成，不构成任何投资建议。
