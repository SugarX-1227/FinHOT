// 盘面：最近一个交易日的涨停原因，和信号战绩（推送进程每个交易日收盘后更新）。
import { useLoaderData } from "react-router";
import { SITE } from "@aihot/site";
import { apiGet, cachedPage } from "@aihot/web/lib/api.server";
import { pageReuse } from "@aihot/web/lib/page-reuse";
import { pageMeta } from "@aihot/web/lib/seo";
import { monthDayTime } from "@aihot/web/lib/format";
import { EmptyState } from "@aihot/web/components/ui/Page";
import { PhoneBar } from "@aihot/web/components/shell/PhoneBar";
import type { Screen } from "@aihot/web/components/shell/screens";
import type { LimitUpDay, LimitUpStock, MarketResponse, RecordData, RecordRow } from "../types.ts";

export const handle: Screen = { tab: "market", name: "盘面" };
export { pageHeaders as headers } from "@aihot/web/lib/api.server";
export const { clientLoader, shouldRevalidate } = pageReuse<typeof loader>();

export async function loader({ request }: { request: Request }) {
  return cachedPage(60, { market: await apiGet<MarketResponse>("/api/finhot/market", { signal: request.signal }) });
}

export function meta() {
  return pageMeta({
    title: "盘面：涨停原因与信号战绩",
    description: `${SITE.name} 每个交易日收盘后整理：每只涨停股为什么涨停、依据是什么；本站精选里“利好 / 利空”判断之后几天的实际表现。`,
    path: "/market",
  });
}

const HORIZONS = [1, 3, 5] as const;
const CONFIDENCE: Record<LimitUpStock["confidence"], string> = {
  高: "bg-hot-soft text-hot",
  中: "bg-amber-soft text-amber-ink",
  低: "bg-accent-softer text-ink-4",
};

/** A 股习惯：涨红跌绿。 */
const tone = (v: number | null | undefined) => (v == null ? "text-ink-4" : v > 0 ? "text-hot" : v < 0 ? "text-ok" : "text-ink-3");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const clock = (hhmm: string) => (hhmm.length === 4 ? `${hhmm.slice(0, 2)}:${hhmm.slice(2)}` : "");

function Stock({ s }: { s: LimitUpStock }) {
  const ev = s.evidence.find((e) => e.kind === "公司" || e.kind === "盘面" || e.kind === "板块");
  return (
    <li className="grid gap-1.5 border-b border-line-soft py-3.5 last:border-0 sm:grid-cols-[13rem_minmax(0,1fr)] sm:gap-5">
      <div className="flex items-baseline gap-2 sm:flex-col sm:gap-0.5">
        <span className="text-[15px] font-semibold text-ink">{s.name}</span>
        <span className="mono whitespace-nowrap text-[12px] text-ink-4">
          {s.code} · {s.boards > 1 ? `${s.boards} 连板` : "首板"}
          {clock(s.first_time) && ` · ${clock(s.first_time)} 封板`}
        </span>
      </div>
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[14.5px] leading-[1.6] text-ink-2">
          <span className={`shrink-0 rounded px-1.5 py-px text-[11.5px] font-semibold ${CONFIDENCE[s.confidence]}`}>{s.confidence}</span>
          <span className="min-w-0">{s.reason}</span>
        </p>
        {ev ? (
          <p className="mt-1 line-clamp-2 text-[12.5px] leading-[1.6] text-ink-4">
            {ev.url ? (
              <a href={ev.url} target="_blank" rel="noopener noreferrer nofollow" className="hover:text-accent">
                {ev.text}
              </a>
            ) : (
              ev.text
            )}
            {ev.source && <span className="ml-1.5 text-ink-4/80">— {ev.source}</span>}
          </p>
        ) : s.evidence.some((e) => e.kind === "同花顺") ? (
          <p className="mt-1 text-[12.5px] text-ink-4">依据：同花顺涨停原因</p>
        ) : null}
      </div>
    </li>
  );
}

function LimitUps({ day, updated }: { day: LimitUpDay; updated: string | null }) {
  const b = day.breadth ?? {};
  const facts = [
    b.zt != null && `涨停 ${b.zt}`,
    b.dt != null && `跌停 ${b.dt}`,
    b.zb != null && `炸板 ${b.zb}`,
    b.seal_rate != null && `封板率 ${b.seal_rate}%`,
    `有依据 ${Math.round(day.coverage)}%`,
  ].filter(Boolean);
  return (
    <section className="card rounded-sheet px-5 py-5 sm:px-7" aria-labelledby="limitups">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="limitups" className="text-[18px] font-bold text-ink">
          涨停原因 <span className="num text-[15px] font-semibold text-ink-3">{day.trade_date}</span>
        </h2>
        {updated && <span className="text-[12px] text-ink-4"><span className="num">{monthDayTime(updated)}</span> 更新</span>}
      </div>
      <p className="mt-1.5 text-[13px] text-ink-3">{facts.join(" · ")}</p>
      {day.ladder?.length > 0 && (
        <p className="mt-2 text-[13px] leading-[1.7] text-ink-3">
          <span className="font-semibold text-ink-2">连板梯队</span>{" "}
          {day.ladder.map((r) => `${r.boards}板 ${r.stocks.slice(0, 4).join("、")}${r.stocks.length > 4 ? "等" : ""}`).join("；")}
        </p>
      )}
      <p className="mt-2 text-[12px] leading-[1.6] text-ink-4">
        可信度：高 = 公司自身的消息（业绩、订单、重组、获批……）；中 = 收评点名、同花顺题材标签或领涨概念；低 = 只按行业推断。
        公司澄清、异常波动等提示写在括号里。
      </p>
      <ul className="mt-2">
        {day.stocks.map((s) => (
          <Stock key={s.code} s={s} />
        ))}
      </ul>
    </section>
  );
}

function Cell({ row, prefix, k }: { row: RecordRow; prefix: "reaction" | "tradable"; k: number }) {
  const n = row[`${prefix}_${k}_n`] as number | null;
  const mean = row[`${prefix}_${k}_mean`] as number | null;
  const win = row[`${prefix}_${k}_win`] as number | null;
  if (!n) return <td className="px-2 py-2.5 text-right text-ink-4 sm:px-3">—</td>;
  // 次数和这一行的样本数一样时不重复写（还没到期的信号会让 T+3、T+5 的次数少一些）
  return (
    <td className="px-2 py-2.5 text-right sm:px-3">
      <span className={`num font-semibold ${tone(mean)}`}>{pct(mean)}</span>
      <span className="block whitespace-nowrap text-[11.5px] text-ink-4">
        {win != null && <>胜率 <span className="num">{win}%</span></>}
        {n !== row.n && <>{win != null && " · "}<span className="num">{n}</span> 次</>}
      </span>
    </td>
  );
}

function RecordTable({ rows, prefix, title, note }: { rows: RecordRow[]; prefix: "reaction" | "tradable"; title: string; note: string }) {
  return (
    <div className="mt-5">
      <h3 className="text-[14px] font-semibold text-ink-2">{title}</h3>
      <p className="mt-0.5 text-[12px] text-ink-4">{note}</p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[21rem] text-[13px] sm:max-w-[46rem]">
          <thead>
            <tr className="border-b border-line-soft text-[12px] text-ink-4">
              <th className="px-2 py-2 text-left font-medium sm:px-3">信号</th>
              <th className="px-2 py-2 text-right font-medium sm:px-3">样本</th>
              {HORIZONS.map((k) => (
                <th key={k} className="px-2 py-2 text-right font-medium sm:px-3">T+{k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.kind} className="border-b border-line-soft last:border-0">
                <td className="px-2 py-2.5 text-ink-2 sm:px-3">{r.kind}</td>
                <td className="num px-2 py-2.5 text-right text-ink-3 sm:px-3">{r.n}</td>
                {HORIZONS.map((k) => (
                  <Cell key={k} row={r} prefix={prefix} k={k} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Record({ record, updated }: { record: RecordData; updated: string | null }) {
  return (
    <section className="card rounded-sheet px-5 py-5 sm:px-7" aria-labelledby="record">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="record" className="text-[18px] font-bold text-ink">信号战绩</h2>
        {updated && <span className="text-[12px] text-ink-4"><span className="num">{monthDayTime(updated)}</span> 更新</span>}
      </div>
      <p className="mt-1.5 max-w-[48em] text-[13px] leading-[1.7] text-ink-3">
        {record.since} 起的信号，对应题材（几个东方财富板块的中位数）相对{record.benchmark}的超额收益，涨红跌绿。
        “精选利好 / 精选利空”是本站精选里 A股影响写了利好、利空的条目；胜率是方向说对的比例。样本少时波动很大，仅供参考，不构成投资建议。
      </p>
      <RecordTable rows={record.rows} prefix="reaction" title="反应口径" note="信号前最后一个收盘 → 第 N 个交易日收盘，含开盘跳空。" />
      <RecordTable rows={record.rows} prefix="tradable" title="可交易口径" note="信号后第一个开盘买入 → 第 N 个交易日收盘。" />
      {record.themes.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[14px] font-semibold text-ink-2">利好兑现得好的题材（反应口径 T+3）</h3>
          <p className="mt-1.5 flex flex-wrap gap-2 text-[13px]">
            {record.themes.map((t) => (
              <span key={t.theme} className="rounded-full bg-accent-softer px-2.5 py-0.5 text-ink-2">
                {t.theme} <span className={`num font-semibold ${tone(t.mean)}`}>{pct(t.mean)}</span>
                <span className="text-ink-4"> · {t.n} 次</span>
              </span>
            ))}
          </p>
        </div>
      )}
      {record.recent.length > 0 && (
        <div className="mt-5">
          <h3 className="text-[14px] font-semibold text-ink-2">最近的信号</h3>
          <ul className="mt-1">
            {record.recent.map((s, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-line-soft py-2.5 text-[13px] last:border-0">
                <span className="num w-[6.5rem] shrink-0 text-ink-4">{monthDayTime(s.at)}</span>
                <span className="shrink-0 text-ink-3">{s.kind} · {s.theme}</span>
                <span className="min-w-0 flex-[1_1_14rem] text-ink-2">{s.title}{s.count > 1 ? `（${s.count} 条）` : ""}</span>
                <span className="ml-auto shrink-0 text-[12px] text-ink-4">
                  T+1 <span className={`num font-semibold ${tone(s.reaction["1"])}`}>{pct(s.reaction["1"])}</span>
                  {"  "}T+3 <span className={`num font-semibold ${tone(s.reaction["3"])}`}>{pct(s.reaction["3"])}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default function MarketPage() {
  const { market } = useLoaderData<typeof loader>();
  const day = market.limitup;
  return (
    <div className="space-y-4 pb-10">
      <PhoneBar title="盘面" large sub={day ? <>{day.trade_date} · 涨停 {day.stocks.length} 家</> : "每个交易日收盘后更新"} />
      <header className="hidden pb-1 pt-1 lg:block">
        <div className="text-[12px] font-semibold tracking-[0.08em] text-hot">A 股盘面</div>
        <h1 className="mt-1.5 text-[24px] font-bold leading-[1.3] tracking-[-0.01em] text-ink lg:text-[26px]">涨停原因与信号战绩</h1>
        <p className="mt-1.5 text-[13.5px] text-ink-3">每个交易日收盘后更新：每只涨停股为什么涨停、依据是什么；精选里的利好利空之后几天兑现得怎么样。</p>
      </header>
      {day ? (
        <LimitUps day={day} updated={market.updatedAt.limitup} />
      ) : (
        <div className="card rounded-sheet">
          <EmptyState title="还没有涨停数据">交易日收盘后（约 15:30）推送进程会把当天的涨停原因推过来。</EmptyState>
        </div>
      )}
      {market.record && market.record.rows.length > 0 && <Record record={market.record} updated={market.updatedAt.record} />}
    </div>
  );
}
