# 在腾讯云轻量服务器上部署 FinHOT

写给第一次用云服务器的人。照着做，大约 40 分钟（其中 20 分钟是等它自己构建）。

部署完会有：

- 一个网站：`http://你的公网IP:3000`，精选流、事件、热点、日报，RSS、API、MCP 都有；后台在 `/admin`；
- 快讯推送进程：每 90 秒抓一轮财联社、华尔街见闻、金十等，把值得看的推给网站，由模型精选；
- 原来 GitHub Actions 出的早报晚报、GitHub Pages 静态站照常运行，互不影响。

## 这台服务器够不够用

够内测用。逐项看：

| 项 | 你的配置 | 需要多少 | 结论 |
|---|---|---|---|
| CPU / 内存 | 4 核 / 4 GB | 平时：数据库、API、后台任务、网页、推送进程合计约 1.5 GB；构建网站镜像时峰值 2~3 GB | 够。装机脚本会再加 4 GB 虚拟内存，防止构建时内存不够 |
| 硬盘 | 40 GB SSD | 系统约 5 GB，镜像约 3 GB，数据库每天增长约 20~40 MB，日志有上限 | 用到明年 3 月到期绰绰有余 |
| 带宽 / 流量 | 3 Mbps，每月 300 GB | 一次打开页面几百 KB；十几个朋友同时用没问题 | 够。公开发布、人多了再升级 |
| 地域 | 上海 | 国内财经网站访问最快 | 美联储、CNBC 等海外 RSS 可能偶尔抓不到（见常见问题），不影响国内快讯 |
| 网址 | 只有 IP | 国内服务器用域名对外提供网站要先 ICP 备案 | 内测用 `IP:3000` 访问，不需要备案 |
| 到期 | 2027-03-27 | — | 到期前续费，或按“备份”一节导出数据再迁移 |

## 第 1 步：（可选）给现在的系统拍个快照

这台机器现在装的是 OpenClaw（龙虾）。**重装系统会清空系统盘**。里面有要留的东西，先在控制台：实例 → 快照 → 创建快照。不需要就跳过。

## 第 2 步：重装成 Ubuntu 24.04

控制台 → 你的实例 → 右上角“更多操作” → **重装系统**：

1. 选 **系统镜像** → **Ubuntu** → **Ubuntu Server 24.04 LTS 64bit**；
2. 登录方式选“设置密码”，记好密码（用户名是 `ubuntu`）；
3. 确认重装，等几分钟，状态回到“运行中”。

> 也可以选“应用镜像”里的 Docker CE（基于 Ubuntu），装机脚本会跳过已经装好的 Docker。

## 第 3 步：在防火墙放行 3000 端口

控制台 → 实例 → **防火墙** → 添加规则：

| 应用类型 | 来源 | 协议 | 端口 | 策略 |
|---|---|---|---|---|
| 自定义 | 全部 IPv4 地址 | TCP | 3000 | 允许 |

22（登录用）默认已经放行，不要删。

## 第 4 步：登录服务器

实例页点 **登录**，用浏览器里的终端（OrcaTerm）登录，不用装任何软件。看到 `ubuntu@...:~$` 就是登上了。

下面的命令一行一行复制进去回车。

## 第 5 步：装环境、下载代码

```bash
sudo -i
cd /opt
git clone https://github.com/SugarX-1227/FinHOT.git finhot
cd /opt/finhot
bash deploy/server-setup.sh
```

`server-setup.sh` 会：加 4 GB 虚拟内存、把时区设成北京时间、从腾讯云的软件源装 Docker、配好镜像加速和日志上限。最后会打印 Docker 的版本，没有报错就成功了。

> `git clone` 卡住或失败（国内访问 GitHub 偶尔不稳），多试一两次。

## 第 6 步：生成配置

```bash
deploy/finhot.sh init
```

它会自动查到这台机器的公网 IP，然后让你输入**智谱的 API Key**（输入时屏幕上不显示，正常）。完成后打印：

```
网站地址：http://你的公网IP:3000    后台：http://你的公网IP:3000/admin
管理员密码（只显示这一次，请记下来）：xxxxxxxxxxxxxxxx
```

**把管理员密码记下来。** 所有配置都在 `/opt/finhot/web/.env` 这一个文件里（只有 root 能读），里面有密码和密钥，不要发给别人。

模型默认用智谱的编程套餐接口和 `glm-5.3-flash`（和你本地 FinHOT 用的一样）。要换模型或接口，改 `web/.env` 里的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`，再 `deploy/finhot.sh restart api worker`。

> 你用的如果是智谱的“编程套餐”，留意它的使用条款是否允许在自己的网站后台调用；不允许的话，把 `LLM_BASE_URL` 换成通用接口 `https://open.bigmodel.cn/api/paas/v4`，用按量计费的 Key。

## 第 7 步：启动

```bash
deploy/finhot.sh up
```

第一次要构建镜像，10~20 分钟，期间屏幕会滚很多字，正常。结束后会打印网站地址。

再等几分钟：数据库建好、信源导入、推送进程开始工作。之后在浏览器打开 `http://你的公网IP:3000`：

- 刚开始“全部”里先出现快讯，模型处理完（每条约半分钟到几分钟）才进入“精选”；
- 后台 `/admin` 用管理员密码登录，“运行”页能看到各个任务，“信源”页能看到每家快讯源推了多少条。

## 日常操作

都在 `/opt/finhot` 目录里运行（先 `sudo -i`，再 `cd /opt/finhot`）：

| 想做的事 | 命令 |
|---|---|
| 看看都在不在跑 | `deploy/finhot.sh status` |
| 看推送进程在干什么 | `deploy/finhot.sh logs pusher`（Ctrl+C 退出） |
| 看网站后台任务日志 | `deploy/finhot.sh logs worker` |
| 更新到最新代码 | `deploy/finhot.sh update` |
| 备份数据库 | `deploy/finhot.sh backup`（存在 `backups/`） |
| 重启某个服务 | `deploy/finhot.sh restart pusher` |
| 全部停掉（数据保留） | `deploy/finhot.sh stop`，再启动用 `deploy/finhot.sh up` |
| 精选或日报是空的，查卡在哪一层 | `deploy/finhot.sh doctor`（见下面“精选或日报是空的”） |
| 马上出一份个人简报 | `deploy/finhot.sh brief 我`（`--send` 发到群里，`--weekly` 我的周报；见“个人简报与持仓提醒”） |

`status` 里推送进程每轮一行，类似：

```
本轮 fetched=74 new=12 buffer=640 chosen=5 pushed=5 created=5 skipped=0 failed=0
```

`fetched` 是这轮抓到的快讯，`chosen` 是够门槛要推的，`created` 是网站新收下的。`failed` 一直不为 0 时看 `logs pusher` 里的原因。

## 控制模型用量

推送进程只把**规则分够门槛**的事件推给网站，每条推过去的快讯网站要调大约 5 次模型（预筛、两次评分、结构化、写标题摘要）。按过去几天的数据估算，门槛 6 分时每天推约 1000~1500 条，大约 5000~7500 次调用，每周两亿 token 上下。

在 `web/.env` 里调（改完 `deploy/finhot.sh restart pusher`）：

| 设置 | 默认 | 想省额度 | 想多收一些 |
|---|---|---|---|
| `FINHOT_PUSH_MIN_SCORE` 规则分门槛 | 6 | 6.5 或 7 | 5.5 |
| `FINHOT_PUSH_MAX_PER_EVENT` 同一件事最多推几家 | 4 | 2 或 3 | 5 |

后台“模型与评测”页能看到每一步的调用次数和 token 用量；“设置 → 付费请求上限”可以给模型调用设每分钟、每小时、每天的上限，超过就自动暂停，到下一个时段恢复。

**模型接口回 429（限流）**：`deploy/finhot.sh doctor` 第 5 节列出最近 24 小时每小时的调用和失败，后面附一条错误原文，
里面有智谱的错误码，“结论”会按错误码给出做法：

| 错误码 | 意思 | 做法 |
|---|---|---|
| 1302 | 同时进行的请求太多 | `web/.env` 写 `ANALYZE_CONCURRENCY=3`（默认 6），`deploy/finhot.sh restart worker` |
| 1303 | 请求太频繁 | 后台“设置 → 付费请求上限”把 llm 的每分钟上限调到平时用量以下（超过上限是排队等，不算失败） |
| 1308 等 | 套餐这个时段的额度用完 | 少调用：`FINHOT_PUSH_MIN_SCORE` 调高，或换更大的套餐 |

被限流的调用会自动退避重试，但一篇文章连着失败太多次就放弃了；原因解决后 `deploy/finhot.sh doctor --fix` 把它们重新排队。

## 调精选的多少

模型给每条快讯独立打两次分（0–100），平均分达到门槛才进精选。门槛按信源分级：快讯社（财联社、华尔街见闻、金十……）
都是 T2，默认 64；政府网、美联储是 T1，默认 60。日报只收精选，精选太少日报就会写“今日安静”。

不用标注，按“每天想要几件精选”来定：

```bash
deploy/finhot.sh selection stats --target 40
```

它列出最近 7 天、T2 门槛从 50 到 80 每天各有几件事够分，给出建议值，再列出建议门槛上下的标题让你看一眼。
最好在有几个交易日的数据之后再看（周末消息少）。要换门槛：

1. 在 `web/.env` 里写 `SELECTION_T2=建议值`（T1 用 `SELECTION_T1`，T1_5 用 `SELECTION_T1_5`）；
2. `deploy/finhot.sh restart api worker`；
3. `deploy/finhot.sh selection reselect --days 3`：把最近 3 天按新门槛重新过一遍。两次评分和标题摘要都复用已有结果，**不再调用模型**；
4. 等几分钟（`deploy/finhot.sh doctor` 第 1 节“等分析”降下来），`deploy/finhot.sh selection redo-daily --from 2026-10-11 --to 2026-10-12` 重编这几天的日报。
   日报平时按“进精选的时间”取材，补选进来的文章会全部算到补选之后的那一期；重编按发布时间归期，把它们放回各自那天，
   之后几期已经收录的不会重复。不重编的话，这几天的日报保持原样，补选的文章出现在下一期里。

新门槛只影响之后的精选和你补选的这几天；更早的不变。

## 精选或日报是空的

日报只收精选。一条快讯要依次过：入库 → 模型分析（预筛、两次评分、写标题摘要）→ 归组去重 → 精选，
任何一层卡住，日报都会写“今日安静”。worker 不为每次模型调用打日志，`logs worker` 里看不出分析有没有在跑，要看数据库：

```bash
deploy/finhot.sh doctor            # 最近 3 天；--days 7 看更久
```

它逐层打印：每天入库多少、分析完多少、几篇够精选、几篇卡在归组、真正进了精选几篇；再加最近 24 小时每小时的
模型调用次数和失败原因、预算熔断、队列积压、worker 心跳、最近几期日报的取材统计（打过分几篇、精选几篇）和当前告警。
最后一节“结论”直接说卡在哪一层、该怎么处理。只读，不调用模型。

原因修好以后（比如模型余额、限流、改了配置）：

1. `deploy/finhot.sh doctor --fix`：分析失败的文章重新排队；归组失败或卡了一小时以上的精选候选重新归组（会调用模型）；
2. 过半小时再跑一次 `deploy/finhot.sh doctor`，看第 4 节“精选”的篇数回来了没有；
3. `deploy/finhot.sh selection redo-daily --from 2026-10-11 --to 2026-10-12` 重编受影响的那几天的日报。
   周报会在它那一周的日报有内容后自动补上。

第 4 节逐期对比三个数：这期窗口里发布的精选、按进精选时间归到这期的、这期日报实际取到的。前两个差很多，
说明这些精选是后来才进的（调门槛、补选、归组晚了）：日报按进精选的时间取材，它们会进之后的一期，
想放回原来那天就按上面第 3 步重编。

部署当天那期日报（它的时间窗在网站收到第一篇文章之前，里面不可能有内容）不生成，也不算“日报缺失”。

## 盘面页：涨停原因与信号战绩

网站侧栏“A股 → 盘面”（手机底栏“盘面”），都由服务器上的推送进程在**交易日 15:10 以后**自动算一次推上去：

- **涨停原因**：每只涨停股为什么涨停、依据是什么（公司公告、收评点名、同花顺涨停原因、领涨概念），带可信度；
  公司澄清、异常波动这类提示写在括号里。
- **信号战绩**：网站精选里“A股影响”写了利好 / 利空的条目，加上服务器存下的涨停主线，对应板块之后 1/3/5 个交易日
  相对中证 1000 的超额收益和胜率。刚开始样本很少，跑上几周才有参考价值。
  东方财富的板块日 K 在服务器上取得到；板块列表（clist）取不到，用随代码发布的 `sources/eastmoney_boards.json`
  （GitHub 上的日报取到新板块会并进去，`deploy/finhot.sh update` 时跟着更新；服务器偶尔也能取到，
  取到的存在推送进程的数据卷里，下次一起用）。

不想等就手动跑：`deploy/finhot.sh market`（涨停原因和战绩一起；交易日盘中跑的是盘中的涨停池）。
推送进程的日志里能看到“盘面任务完成：涨停 xx 只，有依据 xx%……；信号战绩 精选利好 xx、……”；涨停原因失败会写原因，
30 分钟后自动再试；战绩没算成只记一句，下一个交易日再算。
某个行情接口在服务器上连不上时，先跑 `deploy/finhot.sh probe`，它会列出每个接口在这台服务器上通不通。

GitHub Actions 上的日报也算一份战绩，只给静态站的“战绩”页（GitHub 的机器连东方财富经常被断开，不推到网站）。
仓库 Variables 里的 `FINHOT_SITE_URL` 让它也统计网站精选的利好 / 利空（读公开接口）；以前配的 secret
`FINHOT_INGEST_TOKEN` 已经用不到，可以在仓库 Settings → Secrets and variables → Actions 里删掉。

> 推送 token（`web/.env` 的 `INGEST_TOKEN`）现在只在服务器内部用（推送进程 → 网站，走 docker 内网）。
> 换 token：`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` 生成一个新的，替换 `web/.env` 里
> `INGEST_TOKEN=` 后面那串，再 `deploy/finhot.sh restart api pusher`（会重建这两个容器，读入新 token）。

## 个人简报与持仓提醒（内测）

设计见 [PERSONALIZATION.md](PERSONALIZATION.md)。画像（持仓、自选、关注题材、推送地址）放在 `/opt/finhot/profiles/`，
**不入库**；简报、推送记录、反馈存在推送进程数据卷的 `private/` 里。都不会出现在 GitHub 和网站上。

1. **建画像**（冷启动只选 3 个题材也能出简报）：

   ```bash
   deploy/finhot.sh profile init 我 --themes 锂电储能,算力,半导体 --holdings 300750,002594
   deploy/finhot.sh profile show 我
   ```

   同花顺 / 东方财富导出的自选（CSV、TXT，GBK 也行），或者随手粘贴的代码清单：先传到服务器的 `profiles/` 下，再
   `deploy/finhot.sh profile import 我 /app/profiles/自选.csv --as holdings`（`--as watch` 导入为自选）。

2. **配推送**：飞书建一个只有自己的群 → 群设置 → 群机器人 → 添加“自定义机器人”，复制 webhook 地址；
   需要的话勾选“签名校验”。打开 `profiles/我.yaml`，把 `push` 改成：

   ```yaml
   push:
     channel: feishu          # 企业微信群机器人写 wecom
     webhook: https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
     secret: 签名校验的密钥（没开就删掉这行）
     daily_limit: 8
     quiet: "22:30-07:30"
   ```

   每位内测朋友各建一个画像、各用自己的私人群，互相看不到。

3. **试一份**：`deploy/finhot.sh brief 我`（加 `--send` 发到群里；`--weekly` 出我的周报）。
   之后不用管：推送进程按画像里的常看时段（`hours`，默认 08:20 盘前版、15:45 盘后版）在工作日自动出简报并发送；
   持仓 / 自选的重大公告（业绩预告、重组、控制权变更、问询函、停复牌、减持……）和持仓股盘中涨跌超过 5% 即时提醒，
   每人每天最多 8 条，免打扰时段里的放进下一份简报。日志里每轮有一行“个人：profiles=1 sent=…”。

4. **反馈链接**（可选）：`web/.env` 里已经有 `FINHOT_FEEDBACK_SECRET`（老的部署自己加一行，至少 16 位随机字符），
   在腾讯云防火墙放行 8790 端口，再加一行 `FINHOT_FEEDBACK_URL=http://你的公网IP:8790`，`deploy/finhot.sh restart pusher`。
   之后每条后面有“有用 · 不相关 · 已知道”，点一下就记上，用来调各类理由的权重。
   一周后看效果：`deploy/finhot.sh personal-eval 我`。

5. **关系库**：`deploy/finhot.sh profile relations 我` 从最近 30 天的快讯和公告里找“同一句话里点到你的股票和另一家公司、
   又有供货 / 订单 / 合作 / 竞争”的句子，生成待审核的候选（带原句和链接）。在 `profiles/我.yaml` 里把
   `status: 待审核` 改成 `已确认` 才参与排序；也可以自己写 `source: 人工维护` 的条目。人工写的、确认过的不会被覆盖。

股票清单（`sources/entities/stocks.json`）由 GitHub Actions 每天同步（巨潮），`deploy/finhot.sh update` 时跟着更新；
刚部署时还是几百只的种子清单，等第一次同步后（或在服务器上 `docker compose exec pusher python -m finhot entities sync`）才是全市场。
公告源在服务器上能不能连通：`docker compose --env-file web/.env exec pusher python -m finhot sources`，最后几行是公告源，
沪市条数为 0 时把 `sources/sources.yaml` 里巨潮的 `columns` 改成 `[szse, sse]`。

## 安全提醒

- 网站现在是 `http://`，不加密。**后台密码在公共 Wi-Fi 下有被截获的风险**，尽量在自己的网络里登录后台。更稳妥的办法是只在本机访问后台：自己电脑上运行 `ssh -L 3000:127.0.0.1:3000 ubuntu@你的公网IP`，再打开 `http://localhost:3000/admin`。
- 网站地址只发给内测的朋友。“A股影响”一栏由模型生成，页面上已注明仅供参考、不构成投资建议。
- 以后要用域名、对外公开：先做 ICP 备案，再按 `web/docs/deploy.md` 的“配域名和 HTTPS”开 HTTPS；公开前按产品规划第 8 节去掉利好 / 利空表述。

## 常见问题

**构建时报内存不够（killed / out of memory）**：确认第 5 步的脚本跑成功了（`free -h` 里 Swap 一行是 4.0G），再 `deploy/finhot.sh up`。

**网站打不开**：先 `deploy/finhot.sh status` 看 `web`、`api` 是否 `Up`；再确认第 3 步的防火墙规则；最后确认 `web/.env` 里 `SITE_URL` 是 `http://公网IP:3000`。

**模型调用都失败，报参数错误**：`web/.env` 里的 `LLM_EXTRA_JSON` 用的是网站引擎为 GLM 预设的写法；换成本地 FinHOT 用的
`LLM_EXTRA_JSON={"thinking":{"type":"low"}}` 再 `deploy/finhot.sh restart api worker`。报 1113 余额不足，说明套餐 Key 用在了普通接口上，
`LLM_BASE_URL` 要用 `https://open.bigmodel.cn/api/coding/paas/v4`。

**推送进程报 401**：`web/.env` 里的 `INGEST_TOKEN` 被改过了。两边用的是同一个文件，`deploy/finhot.sh restart api pusher` 即可。

**改了 `web/.env` 不生效**：2026-10-12 之前的 `deploy/finhot.sh restart` 用的是 `docker compose restart`，它不会重新读 `.env`。
`deploy/finhot.sh update` 到最新后，`restart` 会重建容器、读入新配置；也可以直接 `docker compose --env-file web/.env up -d`。

**美联储、CNBC、MarketWatch 抓不到**：国内服务器访问部分海外网站不稳。不影响国内快讯；可以在后台“信源”里暂停它们，或在 `web/.env` 设 `EGRESS_PROXY_URL` 走代理。

**想看看真实效果再决定门槛**：`docker compose --env-file web/.env run --rm pusher python -m finhot push --once --dry-run` 打印这一轮会推哪些、各自的规则分，不真的推。

**换服务器 / 服务器到期**：`deploy/finhot.sh backup`，把 `backups/` 里最新的文件和 `web/.env` 下载保存；新机器按本文装好后，用 `web/docs/deploy.md` 的“备份”一节恢复。
