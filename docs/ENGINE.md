# 网站引擎（web/）

`web/` 是 [AIHOT](https://github.com/KKKKhazix/AIHOT) 开源引擎（MIT 许可），用 `git subtree` 引入：
负责持续采集后的精选、事件归组、热度、日报周报月报、网站、RSS / API / MCP 和后台。
FinHOT 的 Python 部分（仓库根目录）作为 A 股数据服务：采集快讯推给引擎，并提供行情、涨停、资金、日历、回测。

## 同步上游

```bash
git subtree pull --prefix web https://github.com/KKKKhazix/aihot main --squash
```

合并后跑一遍 `web/` 里的检查（见 `web/AGENTS.md`），再对照下面的本地改动确认没有被覆盖。

## 本地改动清单

改了引擎本身（`web/apps/`、`web/packages/`）的地方都记在这里，便于合并上游、也便于以后给上游提 PR。
`web/site/`、`web/industry/` 是本站的配置与行业知识，属于正常定制，不在此列。

| 改动 | 文件 | 原因 |
|---|---|---|
| 推送接口 `/api/ingest/items` 支持可选的 `body` 字段，作为正文保存、不再抓原文页 | `packages/backend/src/ingest/items.ts`、`tests/ingest.test.ts`、`docs/sources.md` | 快讯没有独立的文章页，FinHOT 推送时已经有全文 |
| 构建参数 `APT_MIRROR` 换 Debian 源（`deploy/finhot.sh` 传腾讯云镜像；不传用默认源） | `Dockerfile` | 服务器在上海连 deb.debian.org 会卡死；写死腾讯镜像又会让海外的构建（GitHub Actions）失败 |
| 新增 `scripts/finhot-selection.ts`：评分分布与门槛建议、按新门槛补选（复用已付费的评分）、重编日报 | `scripts/finhot-selection.ts` | 没有标注样本时按“每天想要几件精选”定门槛；上游没有补选和重编日报的入口 |
| 新增 `scripts/finhot-doctor.ts`：从入库到日报逐层体检（分析、归组、精选、模型调用、预算、队列、日报取材），`--fix` 用后台“运行”页同样的操作重新排队、重新归组 | `scripts/finhot-doctor.ts` | worker 不为每次模型调用打日志，“精选 / 日报为空”只能从数据库看卡在哪一层 |
| 时间窗在网站第一篇文章之前的日报（部署当天那期）不再生成，也不判为缺失 | `packages/backend/src/reports/compose.ts`、`packages/backend/src/operations/alerts.ts`、`tests/report-recovery.test.ts`、`tests/daily-report-alerts.test.ts` | 上游把“窗口里没有打过分的文章”当作故障；新站第一天的窗口里本来就没有文章，这条告警永远消不掉 |
| 重编日报可以按发布时间归期（`composeDaily(date, reason, true)`），并跳过之后几期已收录的；平时的日报仍按进精选的时间取材 | `packages/backend/src/reports/edition.ts`、`packages/backend/src/reports/compose.ts`、`scripts/finhot-selection.ts`、`tests/report-recovery.test.ts` | 调门槛后补选的文章按上游规则只会进补选之后的那一期，重编过去几天的日报永远取不到它们 |
| 分析并发可以用 `ANALYZE_CONCURRENCY` 调（默认 6 不变） | `packages/backend/src/jobs/content.ts`、`.env.example` | 智谱接口在并发高时回 429，被拒的调用计入文章的重试次数，用完就放弃；并发低一点只是排队慢一点 |
| 没配 `JINA_API_KEY` 时不走 Jina 兜底，原文页读不到就直接按摘要分析 | `packages/backend/src/content/extract.ts`、`tests/finhot-extract.test.ts` | 国内服务器读不到海外原文页（CNBC、MarketWatch），上游会报“JINA_API_KEY is not configured”重试三次再按摘要分析，白等半小时 |
| 测试里的示例行业（AI 的分类、标签、公司、主题）换成财经的对应项 | `tests/*.test.ts`、`apps/web/tests/*.test.ts` | 上游要求改了 `industry/taxonomy.ts` 后同步换掉测试例子；合并上游时这些文件最容易冲突，冲突时保留上游的测试逻辑、换回财经的例子 |

本站自己的模块（`modules/`，不算引擎改动，合并上游不冲突）：`modules/market`（“盘面”页：涨停原因与信号战绩，
数据由 FinHOT 推送进程推到 `POST /api/finhot/market`，用 `INGEST_TOKEN`），登记在 `site/modules/` 三份清单、
`site/package.json` 和 `Dockerfile`。

财经定制本身（不算引擎改动）：`site/site.ts`（站名文案、“A股影响”）、`site/brand/nameplates/`（财经日报报头字）、`site/changelog.json`、
`industry/taxonomy.ts`（7 个分类、标签、公司）、`industry/topics.json`（公司 / 题材 / 政策与数据 三组主题）、`industry/sources.json`、
`industry/prompts/`（预筛、注意力评分、内容理解、写作规则）、`industry/selection.ts`（门槛 T1 60 / T1_5 62 / T2 64，
可用 `.env` 的 `SELECTION_T1`、`SELECTION_T1_5`、`SELECTION_T2` 覆盖；上游给 AI 媒体的 T2 76 让快讯一条都选不上）。

## 名称与许可

引擎代码按 MIT 许可使用，保留 `web/LICENSE` 与 `web/NOTICE`。按上游要求，站点不使用 AIHOT 的名字和 Logo。
