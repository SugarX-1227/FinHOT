# 题材档案 · 知识层

每个题材一个 YAML 文件。这里放的是**变化慢、需要判断的知识**：是什么、产业链、驱动与政策、海外映射、历史行情、证伪节点。
**变化快的数据**（成分股、涨幅、市值、主营、阶段指标、最新新闻研报）不写在这里，`python -m finhot topic build` 每次从问财和 FinHOT 数据现取。

```bash
python -m finhot topic list                 # 列出已有题材
python -m finhot topic build                # 生成全部档案卡 → reports/topics/<slug>.md
python -m finhot topic build 机器人 券商     # 只生成指定题材（名称、slug、别名都行）
python -m finhot topic build --no-wencai    # 不调问财，只用知识层和 FinHOT 本地数据
python -m finhot topic probe 机器人          # 检查问财返回的列名（约 4 次调用）
python -m finhot topic usage                # 最近 7 天问财调用次数
python -m finhot topic draft 低空经济        # 让模型起草新题材（需要 LLM_API_KEY），校对后再用
```

## 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 题材名称，也是文件名 |
| `slug` | 是 | 英文短名，档案卡文件名和网址用 |
| `aliases` | | 别名，`topic build 别名` 也能找到 |
| `updated` | | 知识层最后更新日期 |
| `reviewed` | | 人工校对过设为 `true`；`false` 时档案卡顶部提示“未校对” |
| `one_liner` | 是 | 一句话讲清楚是什么、A 股炒什么 |
| `boundary` | | `includes` / `excludes` 列表，`related` 为相近概念及区别（`name`、`diff`） |
| `status` | | 行业现状，每条 `text` + `url` |
| `drivers` | | `types`（政策、产业趋势、海外映射、涨价与业绩、突发事件、资金情绪等）、`evidence`（实锤 / 预期 / 传闻）、`summary` |
| `policy` | | 每条 `date`、`name`、`issuer`、`points`、`url`（没有出处的会标“待补出处”） |
| `overseas` | | 海外映射：`name`、`ticker`、`link`（怎么传导到 A 股、隔多久）、`url` |
| `chain` | 是 | 产业链：按 `stage`（上游 / 中游 / 下游，或业务线）分组，每组 `segments`。环节字段见下 |
| `focus` | | 当前在炒的细分（人工判断）：`segment`、`why`、`url`。接入问财后档案卡会按环节涨幅排序作对照 |
| `history` | | 历史行情：`window`、`trigger`、`driver`、`leaders`、`fade`（怎么结束的）、`url`、`verified` |
| `falsifiers` | | 什么会证伪逻辑 |
| `risks` | | 风险信号 |
| `calendar` | | 催化日历：`date`、`event`、`url` |
| `wencai` | 是 | 问财问句：`universe`（选股主体，如“人形机器人概念股”）、`index`（板块指数名）、`search`（检索词） |
| `local` | 是 | FinHOT 本地数据匹配用的正则：`keywords`（快讯标题、涨停原因）、`exclude`、`boards`（板块名） |
| `sources` | | 知识层参考来源：`title`、`url` |

环节（`chain[].segments[]`）：

| 字段 | 说明 |
| --- | --- |
| `name` | 环节名 |
| `what` | 做什么，一句话 |
| `moat` | 壁垒与格局，一句话 |
| `keywords` | 主营产品关键词。问财成分股的主营产品命中时，自动归到这个环节、标“主营相关” |
| `stocks` | 已知公司：`name`、`code`（6 位，不确定就留空）、`tier`（核心 / 相关）、`note`（依据，最好写出处） |

## 写法约定

- 每条事实尽量带出处；凭记忆写的历史行情一律 `verified: false`，档案卡会标“待核实”。
- `tier: 核心` 只给业务真实、被多家机构反复点名的公司；其余写 `相关`。问财主营数据和知识层都对不上的成分股，档案卡会提示“可能是蹭概念”。
- `local.keywords` 只匹配快讯**标题**，宁可窄一点：太宽会把无关新闻（融资、人事）也算进热度。
