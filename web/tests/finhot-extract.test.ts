// FinHOT: without a Jina key a page that cannot be read gives no body at once (the article is judged on its
// excerpt), instead of an error that retries three times first.
import "./setup.ts";
import assert from "node:assert/strict";
import http from "node:http";
import { after, test } from "node:test";
import { closeDb } from "@aihot/backend/db";
import { extractFromUrl } from "@aihot/backend/content/extract";

const page = http.createServer((_req, res) => { res.writeHead(403); res.end("blocked"); });
await new Promise<void>(resolve => page.listen(0, "127.0.0.1", resolve));
after(async () => { page.close(); await closeDb(); });

test("a page that cannot be read and no Jina key: no body, no error", async () => {
  delete process.env.JINA_API_KEY;
  const url = `http://127.0.0.1:${(page.address() as { port: number }).port}/article`;
  assert.equal(await extractFromUrl(url, "article:finhot-extract"), null);
});
