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

**美联储、CNBC、MarketWatch 抓不到**：国内服务器访问部分海外网站不稳。不影响国内快讯；可以在后台“信源”里暂停它们，或在 `web/.env` 设 `EGRESS_PROXY_URL` 走代理。

**想看看真实效果再决定门槛**：`docker compose --env-file web/.env run --rm pusher python -m finhot push --once --dry-run` 打印这一轮会推哪些、各自的规则分，不真的推。

**换服务器 / 服务器到期**：`deploy/finhot.sh backup`，把 `backups/` 里最新的文件和 `web/.env` 下载保存；新机器按本文装好后，用 `web/docs/deploy.md` 的“备份”一节恢复。
