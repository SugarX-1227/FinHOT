// 这个行业的分类体系：类别、标签词表、公司（主体）名录，以及防止张冠李戴的身份词典。
// 模型按这里的词表打标签，主题页（topics.json）按标签归类，筛选栏按类别分组。
// 换行业时：类别的 key 会出现在网址里（/all?category=…），上线后就不要再改；标签和名录可以随时增减。

/**
 * 网页上的类别（筛选栏、卡片角标、RSS 分类订阅）。key 是网址和接口里的身份，上线后不要改。
 * section 是日报里的分节标题（几个类别可以共用一节，按这里的顺序排）；guide 告诉结构抽取模型这一类收什么、
 * 和相邻类别的边界在哪（总的归类原则写在 prompts/structure.md 里）。
 * commentary 标出评论类（教程、观点）：日报写过的事又有评论类的后续报道，只占一行快讯（报道它的信源够多时除外）。
 * 没归上类的资料在日报里放进第一个 key 为 industry 的类别所在的节（没有就放最后一节）。
 * feedLabel 是分类 RSS 标题里的名字（不写就用 label）。公开接口、RSS 和 MCP 里要把一类并进另一类发布，写在站点设置里（site/site.ts 的 PUBLIC_CATEGORIES）。
 */
export const CATEGORIES = [
  { key: "policy", label: "政策", feedLabel: "政策与监管", section: "政策与监管", guide: "国内已出台或正在征求意见的政策、法规、监管动作与表态：国务院与部委、证监会与交易所规则、地方政策、产业政策文件。央行的货币政策操作归宏观；海外政府的政策归海外。" },
  { key: "macro", label: "宏观", feedLabel: "宏观与数据", section: "宏观与数据", guide: "国内宏观经济与金融数据、央行货币政策与公开市场操作、利率汇率、财政收支与国债发行。只是例行操作且无变化时仍归宏观。海外宏观数据与海外央行归海外。" },
  { key: "global", label: "海外", feedLabel: "海外市场", section: "海外市场", guide: "海外央行与政府、海外宏观数据、海外股债汇商品市场、地缘政治与国际冲突、海外公司（含中概股在海外的经营事项）。中国对外的贸易反制、外交回应归政策。" },
  { key: "industry", label: "行业", feedLabel: "行业与产业", section: "行业与产业", guide: "某个行业整体的变化：产品价格与供需、产能、技术突破、行业数据与景气、行业会议与标准。只涉及一家上市公司的经营事项归公司。" },
  { key: "company", label: "公司", feedLabel: "公司动态", section: "公司动态", guide: "单家公司（以 A 股、港股上市公司为主）的公告与经营事项：业绩、并购重组、增减持与回购、订单合同、产品发布、人事、诉讼与监管处罚、互动平台的实质回复。" },
  { key: "market", label: "市场", feedLabel: "市场与资金", section: "市场与资金", guide: "A 股与港股市场本身：指数与板块表现、成交额、资金流向（主力、两融、南向）、新股发行上市、市场制度运行。盘中个股涨跌播报如果没有原因说明，也归这里。" },
  { key: "opinion", label: "观点", feedLabel: "机构观点", section: "机构观点", guide: "机构、分析师、经济学家、官员以外人士的判断、预测与解读，研报观点与策略展望。官员的政策表态归政策或宏观；报道里只顺带引用一句评论，不改变原本类别。", commentary: true },
] as const satisfies ReadonlyArray<{ key: string; label: string; feedLabel?: string; section: string; guide: string; commentary?: true }>;

/**
 * 这个行业最受关注的一类发布（AI 行业是新模型）：日报报头的“N 个新模型”、改分类后修订已出的报告都按它数。
 * category 是类别，tag 是标签，两者都对上才算；unit 接在数字后面。
 * 没有这样一类的行业设成 null，报头就不显示这个数。
 */
export const RELEASE: { category: string; tag: string; unit: string } | null = null;

/** 周报月报的总述可以直接写、不必在报道里找到出处的行业通用词（小写）。站名会自动算进去。 */
export const PLAIN_TERMS: readonly string[] = ["a股", "港股", "美股", "gdp", "cpi", "ppi", "pmi", "lpr", "mlf", "ipo", "etf", "ai", "ceo"];

/**
 * 内容理解一步给每篇资料判的“内容类型”（写在 prompts/content-understanding.md 里，改了类型要同步改那份提示词）。
 * 评分提示词（prompts/selection-score.md）按类型给五个维度不同的权重。
 */
export const ITEM_TYPES = ["policy_release", "macro_data", "corporate_event", "industry_development", "market_move", "overseas_event", "opinion_analysis"] as const;

// ── 标签词表 ────────────────────────────────────────────────────────────────────────────

/** 每篇资料的第一个标签必须是这些“分类标签”之一。 */
export const CATEGORY_TAGS = [
  "货币政策", "财政政策", "产业政策", "资本市场监管", "经济数据", "海外宏观", "地缘政治", "业绩", "并购重组", "增减持回购",
  "订单合同", "产品技术", "价格供需", "资金流向", "市场行情", "机构观点", "其他",
] as const;

/** 可选的主题标签：A 股题材（和 FinHOT 的题材词典 finhot/themes.py 保持一致）。 */
export const TOPIC_TAGS = [
  "人工智能", "算力", "半导体", "消费电子", "通信", "机器人", "汽车", "锂电储能", "光伏", "风电", "电力", "油气", "煤炭",
  "有色金属", "黄金", "稀土", "钢铁", "化工", "军工", "低空经济", "商业航天", "医药", "券商", "银行", "保险", "房地产",
  "基建建材", "消费", "文旅", "农业", "传媒游戏", "航运物流", "数字经济", "前沿科技", "贸易摩擦",
] as const;

/** 可选的实体标签：最常出现的政策与监管主体。 */
export const ENTITY_TAGS = ["国务院", "人民银行", "证监会", "金融监管总局", "发改委", "财政部", "商务部", "美联储", "欧洲央行", "日本央行"] as const;

/** 模型常写的近义词，统一成词表里的写法。 */
export const TAG_SYNONYMS: Readonly<Record<string, string>> = {
  降准: "货币政策", 降息: "货币政策", 加息: "货币政策", 逆回购: "货币政策", 公开市场操作: "货币政策", 央行: "货币政策",
  专项债: "财政政策", 国债: "财政政策", 特别国债: "财政政策", 财政: "财政政策", 税收: "财政政策",
  政策: "产业政策", 规划: "产业政策", 补贴: "产业政策", 以旧换新: "产业政策",
  监管: "资本市场监管", 证监会: "资本市场监管", 交易所: "资本市场监管", 处罚: "资本市场监管", 立案: "资本市场监管",
  数据: "经济数据", 宏观数据: "经济数据", PMI: "经济数据", CPI: "经济数据", PPI: "经济数据", GDP: "经济数据", 社融: "经济数据",
  海外: "海外宏观", 美联储议息: "海外宏观", 非农: "海外宏观", 美股: "海外宏观",
  地缘: "地缘政治", 冲突: "地缘政治", 制裁: "地缘政治", 关税: "地缘政治",
  财报: "业绩", 业绩预告: "业绩", 净利润: "业绩", 营收: "业绩",
  并购: "并购重组", 收购: "并购重组", 重组: "并购重组", 资产注入: "并购重组",
  增持: "增减持回购", 减持: "增减持回购", 回购: "增减持回购",
  订单: "订单合同", 中标: "订单合同", 合同: "订单合同",
  新品: "产品技术", 技术: "产品技术", 产品发布: "产品技术", 研发: "产品技术",
  涨价: "价格供需", 降价: "价格供需", 价格: "价格供需", 供需: "价格供需", 产能: "价格供需", 库存: "价格供需",
  资金: "资金流向", 北向: "资金流向", 南向: "资金流向", 两融: "资金流向", 主力资金: "资金流向",
  行情: "市场行情", 收评: "市场行情", 涨停: "市场行情", 板块: "市场行情", 指数: "市场行情",
  观点: "机构观点", 研报: "机构观点", 策略: "机构观点", 预测: "机构观点",
  AI: "人工智能", 大模型: "人工智能", 芯片: "半导体", 存储: "半导体", 光模块: "算力", 数据中心: "算力",
  新能源车: "汽车", 电池: "锂电储能", 储能: "锂电储能", 固态电池: "锂电储能", 原油: "油气", 天然气: "油气",
  铜: "有色金属", 铝: "有色金属", 白银: "黄金", 贵金属: "黄金", 证券: "券商", 地产: "房地产", 楼市: "房地产",
  游戏: "传媒游戏", 影视: "传媒游戏", 航运: "航运物流", 军工装备: "军工",
};

// ── 公司与主体 ──────────────────────────────────────────────────────────────────────────

/**
 * 公司主题：id → 显示名、卡片上显示的标签（null 表示只用 entity:<id> 归类）、别名。
 * aliases 给结构抽取模型看；otherNames 是公司自己的其他称呼（官方账号名、子品牌），
 * 把事实的主体对到发布方时也认它们。
 * 起步名单：A 股权重与高关注度公司，以及常影响 A 股的海外公司；随时增减。
 */
export const ENTITIES: Record<string, { name: string; displayTag: string | null; aliases: string[]; otherNames?: string[] }> = {
  catl: { name: "宁德时代", displayTag: "宁德时代", aliases: ["宁德时代", "CATL"] },
  byd: { name: "比亚迪", displayTag: "比亚迪", aliases: ["比亚迪", "BYD"] },
  moutai: { name: "贵州茅台", displayTag: null, aliases: ["贵州茅台", "茅台"], otherNames: ["i茅台"] },
  smic: { name: "中芯国际", displayTag: null, aliases: ["中芯国际", "SMIC"] },
  huawei: { name: "华为", displayTag: "华为", aliases: ["华为", "Huawei", "鸿蒙", "昇腾"] },
  xiaomi: { name: "小米", displayTag: null, aliases: ["小米", "Xiaomi"] },
  tencent: { name: "腾讯", displayTag: null, aliases: ["腾讯", "Tencent"] },
  alibaba: { name: "阿里巴巴", displayTag: null, aliases: ["阿里巴巴", "阿里", "Alibaba"], otherNames: ["阿里云", "淘天", "通义千问"] },
  bytedance: { name: "字节跳动", displayTag: null, aliases: ["字节跳动", "字节", "ByteDance", "抖音", "豆包"] },
  "ping-an": { name: "中国平安", displayTag: null, aliases: ["中国平安", "平安集团"] },
  cmb: { name: "招商银行", displayTag: null, aliases: ["招商银行", "招行"] },
  "industrial-fii": { name: "工业富联", displayTag: null, aliases: ["工业富联", "富士康", "鸿海"] },
  cambricon: { name: "寒武纪", displayTag: null, aliases: ["寒武纪"] },
  nvidia: { name: "英伟达", displayTag: "英伟达", aliases: ["英伟达", "NVIDIA", "Nvidia"] },
  apple: { name: "苹果", displayTag: null, aliases: ["苹果公司", "Apple", "iPhone"] },
  tesla: { name: "特斯拉", displayTag: null, aliases: ["特斯拉", "Tesla"] },
  tsmc: { name: "台积电", displayTag: null, aliases: ["台积电", "TSMC"] },
};

/**
 * 身份词典：摘要和标题里出现的公司，必须在原文里也出现过，否则退回原标题、丢掉摘要（防止模型张冠李戴）。
 * 行业没有这个问题时可以留空数组。
 */
export const IDENTITY_LEXICON: ReadonlyArray<{ id: string; name: string; patterns: RegExp[] }> = [
  { id: "catl", name: "宁德时代", patterns: [/宁德时代|\bCATL\b/i] },
  { id: "byd", name: "比亚迪", patterns: [/比亚迪|\bBYD\b/i] },
  { id: "moutai", name: "贵州茅台", patterns: [/茅台/] },
  { id: "smic", name: "中芯国际", patterns: [/中芯国际|\bSMIC\b/i] },
  { id: "huawei", name: "华为", patterns: [/华为|\bhuawei\b|鸿蒙|昇腾/i] },
  { id: "xiaomi", name: "小米", patterns: [/小米|\bxiaomi\b/i] },
  { id: "tencent", name: "腾讯", patterns: [/腾讯|\btencent\b/i] },
  { id: "alibaba", name: "阿里巴巴", patterns: [/阿里巴巴|阿里云|\balibaba\b|淘天/i] },
  { id: "bytedance", name: "字节跳动", patterns: [/字节跳动|\bbytedance\b|抖音|豆包/i] },
  { id: "ping-an", name: "中国平安", patterns: [/中国平安/] },
  { id: "cmb", name: "招商银行", patterns: [/招商银行|招行/] },
  { id: "industrial-fii", name: "工业富联", patterns: [/工业富联|富士康|鸿海|\bfoxconn\b/i] },
  { id: "cambricon", name: "寒武纪", patterns: [/寒武纪/] },
  { id: "nvidia", name: "英伟达", patterns: [/英伟达|\bnvidia\b/i] },
  { id: "apple", name: "苹果", patterns: [/苹果公司|\bapple\b|iphone/i] },
  { id: "tesla", name: "特斯拉", patterns: [/特斯拉|\btesla\b/i] },
  { id: "tsmc", name: "台积电", patterns: [/台积电|\btsmc\b/i] },
];

/** 这些域名上的文章，发布方就是对应的公司（托管平台如 GitHub、arXiv 不算）。本站信源都是媒体与政府网站，暂不需要。 */
export const PUBLISHER_DOMAINS: ReadonlyArray<{ entityId: string; domains: readonly string[] }> = [];

/** 原文里的这些写法也算提到了对应公司。 */
export const IDENTITY_CONTEXT_ALIASES: ReadonlyArray<{ entityId: string; pattern: RegExp }> = [];
