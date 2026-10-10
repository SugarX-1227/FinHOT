# FinHOT — 财经热点日报（A股交易参考）

> 每天自动采集多家财经快讯与政策信源，跨源去重、按重要性排序、按板块归类，
> 结合 A 股盘面与资金数据做交叉验证，生成盘前/盘后日报、周报，并发布成网站。
> 参考 [AIHOT](https://github.com/KKKKhazix/AIHOT) 的"信源 → 打分 → 聚簇 → 日报"思路，针对 A 股场景做了轻量化。

- **不配 LLM 也能跑**：分类、打分、排序、题材归类都有规则兜底，保证每天稳定出报告。
- **配了 LLM 更好用**：对排名靠前的条目做中文摘要、利好/利空判断、A 股板块映射，并写"要点速览"和周报回顾。
- **新闻 × 资金**：把新闻题材热度和板块涨幅、主力净流入、涨停家数放在一起，标出"共振 / 资金先行 / 新闻热·资金冷"。
- **信源可插拔**：加源/停源只改 `sources/sources.yaml`；任何一个源或数据项挂了只影响它自己。

日报在 [`reports/`](reports/)（最新一期 [`reports/latest.md`](reports/latest.md)），周报在 [`reports/weekly/`](reports/weekly/)。
开启 GitHub Pages 后可在 `https://<用户名>.github.io/FinHOT/` 浏览网站（见下文）。

## 日报内容

| 栏目 | 说明 |
|---|---|
| 要点速览 | LLM 根据新闻、外盘、A 股盘面、新闻×资金、日历写 3~6 条要点（需配置 LLM） |
| 今日必读 | 全部信源去重后得分最高的 10 条 |
| 今日关注 / 明日关注 | 重要经济数据（预期/前值）、重要事件、新股申购与上市、未来 7 天大额解禁；盘后版看明天 |
| 外盘与大宗 | 美股、中概、恒指、日经、A50、美元、离岸人民币、美债、黄金、原油、铜 |
| A股盘面 | 指数与成交额、涨跌家数、涨停/跌停/炸板与封板率、连板梯队、涨停集中行业、领涨行业/概念、主力净流入/流出、龙虎榜、南向资金 |
| 新闻 × 资金 | 每个题材：新闻条数、板块中位涨幅、主力净流入、涨停数 → 共振 / 资金先行 / 新闻热·资金冷 |
| 分类新闻 | 宏观与政策、海外、行业、公司、市场与资金 |

> 盘前版里的 A 股数据是上一交易日收盘数据。北向资金自 2024 年 8 月起不再披露实时净买入，这里只给成交额。
> 东财板块接口在海外服务器（GitHub Actions）上经常 502，此时自动改用新浪板块数据——涨幅与领涨股照常，
> 但没有主力资金数据，"新闻 × 资金"只按涨幅和涨停判断。本地（国内网络）运行一般能拿到完整的东财数据。

周报（每周六自动生成）：本周指数表现、十大新闻、题材热度榜（较上周变化 + 每日走势）、热门公司、新闻×资金周度回顾、每日分类分布、下周关注。

## 信源

| 类别 | 信源 |
|---|---|
| 7x24 快讯 | 财联社电报、华尔街见闻、金十数据、东方财富、新浪财经、同花顺、证券时报、第一财经 |
| 官方政策 | 中国政府网·最新政策、美联储新闻稿、美联储讲话 |
| 海外媒体 | CNBC、MarketWatch（英文 RSS） |
| 行情快照 | 美股三大指数、中概金龙、恒指、日经、A50 期指、美元指数、离岸人民币、美债、黄金、原油、铜 |
| A 股盘面 | 东方财富：指数、涨跌分布、涨停/跌停/炸板池、行业与概念板块资金、龙虎榜、沪深港通 |
| 日历 | 华尔街见闻财经日历（经济数据 + 事件）、东方财富新股与解禁 |

> 快讯接口是各网站网页端公开使用的 JSON 接口，网站改版可能导致某个源失效。
> 日报页脚的"信源状态"和 `python -m finhot sources` 可以随时看到哪个源坏了。

## 快速开始

```bash
pip install -r requirements.txt

python -m finhot sources            # 信源健康检查：每个源抓一页，看看是否正常
python -m finhot collect --hours 3  # 只采集，在终端打印去重排序后的前 30 条
python -m finhot market             # 打印 A 股盘面快照与今日关注
python -m finhot run                # 生成日报（窗口：上一份日报截止 → 现在，6~72 小时；无历史则 24 小时）
python -m finhot run --hours 12 --edition close --no-llm   # 指定窗口、版次（pre/noon/close）、不用 LLM
python -m finhot weekly             # 生成本周周报 → reports/weekly/YYYY-Www.md
python -m finhot site               # 生成网站到 _site/，用浏览器打开 _site/index.html 预览
python -m finhot auto               # 定时任务入口：按当前时段生成该出的日报/周报，已生成则跳过
```

`run --no-market` 可跳过 A 股盘面与日历（离线调试用）。

### 启用 LLM（可选）

任意 OpenAI 兼容接口均可，默认按 DeepSeek 配置：

```bash
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1   # 可选，默认即此
export LLM_MODEL=deepseek-chat                    # 可选
export LLM_MAX_ITEMS=80                           # 可选，最多分析多少条（预算熔断）
export LLM_MAX_CALLS=18                           # 可选，单次运行最多调用次数
export LLM_TIMEOUT=300                            # 可选，单次请求超时秒数
export LLM_TRUST_ENV=0                            # 可选，忽略系统代理（见下）
export LLM_BODY_EXTRA='{"thinking":{"type":"low"}}'  # 可选，供应商特有参数
```

智谱 GLM 配置示例（本机挂了代理软件时务必加 `LLM_TRUST_ENV=0`，否则国内 API 走代理会超时）：

```bash
export LLM_API_KEY=xxx
# 重要：Token Plan / GLM 编码套餐的额度只在 Coding 端点生效；
# 普通端点 /api/paas/v4 走按量付费余额，用套餐 key 会报 1113 余额不足
export LLM_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
export LLM_MODEL=glm-5.3-flash
export LLM_BODY_EXTRA='{"thinking":{"type":"low"}}'        # GLM-5 系列始终思考，可调 low/high/max
export LLM_TRUST_ENV=0
```

### GitHub Actions 自动运行

GitHub 的定时任务实测经常延迟 3~7 小时（原来 07:30 的任务 10~11 点才跑），所以 `.github/workflows/daily.yml`
在每个时段放了多个触发点，由 `python -m finhot auto` 判断该出哪一版、已出过就跳过：

| 版次 | 触发时间（北京时间） | 文件 |
|---|---|---|
| 盘前版 | 每天 05:17 / 06:17 / 07:17 / 08:17（03:00~12:00 之间任一次成功即可） | `reports/YYYY-MM-DD.md` |
| 盘后版 | 工作日 15:47 / 16:47 / 17:47（15:00 之后任一次成功即可） | `reports/YYYY-MM-DD-close.md` |
| 周报 | 周六盘前那次顺带生成；周六/周日 09:23 兜底 | `reports/weekly/YYYY-Www.md` |

每份日报的采集窗口接着上一份日报的截止时间，周末/节假日后的第一份会自动覆盖整个休市期间（上限 72 小时）。
也可以在 Actions 页面手动触发 `daily-report`：选择 auto / pre / noon / close / weekly，可指定小时数、是否用 LLM。

生成的日报和数据会自动 commit 回仓库。要启用 LLM，在仓库 **Settings → Secrets and variables → Actions** 中：
- Secrets 添加 `LLM_API_KEY`
- Variables 添加 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_BODY_EXTRA`（不用 DeepSeek 时才需要）

### 网站（GitHub Pages）

1. 仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**（只需设置一次）；
2. 之后每次生成新日报都会自动发布；也可以在 Actions 页面手动运行 `site`。

网站包含：首页（最新一期 + 近期列表）、往期日报（按月日历）、周报、RSS 订阅（`feed.xml`），手机和深色模式均可阅读，
涨跌按 A 股习惯红涨绿跌。未开启 Pages 时 `site` 工作流只给出提示，不会报错。

## 处理流程

```
sources.yaml ─► 并发采集（按时间窗口自动翻页，单源失败隔离）      ┐ 同时进行：外盘行情、A 股盘面、
            ─► 跨源去重聚簇（同一事件多家报道合并，记录"等N家"） │ 今日/明日关注（日历、新股、解禁）
            ─► 规则分类（政策/宏观/海外/市场/行业/公司）+ 重要性打分 ┘
            ─►（可选）LLM：摘要 / 利好利空 / 板块映射
            ─► 新闻 × 资金：题材词典把新闻和板块对上号，结合涨幅/资金/涨停给出判断
            ─►（可选）LLM：要点速览
            ─► reports/YYYY-MM-DD*.md  +  data/YYYY-MM-DD/items*.jsonl、meta*.json
                 └─► 周报（汇总一周 data/）  └─► 网站（渲染 reports/）
```

- 打分规则见 `finhot/scoring.py`，题材词典见 `finhot/themes.py`（关键词表都可以直接改）；LLM 提示词在 `prompts/`。
- `data/` 下保留每期的结构化条目（含分数、类别、LLM 映射）以及盘面、日历、新闻×资金结果，
  供周报统计，也方便以后做"高分新闻 → 次日板块表现"的回测和校准。

## 目录

```
finhot/
  fetchers/     信源适配器（flash.py 快讯类，official.py 政策与 RSS）
  collect.py    并发采集
  dedup.py      去重聚簇
  scoring.py    规则分类与打分
  themes.py     A 股题材词典（新闻/板块 → 题材）
  llm.py        可选 LLM 增强
  market.py     外盘行情快照
  ashare.py     A 股盘面：指数、涨停池、连板、板块资金、龙虎榜、沪深港通
  crosscheck.py 新闻 × 资金 交叉验证
  calendar_watch.py  今日关注：经济数据、事件、新股、解禁
  editions.py   版次与调度（多触发点去重、采集窗口）
  report.py     日报渲染
  pipeline.py   串起日报完整流程
  weekly.py     周报
  site.py       静态网站
sources/sources.yaml   信源与行情品种配置
prompts/               LLM 提示词
reports/               生成的日报（weekly/ 为周报）
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
- [x] 周报、热度统计（题材热度榜、热门公司、每日分类分布）
- [x] 接入涨停池/龙虎榜/板块资金/沪深港通，做"新闻 × 资金"交叉验证
- [x] 经济数据日历、重要事件、新股申购/上市、限售解禁等"今日关注"
- [x] 做成网站（GitHub Pages + RSS）
- [ ] 回测：高分新闻/共振题材 → 次日、5 日板块表现，用结果反过来校准打分与题材词典
- [ ] 题材词典自动扩充（用 LLM 从板块成分与新闻中归纳新题材）

## 免责声明

本项目仅为个人信息整理工具，所有内容来自公开新闻并由程序/AI 自动生成，不构成任何投资建议。
