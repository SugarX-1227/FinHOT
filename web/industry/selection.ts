// 精选的门槛。评分标准本身写在 prompts/selection-score.md；这里只决定“多少分算入选”。
// 每篇资料由评分模型独立打两次分（0–100），两次之和 ≥ 2 × 门槛才成为精选候选；
// 确认新闻身份与新增价值后，由发布规则决定展示（见 docs/selection.md），卡片上显示两次的平均分。
// 门槛按信源分级区分：官方一手信源的门槛低一些，媒体和个人的高一些。改了门槛或评分提示词，
// 用 scripts/eval-selection.ts 在你自己标注的样本上重跑一遍，再决定上线（见 docs/selection.md）。

/** .env 里的 SELECTION_T1 / SELECTION_T1_5 / SELECTION_T2 覆盖默认门槛，改完重启 api、worker 即可，不用重新构建。 */
function threshold(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 && value <= 100 ? value : fallback;
}

export const SELECTION = {
  /**
   * 信源分级 → 入选门槛（平均分）。分级在后台“信源”里给每个源设置：
   *   T1 官方一手（官网、官方博客、机构）· T1_5 官方账号、准官方创作者 · T2 媒体与个人
   * 分级 EXCLUDE_MP 以及这里没有列出的分级，不参与精选评分（只进“全部动态”）。
   *
   * FinHOT：快讯社（财联社、华尔街见闻、金十……）都是 T2，又是精选的主要来源；AIHOT 给 AI 媒体定的 76
   * 太严，上线头两天 T2 一条都没选上（俄乌“能源停火”这类大事两次平均也只有六十多分）。T2 先定 64，
   * 再用 scripts/finhot-selection.ts stats 按“每天想要几件精选”调。
   */
  thresholds: { T1: threshold("SELECTION_T1", 60), T1_5: threshold("SELECTION_T1_5", 62), T2: threshold("SELECTION_T2", 64) } as Record<string, number>,
  /**
   * 没入选、但平均分高于这个数的资料，也用精选的写法（内容理解：标题、摘要、推荐理由）来写，
   * 其余用更便宜的“标题摘要翻译”。
   */
  understandFloor: 50,
} as const;
