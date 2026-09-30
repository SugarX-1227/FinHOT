# FinHOT — 财经热点日报（A股交易参考）

> 每天自动采集财经新闻，用 LLM 筛选、摘要并映射到 A 股板块，生成盘前可读的日报。
> 参考 [AIHOT](https://github.com/KKKKhazix/AIHOT) 的"信源 → 打分 → 聚簇 → 日报"思路，针对 A 股交易场景做轻量化定制。

## 项目定位

- **输入**：公开财经信源（财联社电报、华尔街见闻、金十数据、监管机构公告、海外市场隔夜行情等）
- **处理**：去重 → LLM 分类（宏观/政策/行业/公司/海外/资金面）→ 重要性打分 → 中文摘要 → **A股映射**（利好/利空方向、相关行业板块、代表 ETF/个股）
- **输出**：`reports/YYYY-MM-DD.md` 盘前日报，自动 commit 到本仓库

与通用新闻站的差异在于最后一环：**每条重要新闻都要回答"这对明天 A 股的哪个板块意味着什么"**。

## 目录规划

```
.
├── sources/            # 信源配置（名称、类型、URL、抓取频率、优先级）
├── src/                # 采集与处理脚本（Python）
├── prompts/            # LLM 提示词（分类/打分/摘要/板块映射）
├── reports/            # 生成的日报（YYYY-MM-DD.md）
├── .github/workflows/  # 定时调度（GitHub Actions）
└── docs/DESIGN.md      # 详细设计文档
```

## 快速开始（待实现）

```bash
# 目标形态：
pip install -r requirements.txt
export LLM_API_KEY=sk-xxx          # 任意 OpenAI 兼容 API
python src/main.py --date today     # 手动生成一次日报
# 或等 GitHub Actions 每天盘前自动运行
```

## Roadmap

- [ ] M1 单源采集：跑通 1-2 个信源，输出原始条目 JSON
- [ ] M2 多源采集 + 去重（URL + 标题相似度）
- [ ] M3 LLM 流水线：分类 / 打分 / 摘要 / A股板块映射
- [ ] M4 自动化：GitHub Actions 定时生成日报并 commit
- [ ] M5 增强：周报、热度统计、行情数据接入（涨停池/龙虎榜/北向资金）
- [ ] M6 可选：迁移到 AIHOT 完整框架，做成网站

## 免责声明

本项目仅为个人信息整理工具，所有内容由公开新闻与 AI 生成，不构成任何投资建议。
