// 盘面页的入口：电脑侧栏“A股”一节、手机底栏一个标签。
import { defineWebModule } from "@aihot/web/modules";
import { IconChart } from "@aihot/web/components/icons";

export default defineWebModule({
  name: "market",
  sidebar: { section: "A股", items: [{ to: "/market", label: "盘面", icon: IconChart }] },
  tabs: [{ key: "market", to: "/market", label: "盘面", icon: IconChart }],
});
