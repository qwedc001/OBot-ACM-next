import http from "@/api/http";

/** 本插件的后端前缀，与 WebPlugin 的 slug 一致。 */
const BASE = "/plugins/contestlist";

/**
 * 算法竞赛列表的接口。
 *
 * 注册表挂到 `api.contestlist.*`，避免与其他工具的方法重名。
 */
export default {
  /** 比赛列表 + 在途改动 + 待新增草稿 */
  listItems: () => http.get(`${BASE}/items`),
  getItem: (hash) => http.get(`${BASE}/items/${encodeURIComponent(hash)}`),

  /** 提交入口：新增 / 修改（删除只开给管理员，见下面的 submitDelete） */
  submitCreate: (payload) => http.post(`${BASE}/submissions`, payload),
  submitUpdate: (payload) => http.post(`${BASE}/submissions/update`, payload),
  /** 删除：后端要求管理员权限（`/admin/delete`），普通用户拿到 403 */
  submitDelete: (payload) => http.post(`${BASE}/admin/delete`, payload),

  /** 提交单列表。`q` 按比赛搜（平台 / 简称 / 名称 / 哈希），由后端过滤 */
  listSubmissions: (params) => http.get(`${BASE}/submissions`, { params }),
  /**
   * 审核队列（本插件自己的 `/queue`，只认自己工具的提交单）。
   *
   * 不用框架的 `GET /api/plugins/curation/admin/queue`：它只支持 `img_key` 精确匹配，
   * 审核台需要按比赛名搜索，而那个接口对所有工具通用、不好为单个工具加参数。
   */
  reviewQueue: (params) => http.get(`${BASE}/queue`, { params }),
  withdraw: (id) => http.delete(`${BASE}/submissions/${id}`),

  /** 冲突裁定（admin）。allowDuplicate 用于放行历史遗留的重复比赛 */
  resolveConflict: (id, keepNew, allowDuplicate = false) =>
    http.post(`${BASE}/admin/conflicts/${id}/resolve`, {
      keep_new: keepNew,
      allow_duplicate: allowDuplicate,
    }),

  /**
   * 体检「已通过但已经不能直接下发」的提交单，把它们挂进「冲突待裁定」。
   *
   * 冲突判定平时只在下发那一瞬间做（下发预览是只读的），不主动跑这一下的话，
   * 冲突表里要等到第一次下发才有人。这个调用幂等，也不写数据文件。
   */
  scanConflicts: () => http.post(`${BASE}/admin/conflicts/scan`),
};
