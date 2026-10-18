import http from "@/api/http";
const BASE = "/plugins/pickone/groups";
const part = encodeURIComponent;
export default {
  scopes: () => http.get(`${BASE}/scopes`),
  categories: (scope) => http.get(`${BASE}/categories`, { params: { scope } }),
  create: (scope, key, values) =>
    http.post(`${BASE}/categories/${part(key)}`, values, { params: { scope } }),
  category: (scope, key, values) =>
    http.put(`${BASE}/categories/${part(key)}`, values, { params: { scope } }),
  images: (scope, key, page) =>
    http.get(`${BASE}/images/${part(key)}`, { params: { scope, page } }),
  image: (scope, key, values) =>
    http.put(`${BASE}/images/${part(key)}`, values, { params: { scope } }),
  remove: (scope, key, name) =>
    http.delete(`${BASE}/images/${part(key)}/${part(name)}`, {
      params: { scope },
      data: {},
    }),
  audit: (scope, page) =>
    http.get(`${BASE}/audit`, { params: { scope, page } }),
  review: (scope, key, name, approve) =>
    http.post(
      `${BASE}/audit/${part(key)}/${part(name)}`,
      { approve },
      { params: { scope } },
    ),
  media: (scope, key, name, pending) =>
    `/api${BASE}/media/${part(key)}/${part(name)}?${new URLSearchParams({ scope, pending })}`,
};
