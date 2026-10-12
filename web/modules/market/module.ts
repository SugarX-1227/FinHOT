// FinHOT 盘面：涨停原因与信号战绩。数据由 FinHOT 的推送进程在服务器上算好后推过来（POST /api/finhot/market），
// 这里只存最新的几份并展示，不调用模型、不抓行情。
import { defineModule } from "@aihot/contracts/modules";

export default defineModule({
  name: "market",
  pages: [{ path: "market", file: "web/market-page.tsx" }],
  apiPaths: [/^\/api\/finhot\//],
});
