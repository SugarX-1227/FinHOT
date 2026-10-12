// 盘面数据：只有带推送 token 的才能写；格式不对的不收；读者读到每类最新的一份。
process.env.INGEST_TOKEN = "market-module-test-ingest-token";
import "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { installModules } from "@aihot/backend/modules";
import { buildApp } from "../../../apps/api/src/app.ts";
import market from "../server.ts";

installModules([market]);
const app = await buildApp();
after(async () => { await app.close(); await closeDb(); });

const auth = { authorization: `Bearer ${process.env.INGEST_TOKEN}` };
const post = (payload: unknown, headers: Record<string, string> = auth) =>
  app.inject({ method: "POST", url: "/api/finhot/market", headers, payload: payload as object });

test("only the push token writes, and malformed reports are refused", async () => {
  const day = { trade_date: "2026-10-09", coverage: 50, breadth: { zt: 1 }, ladder: [], stocks: [] };
  assert.equal((await post({ kind: "limitup", key: "2026-10-09", data: day }, {})).statusCode, 401);
  assert.equal((await post({ kind: "limitup", key: "2026-10-09", data: day }, { authorization: "Bearer wrong-token-0000000000" })).statusCode, 401);
  for (const bad of [{ kind: "other", key: "2026-10-09", data: day }, { kind: "limitup", key: "../x", data: day }, { kind: "limitup", key: "2026-10-09", data: [1] }]) {
    assert.equal((await post(bad)).statusCode, 400, JSON.stringify(bad));
  }
  assert.equal((await sql`SELECT count(*)::int AS n FROM finhot_market`)[0]!.n, 0);
});

test("readers get the newest report of each kind; a re-push replaces that day's", async () => {
  const empty = (await app.inject({ method: "GET", url: "/api/finhot/market" })).json();
  assert.deepEqual(empty, { limitup: null, record: null, updatedAt: { limitup: null, record: null } });
  for (const d of ["2026-10-09", "2026-10-12", "2026-10-10"]) {
    assert.equal((await post({ kind: "limitup", key: d, data: { trade_date: d, stocks: [{ name: "旧" }] } })).statusCode, 200);
  }
  await post({ kind: "limitup", key: "2026-10-12", data: { trade_date: "2026-10-12", stocks: [{ name: "新" }] } });
  await post({ kind: "record", key: "2026-10-12", data: { rows: [{ kind: "精选利好", n: 3 }] } });
  const res = await app.inject({ method: "GET", url: "/api/finhot/market" });
  assert.equal(res.headers["cache-control"], "public, max-age=60");
  const body = res.json();
  assert.equal(body.limitup.trade_date, "2026-10-12");
  assert.deepEqual(body.limitup.stocks, [{ name: "新" }]);
  assert.equal(body.record.rows[0].kind, "精选利好");
  assert.ok(body.updatedAt.limitup && body.updatedAt.record);
});
