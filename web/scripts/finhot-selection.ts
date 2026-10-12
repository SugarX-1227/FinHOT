// FinHOT：精选门槛的诊断与补选（服务器上用 deploy/finhot.sh selection 调用）。
//
//   node scripts/finhot-selection.ts stats [--days 7] [--target 40]
//       最近几天每个分级的评分分布：门槛定在多少，每天大约有几件事进精选；按 --target（每天想要几件）给出 T2 的建议值，
//       并列出建议门槛上下的标题，方便人工看一眼。
//   node scripts/finhot-selection.ts reselect [--days 3]
//       改了门槛以后，把最近几天“按新门槛该进 / 该出精选”的条目重新过一遍分析。两次评分和写好的标题摘要都会复用
//       已付费的回执，不产生新的模型调用；worker 处理完（通常几分钟）精选就更新了。
//   node scripts/finhot-selection.ts redo-daily --from 2026-10-11 [--to 2026-10-12]
//       按现在的精选重新编排这几天的日报（已出的那期留作历史版本）。先 reselect、等 worker 处理完再跑。
//       平时日报按“进精选的时间”取材（补选进来的算到补选那天之后的一期）；重编按发布时间归期，
//       让补选的文章回到它发布那天的日报里，之后几期已经收录的不重复。
import { closeDb, sql } from "@aihot/backend/db";
import { tierThreshold } from "@aihot/backend/editorial/analyze";
import { queueProcessing } from "@aihot/backend/jobs/content";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { composeDaily } from "@aihot/backend/reports/compose";

const [command = "help", ...rest] = process.argv.slice(2);
const flag = (name: string, fallback: string) => {
  const i = rest.indexOf(`--${name}`);
  return i >= 0 && rest[i + 1] ? rest[i + 1]! : fallback;
};

interface Row { article_id: string; tier: string; score: number; selected: boolean; event: string; day: string; title: string }

/** 每篇资料最新一次带评分的分析，及其所在的事件和北京日期。 */
async function scored(days: number): Promise<Row[]> {
  return sql<Row[]>`
    WITH latest AS (
      SELECT DISTINCT ON (a.article_id) a.article_id, a.score, a.selected, a.relevance
      FROM analyses a WHERE a.created_at > now() - make_interval(days => ${days + 2})
      ORDER BY a.article_id, a.id DESC
    )
    SELECT l.article_id, s.tier, l.score::float AS score, l.selected,
           coalesce('f' || p.fact_id::text, 'a' || p.article_id) AS event,
           to_char(p.timeline_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') AS day, p.title
    FROM latest l JOIN publications p ON p.article_id = l.article_id JOIN sources s ON s.id = p.source_id
    WHERE l.relevance = 'pass' AND l.score IS NOT NULL AND NOT p.backfill AND p.visibility = 'public'
      AND p.timeline_at > now() - make_interval(days => ${days})`;
}

/** 门槛设成 t 时，每天有几件事（同一件事多家报道只算一次）够分。 */
function eventsPerDay(rows: Row[], pass: (r: Row) => boolean): Map<string, number> {
  const byDay = new Map<string, Set<string>>();
  for (const r of rows) if (pass(r)) {
    const set = byDay.get(r.day) ?? new Set<string>();
    set.add(r.event);
    byDay.set(r.day, set);
  }
  return new Map([...byDay].map(([d, s]) => [d, s.size]));
}

async function stats() {
  const days = Number(flag("days", "7"));
  const target = Number(flag("target", "40"));
  const rows = await scored(days);
  if (!rows.length) { console.log(`最近 ${days} 天没有带评分的资料。`); return; }
  const dates = [...new Set(rows.map((r) => r.day))].sort();
  const current = (r: Row) => r.score >= (tierThreshold(r.tier) ?? Infinity);

  console.log(`最近 ${days} 天：${rows.length} 篇有评分，分级：` +
    [...new Set(rows.map((r) => r.tier))].map((t) => `${t} ${rows.filter((r) => r.tier === t).length} 篇（门槛 ${tierThreshold(t) ?? "无"}）`).join("，"));
  console.log("\n现在的门槛下，每天够分的事件数 / 已经是精选的篇数：");
  const now = eventsPerDay(rows, current);
  for (const d of dates) console.log(`  ${d}  ${String(now.get(d) ?? 0).padStart(4)} 件   ${rows.filter((r) => r.day === d && r.selected).length} 篇在分析时已够分`);

  console.log("\nT2 门槛换成下面的数（T1、T1_5 不变），每天够分的事件数：");
  console.log("  门槛  " + dates.map((d) => d.slice(5)).join("  ") + "   日均");
  const sweep: Array<{ t: number; avg: number }> = [];
  for (let t = 50; t <= 80; t += 2) {
    const per = eventsPerDay(rows, (r) => (r.tier === "T2" ? r.score >= t : current(r)));
    const counts = dates.map((d) => per.get(d) ?? 0);
    const avg = counts.reduce((a, b) => a + b, 0) / dates.length;
    sweep.push({ t, avg });
    console.log(`  ${String(t).padStart(4)}  ${counts.map((n) => String(n).padStart(5)).join(" ")}  ${avg.toFixed(1).padStart(6)}`);
  }
  const pick = sweep.filter((s) => s.avg >= target).at(-1) ?? sweep[0]!;
  console.log(`\n想要每天约 ${target} 件精选：T2 门槛建议 ${pick.t}（日均 ${pick.avg.toFixed(1)} 件）。` +
    `在 web/.env 写 SELECTION_T2=${pick.t}，再 deploy/finhot.sh restart api worker，最后 deploy/finhot.sh selection reselect。`);
  console.log("注意：周末、节假日消息少，按交易日的数字来定。");

  const t2 = rows.filter((r) => r.tier === "T2").sort((a, b) => b.score - a.score);
  const show = (label: string, list: Row[]) => {
    console.log(`\n${label}`);
    for (const r of list) console.log(`  ${r.score.toFixed(0).padStart(3)}  ${r.day.slice(5)}  ${r.title.slice(0, 60)}`);
  };
  show(`刚好够 ${pick.t} 分的（T2，${pick.t}–${pick.t + 3}）：`, t2.filter((r) => r.score >= pick.t && r.score < pick.t + 4).slice(0, 15));
  show(`差一点的（T2，${pick.t - 4}–${pick.t - 1}）：`, t2.filter((r) => r.score >= pick.t - 4 && r.score < pick.t).slice(0, 15));
}

async function reselect() {
  const days = Number(flag("days", "3"));
  const rows = await scored(days);
  const changed = rows.filter((r) => r.selected !== (r.score >= (tierThreshold(r.tier) ?? Infinity)));
  let queued = 0;
  for (const r of changed) if (await queueProcessing(r.article_id)) queued += 1;
  const up = changed.filter((r) => !r.selected).length;
  console.log(`最近 ${days} 天按现在的门槛：${up} 篇该进精选、${changed.length - up} 篇该出，已排队重新分析 ${queued} 篇（复用已有评分，不调用模型）。`);
  console.log("worker 处理完（几分钟）精选就会更新；之后可以 deploy/finhot.sh selection redo-daily --from <日期> 重编日报。");
}

async function redoDaily() {
  const from = flag("from", "");
  const to = flag("to", from);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) throw new Error("用法：redo-daily --from YYYY-MM-DD [--to YYYY-MM-DD]");
  for (let d = new Date(`${from}T00:00:00Z`); d <= new Date(`${to}T00:00:00Z`); d = new Date(d.getTime() + 86400000)) {
    const date = d.toISOString().slice(0, 10);
    try {
      const res = await composeDaily(date, "FinHOT 调整精选门槛后重编（按发布时间归期）", true);
      console.log(`${date} 日报：${res.entries} 件`);
    } catch (error) {
      console.log(`${date} 日报没有重编：${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

try {
  if (command === "stats") await stats();
  else if (command === "reselect") await reselect();
  else if (command === "redo-daily") await redoDaily();
  else console.log("用法：node scripts/finhot-selection.ts stats [--days 7] [--target 40] | reselect [--days 3] | redo-daily --from YYYY-MM-DD [--to YYYY-MM-DD]");
} finally {
  await stopBoss();
  await closeDb();
}
