/** 时间戳（秒）格式化。 */
export function formatTime(seconds, withSeconds = false) {
  if (!seconds) return "—";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "—";

  const pad = (value) => String(value).padStart(2, "0");
  const base =
    `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return withSeconds ? `${base}:${pad(date.getSeconds())}` : base;
}

/** 相对时间。 */
export function fromNow(seconds) {
  if (!seconds) return "—";
  const diff = Date.now() / 1000 - seconds;
  if (diff < 0) return formatTime(seconds);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return formatTime(seconds);
}

export function truncate(text, length = 60) {
  const value = String(text ?? "");
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

/**
 * 状态文案。用户侧只区分「等待审核 / 已生效 / 未通过」三种说法，
 * 内部状态名（pending/approved/conflict/applied/rejected）不暴露给普通用户。
 */
export const STATUS_META = {
  pending: { label: "等待审核", short: "待审核", type: "warning" },
  approved: {
    label: "审核通过，等待管理员下发",
    short: "待下发",
    type: "primary",
  },
  conflict: {
    label: "与最新数据冲突，待管理员处理",
    short: "待处理",
    type: "danger",
  },
  applied: { label: "已生效", short: "已生效", type: "success" },
  rejected: { label: "未通过", short: "未通过", type: "info" },
};

export function statusMeta(status) {
  return STATUS_META[status] ?? { label: status, short: status, type: "info" };
}

export const FIELD_LABELS = {
  ocr_text: "图片描述",
  likes: "点赞",
  comments: "评论",
  id: "显示名",
  keys: "别名",
};

export function fieldLabel(field) {
  return FIELD_LABELS[field] ?? field;
}

/** 把任意值渲染成单行文本，用于对比展示。 */
export function renderValue(value) {
  if (value === null || value === undefined) return "（空）";
  if (Array.isArray(value)) return value.length ? value.join("、") : "（空）";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  return text === "" ? "（空）" : text;
}

/** 点赞提交里存的是「加多少」，展示成 +3。 */
export function renderLikeDelta(delta) {
  return `+${Number(delta)}`;
}
