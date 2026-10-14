// FinHOT：从入库到日报，每一层现在是什么情况（服务器上用 deploy/finhot.sh doctor 调用）。
//
//   node scripts/finhot-doctor.ts [--days 3]
//       逐层打印：入库 → 模型分析 → 归组去重 → 精选 → 日报，再加模型调用、预算熔断、队列、定时任务和当前告警，
//       最后按数字说卡在哪一层。只读，不调用模型。worker 不为每次模型调用打日志，看日志判断不了分析停没停，看这里。
//   node scripts/finhot-doctor.ts --fix
//       用后台“运行”页同样的操作修：分析失败的文章重新排队；归组失败或卡住的精选候选重新归组（会调用模型）。
import { closeDb, sql } from "@aihot/backend/db";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { failureGroupSql, requeueFailed } from "@aihot/backend/jobs/content";
import { rerun } from "@aihot/backend/admin/content";
import { waitingSelectedNews } from "@aihot/backend/operations/grouping";
import { collectFindings } from "@aihot/backend/operations/alerts";
import { addDays, beijingAt, beijingDate, beijingTime } from "@aihot/contracts/time";
import { EDITION_TIMES } from "@aihot/site";

const args = process.argv.slice(2);
const flag = (name: string, fallback: string) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1]! : fallback;
};
const days = Math.max(1, Math.min(14, Number(flag("days", "3")) || 3));
const BJ = "Asia/Shanghai";
const stamp = (d: Date | null | undefined) => (d ? d.toLocaleString("zh-CN", { timeZone: BJ, hour12: false }).replace(/\//g, "-") : "—");
const ago = (d: Date | null | undefined) => {
  if (!d) return "—";
  const m = Math.round((Date.now() - d.getTime()) / 60000);
  return m < 90 ? `${m} 分钟前` : m < 2880 ? `${Math.round(m / 60)} 小时前` : `${Math.round(m / 1440)} 天前`;
};
const table = (head: string[], rows: Array<Array<string | number | null>>) => {
  const cells = [head, ...rows.map((r) => r.map((c) => (c === null ? "—" : String(c))))];
  const width = (s: string) => [...s].reduce((w, ch) => w + (/[⺀-￿]/.test(ch) ? 2 : 1), 0);
  const cols = head.map((_, i) => Math.max(...cells.map((r) => width(r[i] ?? ""))));
  for (const r of cells) console.log("  " + r.map((c, i) => c + " ".repeat(cols[i]! - width(c))).join("  ").trimEnd());
};
const section = (title: string) => console.log(`\n== ${title}`);
const verdicts: string[] = [];

async function doctor() {
  console.log(`FinHOT 体检 ${stamp(new Date())}（北京时间），看最近 ${days} 天`);

  section("1. 入库 → 模型分析（按入库日期；只算编辑信源，快讯推送都在这里）");
  const ingest = await sql<Array<{ day: string; total: number; backfill: number; waiting: number; stuck: number; analyzed: number; blocked: number; failed: number; skipped: number; oldest: Date | null }>>`
    SELECT to_char(a.discovered_at AT TIME ZONE ${BJ}, 'MM-DD') AS day, count(*)::int AS total,
      count(*) FILTER (WHERE a.backfill)::int AS backfill,
      count(*) FILTER (WHERE a.processing_state = 'new')::int AS waiting,
      count(*) FILTER (WHERE a.processing_state = 'new' AND a.discovered_at < now() - interval '1 hour')::int AS stuck,
      count(*) FILTER (WHERE a.processing_state = 'analyzed')::int AS analyzed,
      count(*) FILTER (WHERE a.processing_state = 'blocked')::int AS blocked,
      count(*) FILTER (WHERE a.processing_state = 'failed')::int AS failed,
      count(*) FILTER (WHERE a.processing_state = 'skipped')::int AS skipped,
      min(a.discovered_at) FILTER (WHERE a.processing_state = 'new') AS oldest
    FROM articles a JOIN sources s ON s.id = a.source_id
    WHERE s.participation_mode = 'editorial' AND a.discovered_at > date_trunc('day', now() AT TIME ZONE ${BJ}) AT TIME ZONE ${BJ} - make_interval(days => ${days - 1})
    GROUP BY 1 ORDER BY 1`;
  table(["日期", "入库", "历史补录", "等分析", "其中超1小时", "分析完", "拦下", "失败", "跳过"],
    ingest.map((r) => [r.day, r.total, r.backfill, r.waiting, r.stuck, r.analyzed, r.blocked, r.failed, r.skipped]));
  const stuck = ingest.reduce((n, r) => n + r.stuck, 0);
  const failed = ingest.reduce((n, r) => n + r.failed, 0);
  const analyzed = ingest.reduce((n, r) => n + r.analyzed, 0);
  if (!ingest.length) verdicts.push("最近没有编辑信源的文章入库：先看推送进程（deploy/finhot.sh status）");
  if (stuck >= 10) verdicts.push(`${stuck} 篇文章入库超过 1 小时还没分析完：分析卡住了，看第 5、6 节（模型调用、预算）和第 7 节（队列）`);
  if (failed >= 10) verdicts.push(`${failed} 篇文章分析失败：看下面的失败原因；修好原因后 deploy/finhot.sh doctor --fix 重新排队`);
  const outside = await sql<Array<{ id: string; mode: string; n: number }>>`
    SELECT s.id, s.participation_mode AS mode, count(*)::int AS n FROM articles a JOIN sources s ON s.id = a.source_id
    WHERE s.kind = 'external' AND s.participation_mode <> 'editorial' AND a.discovered_at > now() - make_interval(days => ${days})
    GROUP BY 1, 2 ORDER BY 3 DESC`;
  for (const o of outside) verdicts.push(`推送信源 ${o.id} 是 ${o.mode}（不分析、不进精选），最近 ${o.n} 篇白推了：后台“信源”页改成 editorial`);
  const processingErrors = await sql<Array<{ error: string; n: number; state: string }>>`
    SELECT ${failureGroupSql()} AS error, processing_state AS state, count(*)::int AS n FROM articles
    WHERE processing_state IN ('new', 'failed') AND processing_error IS NOT NULL AND discovered_at > now() - make_interval(days => ${days})
    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 6`;
  if (processingErrors.length) {
    console.log("  分析没成功的原因（等重试 / 已失败）：");
    for (const e of processingErrors) console.log(`    ${String(e.n).padStart(4)} 篇 ${e.state === "new" ? "等重试" : "已失败"}  ${e.error}`);
  }

  section("2. 分析结果（每篇最新一次分析；精选候选 = 两次评分够门槛）");
  const analysis = await sql<Array<{ day: string; pass: number; block: number; unknown: number; scored: number; candidates: number; best: number | null }>>`
    WITH latest AS (
      SELECT DISTINCT ON (an.article_id) an.article_id, an.relevance, an.score, an.selected
      FROM analyses an JOIN articles a ON a.id = an.article_id AND an.input_revision = a.revision
      WHERE a.discovered_at > date_trunc('day', now() AT TIME ZONE ${BJ}) AT TIME ZONE ${BJ} - make_interval(days => ${days - 1})
      ORDER BY an.article_id, an.id DESC
    )
    SELECT to_char(a.discovered_at AT TIME ZONE ${BJ}, 'MM-DD') AS day,
      count(*) FILTER (WHERE l.relevance = 'pass')::int AS pass, count(*) FILTER (WHERE l.relevance = 'block')::int AS block,
      count(*) FILTER (WHERE l.relevance = 'unknown')::int AS unknown, count(*) FILTER (WHERE l.score IS NOT NULL)::int AS scored,
      count(*) FILTER (WHERE l.selected)::int AS candidates, max(l.score)::int AS best
    FROM latest l JOIN articles a ON a.id = l.article_id GROUP BY 1 ORDER BY 1`;
  table(["日期", "通过", "拦下", "没写成", "有评分", "精选候选", "最高分"], analysis.map((r) => [r.day, r.pass, r.block, r.unknown, r.scored, r.candidates, r.best]));
  const candidates = analysis.reduce((n, r) => n + r.candidates, 0);
  if (analyzed > 20 && candidates === 0) verdicts.push("分析在跑，但没有一篇够精选门槛：门槛偏高，deploy/finhot.sh selection stats 看分布");

  section("3. 归组去重（精选候选要等这一步做完才会真正进精选）");
  const grouping = await sql<Array<{ status: string; n: number; candidates: number; no_value: number }>>`
    SELECT a.grouping_status AS status, count(*)::int AS n,
      count(*) FILTER (WHERE p.selection_candidate)::int AS candidates,
      count(*) FILTER (WHERE p.selection_candidate AND a.selection_adds_value = false)::int AS no_value
    FROM articles a JOIN sources s ON s.id = a.source_id LEFT JOIN publications p ON p.article_id = a.id
    WHERE s.participation_mode = 'editorial' AND a.processing_state = 'analyzed'
      AND a.discovered_at > now() - make_interval(days => ${days})
    GROUP BY 1 ORDER BY 1`;
  const label: Record<string, string> = { pending: "还没归组", complete: "已归组", failed: "归组失败" };
  table(["状态", "文章", "其中精选候选", "候选里判定为重复"], grouping.map((g) => [label[g.status] ?? g.status, g.n, g.candidates, g.no_value]));
  const waiting = await waitingSelectedNews();
  if (waiting.length) {
    const failedGroup = waiting.filter((w) => w.failed);
    console.log(`  卡在归组的精选候选 ${waiting.length} 篇（失败 ${failedGroup.length}），最早从 ${stamp(waiting[0]!.since)} 开始等：`);
    for (const w of waiting.slice(0, 8)) console.log(`    ${w.failed ? "失败" : "在等"}  ${ago(w.since).padEnd(8)} ${w.title.slice(0, 40)}${w.error ? `  ← ${w.error.slice(0, 80)}` : ""}`);
    if (failedGroup.length) verdicts.push(`${failedGroup.length} 篇精选候选归组失败，进不了精选：修好原因后 deploy/finhot.sh doctor --fix 重新归组`);
    else if (Date.now() - waiting[0]!.since.getTime() > 3600_000) verdicts.push(`${waiting.length} 篇精选候选等归组超过 1 小时：看第 7 节 events.group 排队`);
  }
  const groupErrors = await sql<Array<{ error: string; n: number }>>`
    SELECT ${failureGroupSql("grouping_error")} AS error, count(*)::int AS n FROM articles
    WHERE grouping_status = 'failed' AND discovered_at > now() - make_interval(days => ${days}) GROUP BY 1 ORDER BY 2 DESC LIMIT 5`;
  if (groupErrors.length) {
    console.log("  归组失败的原因：");
    for (const e of groupErrors) console.log(`    ${String(e.n).padStart(4)} 篇  ${e.error}`);
  }

  section("4. 精选 → 日报（每期日报的窗口是前一天 08:00 到当天 08:00）");
  // 日报平时按“进精选的时间”取材：后来才进精选的（调门槛、补选、归组晚了）算到进精选那一刻之后的一期。
  const today = beijingDate(new Date());
  const next = beijingTime(new Date()) >= EDITION_TIMES.daily ? addDays(today, 1) : today;
  const saved = new Map((await sql<Array<{ key: string; n: number | null }>>`
    SELECT key, (content->'generator'->>'selectedReports')::int AS n FROM reports WHERE kind = 'daily' AND key >= ${addDays(next, -days)}`).map((r) => [r.key, r.n]));
  const issues: Array<[string, number, number, number | null]> = [];
  for (let date = addDays(next, -days); date <= next; date = addDays(date, 1)) {
    const end = beijingAt(date, EDITION_TIMES.daily);
    const begin = new Date(end.getTime() - 86400000);
    const [c] = await sql<Array<{ arrived: number; released: number }>>`
      SELECT count(*) FILTER (WHERE p.timeline_at >= ${begin} AND p.timeline_at < ${end})::int AS arrived,
        count(*) FILTER (WHERE ((p.visible_after <= p.timeline_at AND p.timeline_at >= ${begin} AND p.timeline_at < ${end})
          OR (p.visible_after > p.timeline_at AND p.visible_after >= ${begin} AND p.visible_after < ${end}))
          AND p.timeline_at >= ${begin}::timestamptz - interval '24 hours')::int AS released
      FROM publications p WHERE p.selected AND p.visibility = 'public' AND NOT p.backfill
        AND (p.timeline_at >= ${begin}::timestamptz - interval '24 hours' OR p.visible_after >= ${begin})`;
    issues.push([date, c!.arrived, c!.released, saved.get(date) ?? null]);
  }
  table(["日报", "这期窗口里发布的精选", "按进精选时间归这期", "这期日报实际取到"],
    issues.map(([date, arrived, released, got]) => [date === next ? `${date}（下一期）` : date, arrived, released, date === next ? "—" : got]));
  const selectedTotal = issues.reduce((n, [, arrived]) => n + arrived, 0);
  if (candidates > 0 && selectedTotal === 0) verdicts.push("有精选候选、但一篇都没进精选：卡在归组去重（第 3 节）");
  const late = issues.filter(([date, arrived, , got]) => date !== next && got !== null && arrived > got);
  if (late.length) verdicts.push(`${late.map(([d]) => d).join("、")} 的日报编的时候，窗口里发布的精选大多还没进精选（后来调门槛、补选才进的）。` +
    `日报按进精选的时间取材，所以它们没进这几期：deploy/finhot.sh selection redo-daily --from ${late[0]![0]} --to ${late.at(-1)![0]} 按发布时间重编`);

  section("5. 模型调用（最近 24 小时，按小时；失败和结果未知的单列）");
  const calls = await sql<Array<{ hour: string; service: string; n: number; failed: number; unknown: number; latency: number | null }>>`
    SELECT to_char(date_trunc('hour', started_at) AT TIME ZONE ${BJ}, 'MM-DD HH24') || '时' AS hour, service, count(*)::int AS n,
      count(*) FILTER (WHERE status = 'failed')::int AS failed, count(*) FILTER (WHERE status = 'unknown')::int AS unknown,
      (percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) / 1000)::int AS latency
    FROM receipt_attempts WHERE origin = 'live' AND started_at > now() - interval '24 hours'
    GROUP BY 1, 2 ORDER BY 1, 2`;
  table(["时段", "服务", "调用", "失败", "未知", "耗时中位(秒)"], calls.map((c) => [c.hour, c.service, c.n, c.failed, c.unknown, c.latency]));
  const [last] = await sql<Array<{ ok: Date | null; any: Date | null }>>`
    SELECT max(started_at) FILTER (WHERE status = 'received') AS ok, max(started_at) AS any FROM receipt_attempts WHERE origin = 'live'`;
  console.log(`  最近一次调用 ${stamp(last?.any)}（${ago(last?.any)}），最近一次成功 ${stamp(last?.ok)}（${ago(last?.ok)}）`);
  // 归类时数字被换成了“…”，再附一条原文：GLM 的错误码（1302 并发、1303 频率、1308 额度……）就在里面
  const callErrors = await sql<Array<{ service: string; error: string; n: number; example: string }>>`
    SELECT service, ${failureGroupSql("error")} AS error, count(*)::int AS n,
      (array_agg(left(error, 240) ORDER BY started_at DESC))[1] AS example FROM receipt_attempts
    WHERE origin = 'live' AND status IN ('failed', 'unknown') AND started_at > now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5`;
  for (const e of callErrors) console.log(`    ${String(e.n).padStart(4)} 次 ${e.service}  ${e.error}\n         例：${e.example}`);
  const limited = callErrors.filter((e) => /HTTP 429/.test(e.error)).reduce((n, e) => n + e.n, 0);
  const code = /"code"\s*:\s*"?(\d{4})/.exec(callErrors.find((e) => /HTTP 429/.test(e.error))?.example ?? "")?.[1];
  if (limited >= 50) verdicts.push(`最近 24 小时 ${limited} 次模型调用被限流（HTTP 429${code ? `，错误码 ${code}` : ""}）：` + (
    code === "1302" ? "并发太高，在 web/.env 写 ANALYZE_CONCURRENCY=3，再 deploy/finhot.sh restart worker"
      : code === "1303" ? "频率太高，后台“设置 → 付费请求上限”把 llm 的每分钟上限调到平时用量以下"
        : code && /^13(08|10)$/.test(code) ? "套餐在这个时段的额度用完了，只能少调用（FINHOT_PUSH_MIN_SCORE 调高）或换更大的套餐"
          : "先试 ANALYZE_CONCURRENCY=3（web/.env，再 deploy/finhot.sh restart worker）") +
    "；被限流用光重试的文章，原因解决后 deploy/finhot.sh doctor --fix 重新排队");
  const totalCalls = calls.reduce((n, c) => n + c.n, 0);
  const failedCalls = calls.reduce((n, c) => n + c.failed + c.unknown, 0);
  if (totalCalls > 0 && failedCalls / totalCalls > 0.3) verdicts.push(`最近 24 小时模型调用 ${failedCalls}/${totalCalls} 次失败：看上面的错误（余额、限流、模型名）`);
  if (stuck >= 10 && last?.any && Date.now() - last.any.getTime() > 30 * 60_000) verdicts.push(`上一次模型调用在 ${ago(last.any)}，而文章在排队：worker 没在消费，看第 7、8 节`);

  section("6. 预算熔断（达到上限后这一路调用全停，等时间窗过去）");
  const budgets = await sql<Array<{ service: string; per_minute: number; per_hour: number; per_day: number; minute: number; hour: number; day: number }>>`
    SELECT b.service, b.per_minute, b.per_hour, b.per_day,
      count(ra.*) FILTER (WHERE ra.started_at > now() - interval '1 minute')::int AS minute,
      count(ra.*) FILTER (WHERE ra.started_at > now() - interval '1 hour')::int AS hour, count(ra.*)::int AS day
    FROM budgets b LEFT JOIN receipt_attempts ra ON ra.service = b.service AND ra.origin = 'live' AND ra.started_at > now() - interval '1 day'
    GROUP BY 1, 2, 3, 4 HAVING count(ra.*) > 0 OR b.service IN ('llm', 'zhipu') ORDER BY 1`;
  table(["服务", "1分钟 用/限", "1小时 用/限", "24小时 用/限"], budgets.map((b) => [b.service, `${b.minute}/${b.per_minute}`, `${b.hour}/${b.per_hour}`, `${b.day}/${b.per_day}`]));
  for (const b of budgets) {
    const full = b.per_day <= 0 || b.per_hour <= 0 || b.per_minute <= 0 ? "已被设成 0（停用）" : b.day >= b.per_day ? "24 小时额度用完" : b.hour >= b.per_hour ? "1 小时额度用完" : null;
    if (full) verdicts.push(`预算熔断：${b.service} ${full}，调用全部暂停。后台“运行”页或 budgets 表调高 per_day / per_hour`);
  }
  const [budgetWaits] = await sql<Array<{ n: number }>>`
    SELECT count(*)::int AS n FROM articles WHERE processing_state = 'new' AND processing_error LIKE 'Budget for % exhausted%'`;
  if (budgetWaits!.n) console.log(`  ${budgetWaits!.n} 篇文章在等预算恢复`);

  section("7. 队列（等待中和正在跑的任务）");
  const queues = await sql<Array<{ name: string; state: string; n: number; oldest: Date }>>`
    SELECT name, state, count(*)::int AS n, min(created_on) AS oldest FROM pgboss.job
    WHERE state IN ('created', 'retry', 'active') AND name NOT LIKE 'cron.%' GROUP BY 1, 2 ORDER BY 1, 2`;
  table(["队列", "状态", "任务", "最早的"], queues.map((q) => [q.name, q.state, q.n, ago(q.oldest)]));
  if (!queues.length) console.log("  （空）");
  for (const q of queues) if (q.state !== "active" && Date.now() - q.oldest.getTime() > 2 * 3600_000 && q.n >= 5) {
    verdicts.push(`${q.name} 有 ${q.n} 个任务排队超过 2 小时：处理不过来或没在消费${q.name === "events.group" ? "（归组是一条一条串行的，模型慢时会越积越多）" : ""}`);
  }
  const failedJobs = await sql<Array<{ name: string; n: number; output: string | null }>>`
    SELECT name, count(*)::int AS n, left((array_agg(output::text ORDER BY completed_on DESC))[1], 160) AS output
    FROM pgboss.job WHERE state = 'failed' AND completed_on > now() - interval '24 hours' AND name NOT LIKE 'cron.%' GROUP BY 1 ORDER BY 2 DESC`;
  for (const f of failedJobs) console.log(`  24 小时内失败 ${f.n} 个 ${f.name}，最近一个：${f.output ?? ""}`);

  section("8. 进程心跳与定时任务");
  const beats = await sql<Array<{ key: string; updated_at: Date; value: { startedAt?: string } }>>`
    SELECT key, updated_at, value FROM settings WHERE key LIKE 'heartbeat.%' ORDER BY key`;
  for (const b of beats) {
    const alive = Date.now() - b.updated_at.getTime() < 3 * 60_000;
    console.log(`  ${b.key.slice(10).padEnd(8)} ${alive ? "在线" : "掉线"}，心跳 ${ago(b.updated_at)}，启动于 ${stamp(b.value.startedAt ? new Date(b.value.startedAt) : null)}`);
    if (b.key === "heartbeat.worker" && !alive) verdicts.push("worker 心跳停了：deploy/finhot.sh restart worker，再 deploy/finhot.sh logs worker 看启动报错");
  }
  const jobs = await sql<Array<{ job: string; started_at: Date; status: string; error: string | null; failed: number; runs: number }>>`
    SELECT DISTINCT ON (r.job) r.job, r.started_at, r.status, left(r.error, 160) AS error,
      (SELECT count(*) FILTER (WHERE status = 'failed') FROM job_runs x WHERE x.job = r.job AND x.started_at > now() - interval '24 hours')::int AS failed,
      (SELECT count(*) FROM job_runs x WHERE x.job = r.job AND x.started_at > now() - interval '24 hours')::int AS runs
    FROM job_runs r WHERE r.job IN ('content.sweep', 'ops.recover', 'reports.compose', 'hot.rank', 'stories.links')
    ORDER BY r.job, r.started_at DESC`;
  table(["定时任务", "上次", "结果", "24小时失败/次数"], jobs.map((j) => [j.job, ago(j.started_at), j.status, `${j.failed}/${j.runs}`]));
  for (const j of jobs) if (j.error && j.status === "failed") console.log(`    ${j.job}：${j.error}`);
  const sweep = jobs.find((j) => j.job === "content.sweep");
  if (!sweep || Date.now() - sweep.started_at.getTime() > 30 * 60_000) verdicts.push("content.sweep 超过 30 分钟没跑：worker 的定时任务没在运行");

  section("9. 日报（最近几期的取材统计）");
  const reports = await sql<Array<{ key: string; generated_at: Date; revision: number; g: Record<string, number> | null; lead: string | null }>>`
    SELECT key, generated_at, revision, content->'generator' AS g, content->'lead'->>'title' AS lead FROM reports
    WHERE kind = 'daily' ORDER BY key DESC LIMIT ${days + 2}`;
  table(["日期", "生成于", "版本", "打过分", "精选", "事件", "取材", "头条"], reports.map((r) => [r.key, stamp(r.generated_at), r.revision,
    r.g?.judgedReports ?? null, r.g?.selectedReports ?? null, r.g?.events ?? null, r.g?.byArrival ? "按发布时间" : "按进精选时间", (r.lead ?? "").slice(0, 24)]));

  section("10. 当前告警");
  const findings = await collectFindings();
  for (const f of findings) {
    console.log(`  [${f.level}] ${f.title}${f.impact ? `：${[f.impact].flat().filter(Boolean).join("；")}` : ""}`);
    if (f.detail) console.log(`         ${String(f.detail).slice(0, 300)}`);
  }
  if (!findings.length) console.log("  （没有）");

  section("结论");
  if (!verdicts.length) verdicts.push("各层都没看到明显问题。日报仍为空的话，把这份输出发给维护的人");
  for (const v of verdicts) console.log(`  - ${v}`);
}

async function fix() {
  const failedArticles = await requeueFailed(null);
  console.log(`分析失败的文章重新排队：${failedArticles.requeued} 篇（最近 30 天）`);
  const batch = Date.now().toString(36);
  let regrouped = 0;
  for (const w of await waitingSelectedNews()) {
    // 结果未知的付费请求要先在后台核对放行，其余的（失败、长时间没动）重新归组
    if (w.recovery === "receipt" || (!w.failed && Date.now() - w.since.getTime() < 3600_000)) continue;
    if (await rerun(w.articleId, "group", `doctor-${batch}-${w.articleId.slice(0, 40)}`, "finhot-doctor")) regrouped += 1;
  }
  console.log(`卡住的精选候选重新归组：${regrouped} 篇。worker 处理完（几分钟到几十分钟）后再跑一次 deploy/finhot.sh doctor 看结果。`);
}

try {
  if (args.includes("--fix")) await fix();
  else await doctor();
} finally {
  await stopBoss();
  await closeDb();
}
