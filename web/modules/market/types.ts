// FinHOT 推送进程推来的数据（finhot/market_job.py），页面和接口共用。

export interface Evidence { kind: string; text: string; source: string; url: string }

export interface LimitUpStock {
  code: string;
  name: string;
  boards: number;
  first_time: string;
  industry: string;
  reason: string;
  confidence: "高" | "中" | "低";
  themes: string[];
  evidence: Evidence[];
  written_by: string;
}

export interface LimitUpDay {
  trade_date: string;
  coverage: number;
  breadth: { zt?: number; dt?: number; zb?: number; seal_rate?: number; up?: number; down?: number };
  ladder: Array<{ boards: number; stocks: string[] }>;
  stocks: LimitUpStock[];
}

/** 一类信号的汇总：反应 / 可交易口径各自 T+1/3/5 的样本数、平均超额（%）、胜率（%，不判方向的为 null）。 */
export interface RecordRow {
  kind: string;
  n: number;
  bullish: boolean | null;
  [field: string]: string | number | boolean | null;
}

export interface RecordSignal {
  at: string;
  kind: string;
  theme: string;
  title: string;
  count: number;
  reaction: Record<string, number | null>;
  tradable: Record<string, number | null>;
}

export interface RecordData {
  generated: string;
  since: string;
  benchmark: string;
  rows: RecordRow[];
  recent: RecordSignal[];
  themes: Array<{ theme: string; mean: number; n: number }>;
}

export interface MarketResponse {
  limitup: LimitUpDay | null;
  record: RecordData | null;
  updatedAt: { limitup: string | null; record: string | null };
}

export const MARKET_KINDS = ["limitup", "record"] as const;
