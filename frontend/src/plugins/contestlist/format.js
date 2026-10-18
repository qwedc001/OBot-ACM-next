/**
 * 算法竞赛列表自己的界面词汇。
 *
 * 通用的那些（时间、相对时间、状态文案、字段名）直接用框架的
 * `@/utils/format`（PickOne 也是这么做的），这里只放本工具特有的东西：
 * 比赛阶段、比赛字段的中文名、字段值的单行渲染。
 */

/**
 * 比赛处在哪个阶段。
 *
 * 后端按上游 `DynamicContest.get_phase()` 的规则算好 `phase` / `phase_label`，
 * 这里只负责给它配一个 chip 颜色 —— 不重复算一遍时间，免得两边判断不一致。
 */
export const PHASE_META = {
  upcoming: { chip: "ep-chip--accent" },
  running: { chip: "ep-chip--ok" },
  ended: { chip: "" },
};

export function phaseChip(phase) {
  return (PHASE_META[phase] ?? { chip: "" }).chip;
}

/** 比赛字段的中文名（与后端 types.FIELD_LABELS 一一对应） */
export const CONTEST_FIELD_LABELS = {
  platform: "平台",
  abbr: "简称",
  name: "比赛全称",
  start_time: "开始时间",
  duration: "时长",
  supplement: "比赛地点",
};

/** 字段顺序（与后端 types.FIELD_ORDER 一致）：表单、对比、在途改动都按它排 */
export const FIELD_ORDER = [
  "platform",
  "abbr",
  "name",
  "start_time",
  "duration",
  "supplement",
];

export function contestFieldLabel(name) {
  return CONTEST_FIELD_LABELS[name] ?? name;
}

/**
 * 一条在途改动里某个字段的新值；这条提交单不管这个字段时返回 undefined。
 *
 * 「审核中的改动」这个块只列修改类的提交单，而修改一定是单字段：
 * `value` 就是那个字段的新值，`field` 是字段名。
 */
export function pendingFieldValue(item, name) {
  if (!item?.field) return undefined;
  return item.field === name ? item.value : undefined;
}

/**
 * 表格里的 ID 只显示前 6 位。
 *
 * 提交单 id 是自增整数，长了以后一列全是数字、还占宽度；`#000123` 这种写法
 * 在列表里也扫不出差别。悬停时给完整值（表格列自己挂 title）。
 */
export const SHORT_ID_LENGTH = 6;

export function shortId(id) {
  const text = String(id ?? "");
  return text.length > SHORT_ID_LENGTH ? text.slice(0, SHORT_ID_LENGTH) : text;
}

/**
 * `ICPC · 济南区域赛` —— 列表和提交单里指代一场比赛的统一写法。
 *
 * 与后端 `Contest.badge()` 同一套规则（简称缺失时退回名称）。前端需要它的地方
 * 是「还没写进文件的草稿」（那样的条目后端算不出 badge），别的地方直接用
 * 接口返回的 `badge` / `target`，避免两处各拼一遍。
 */
export function contestBadge(value) {
  const source = value ?? {};
  const platform = String(source.platform ?? "").trim();
  const abbr =
    String(source.abbr ?? "").trim() || String(source.name ?? "").trim();
  if (platform && abbr) return `${platform} · ${abbr}`;
  return platform || abbr || "（未命名）";
}

/**
 * 持续秒数 -> `5 小时`（整小时）/ `5 小时 30 分钟`。
 *
 * 放在本插件里而不是 `@/utils/format`：字段值在表格里占的宽度是有意义的，
 * 「18000」比「5 小时」难读得多，但只有录比赛的工具需要这个说法。
 */
export function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return "—";
  const minutes = Math.round(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} 分钟`;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}
