// 盘面数据的收发：推送进程用 INGEST_TOKEN 推（和快讯推送同一个 token），读者读最新的一份。
import { timingSafeEqual } from "node:crypto";
import type { FastifyRequest } from "fastify";
import { credential } from "@aihot/backend/config";
import { sql } from "@aihot/backend/db";
import { defineServerModule } from "@aihot/backend/modules";
import { MARKET_KINDS, type MarketResponse } from "./types.ts";

function authorized(req: FastifyRequest): boolean {
  const expected = credential("auth", "INGEST_TOKEN") ?? "";
  if (expected.length < 16) return false;
  const given = /^Bearer\s+(.+)$/i.exec(req.headers.authorization ?? "")?.[1]?.trim() ?? "";
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export default defineServerModule({
  name: "market",
  http(app) {
    app.post("/api/finhot/market", { bodyLimit: 4 * 1024 * 1024 }, async (req, reply) => {
      reply.header("Cache-Control", "no-store");
      if (!authorized(req)) return reply.code(401).type("text/plain; charset=utf-8").send("Unauthorized");
      const body = (req.body ?? {}) as { kind?: unknown; key?: unknown; data?: unknown };
      const kind = typeof body.kind === "string" && (MARKET_KINDS as readonly string[]).includes(body.kind) ? body.kind : null;
      const key = typeof body.key === "string" && /^[\w-]{1,40}$/.test(body.key) ? body.key : null;
      if (!kind || !key || !body.data || typeof body.data !== "object" || Array.isArray(body.data)) {
        return reply.code(400).send({ error: `kind（${MARKET_KINDS.join(" / ")}）、key（日期）和 data（对象）都要有` });
      }
      await sql`
        INSERT INTO finhot_market (kind, key, data, updated_at) VALUES (${kind}, ${key}, ${sql.json(body.data as never)}, now())
        ON CONFLICT (kind, key) DO UPDATE SET data = EXCLUDED.data, updated_at = now()`;
      return { ok: true };
    });

    app.get("/api/finhot/market", async (_req, reply) => {
      const rows = await sql<{ kind: string; data: unknown; updated_at: Date }[]>`
        SELECT DISTINCT ON (kind) kind, data, updated_at FROM finhot_market ORDER BY kind, key DESC`;
      const by = new Map(rows.map((r) => [r.kind, r]));
      const out: MarketResponse = {
        limitup: (by.get("limitup")?.data ?? null) as MarketResponse["limitup"],
        record: (by.get("record")?.data ?? null) as MarketResponse["record"],
        updatedAt: {
          limitup: by.get("limitup")?.updated_at.toISOString() ?? null,
          record: by.get("record")?.updated_at.toISOString() ?? null,
        },
      };
      reply.header("Cache-Control", "public, max-age=60");
      return out;
    });
  },
});
