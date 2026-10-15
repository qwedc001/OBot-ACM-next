export function bindingOptions(excludeUid, excludeBefore, showUnrated) {
  const tokens = excludeUid.trim() ? excludeUid.trim().split(/[\s,，]+/) : [];
  const ids = tokens.map(Number);
  if (
    tokens.length > 100 ||
    tokens.some((value) => !/^\d+$/.test(value)) ||
    ids.some((value) => !Number.isSafeInteger(value))
  )
    throw new Error("排除 UID 请填写最多 100 个非负整数，用逗号或空格分隔。");
  const day = excludeBefore || "1970-01-01";
  const date = new Date(day + "T00:00:00Z");
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(day) ||
    Number.isNaN(date.getTime()) ||
    date.toISOString().slice(0, 10) !== day
  )
    throw new Error("请选择有效日期。");
  return {
    exclude_uid: [...new Set(ids)],
    exclude_before: day,
    show_unrated: Boolean(showUnrated),
  };
}
