import http from "@/api/http";
const base = "/plugins/curation/admin";
export default {
  review: (id, payload) => http.post(`${base}/review/${id}`, payload),
  reviewBatch: (payload) => http.post(`${base}/review/batch`, payload),
  unreview: (id) => http.post(`${base}/unreview/${id}`),
  logs: (params) => http.get(`${base}/logs`, { params }),
  pluginApply: (slug) =>
    http.post(`/plugins/${encodeURIComponent(slug)}/admin/apply`, {}),
  pluginApplyPreview: (slug) =>
    http.get(`/plugins/${encodeURIComponent(slug)}/admin/apply/preview`),
  pluginOverview: (slug) =>
    http.get(`/plugins/${encodeURIComponent(slug)}/admin/overview`),
};
