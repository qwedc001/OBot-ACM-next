import http from "@/api/http";

/** 本插件的后端前缀。后端由 `backend/web/plugins/pickone.py` 提供同样的 slug。 */
const BASE = "/plugins/pickone";

/** 缩略图/原图是 <img src>，不走 axios，所以这里直接拼 URL。 */
export const thumbUrl = (imgKey, name) =>
  `/api${BASE}/images/${encodeURIComponent(imgKey)}/thumb/${encodeURIComponent(name)}`;
export const rawUrl = (imgKey, name) =>
  `/api${BASE}/images/${encodeURIComponent(imgKey)}/raw/${encodeURIComponent(name)}`;
export const auditThumbUrl = (imgKey, name) =>
  `/api${BASE}/admin/audit/${encodeURIComponent(imgKey)}/thumb/${encodeURIComponent(name)}`;
export const auditRawUrl = (imgKey, name) =>
  `/api${BASE}/admin/audit/${encodeURIComponent(imgKey)}/raw/${encodeURIComponent(name)}`;

/**
 * PickOne 插件自己的接口。
 *
 * 注册表挂到 `api.pickone.*`，避免与其他工具的方法重名。
 */
const pickoneApi = {
  // ---- 类别 ----
  categories: () => http.get(`${BASE}/categories`),
  categorySummary: () => http.get(`${BASE}/categories/summary`),
  category: (imgKey) =>
    http.get(`${BASE}/categories/${encodeURIComponent(imgKey)}`),

  // ---- 图片 ----
  images: (imgKey, params) =>
    http.get(`${BASE}/images/${encodeURIComponent(imgKey)}`, { params }),
  imageStats: (imgKey) =>
    http.get(`${BASE}/images/${encodeURIComponent(imgKey)}/stats`),
  image: (imgKey, name) =>
    http.get(
      `${BASE}/images/${encodeURIComponent(imgKey)}/item/${encodeURIComponent(name)}`,
    ),
  lookupHashId: (imgKey, hashId) =>
    http.get(
      `${BASE}/images/${encodeURIComponent(imgKey)}/hash-id/${encodeURIComponent(hashId)}`,
    ),
  thumbUrl,
  rawUrl,
  auditThumbUrl,
  auditRawUrl,

  // ---- 提交 ----
  submissions: (params) => http.get(`${BASE}/submissions`, { params }),
  mySubmissions: () => http.get(`${BASE}/submissions/mine`),
  submit: (imgKey, type, payload) =>
    http.post(`${BASE}/submissions`, payload, {
      params: { img_key: imgKey, type },
    }),
  submitBatch: (imgKey, payload) =>
    http.post(`${BASE}/submissions/batch`, payload, {
      params: { img_key: imgKey },
    }),
  withdraw: (id) => http.delete(`${BASE}/submissions/${id}`),

  // ---- 管理：审核队列 + 本插件专属的写盘动作 ----
  /**
   * 审核队列（框架接口 `/api/plugins/curation/admin/queue` 的必要包装）。
   *
   * 队列本身是通用的（它只读提交单表），所以实现在框架里；这里只是把
   * `plugin=pickone` 注入进去，省得每个调用点都记得写。
   * 视图里请用 `api.pickone.reviewQueue()` —— 这个方法和框架的同名方法重名，
   */
  reviewQueue: (params) =>
    http.get("/plugins/curation/admin/queue", {
      params: { ...params, plugin: "pickone" },
    }),
  applyPreview: () => http.get(`${BASE}/admin/apply/preview`),
  /**
   * 真正写盘。不要再传参数：老签名是 `apply(dryRun)`，写成 `api.apply({})` 会让
   * dry_run 变成真值，结果只做了预览没写盘。要预览请用 applyPreview()。
   */
  apply: () => http.post(`${BASE}/admin/apply`, { dry_run: false }),
  conflicts: (params) => http.get(`${BASE}/admin/conflicts`, { params }),
  /**
   * 体检「已通过但已经不能直接下发」的提交单，把它们挂进「冲突待裁定」。
   *
   * 冲突判定平时只在下发那一瞬间做（下发预览是只读的），不主动跑这一下的话，
   * 「两个人改了同一处」要等到第一次下发才会分出胜负。这个调用幂等，也不写数据文件。
   */
  scanConflicts: () => http.post(`${BASE}/admin/conflicts/scan`),
  resolveConflict: (id, keepNew) =>
    http.post(`${BASE}/admin/conflicts/${id}/resolve`, { keep_new: keepNew }),
  integrity: () => http.get(`${BASE}/admin/integrity`),
  /** 审核台首页：本插件的计数 + 数据目录是否可用 */
  overview: () => http.get(`${BASE}/admin/overview`),

  // ---- OBot-ACM 原生 __AUDIT__ 队列（与提交单审核完全分开） ----
  auditQueue: (params) => http.get(`${BASE}/admin/audit`, { params }),
  approveAudit: (imgKey, name) =>
    http.post(
      `${BASE}/admin/audit/${encodeURIComponent(imgKey)}/${encodeURIComponent(name)}/approve`,
    ),
  rejectAudit: (imgKey, name) =>
    http.post(
      `${BASE}/admin/audit/${encodeURIComponent(imgKey)}/${encodeURIComponent(name)}/reject`,
    ),
};

export default pickoneApi;
