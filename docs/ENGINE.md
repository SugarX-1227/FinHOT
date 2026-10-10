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

## 名称与许可

引擎代码按 MIT 许可使用，保留 `web/LICENSE` 与 `web/NOTICE`。按上游要求，站点不使用 AIHOT 的名字和 Logo。
