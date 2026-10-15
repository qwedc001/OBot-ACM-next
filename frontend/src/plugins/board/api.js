import http from "@/api/http";
export const pluginApi = {
  inspect: (token) => http.post("/plugins/board/bind/inspect", { token }),
  submit: (payload) =>
    http.post("/plugins/board/bind/submit", payload, { timeout: 60000 }),
  scopes: () => http.get("/plugins/board/scopes"),
  groupCheck: () => http.post("/plugins/board/group-check", {}),
  configs: (scope = "personal") =>
    http.get("/plugins/board/configs", { params: { scope } }),
  settings: (name, options, scope) =>
    http.post("/plugins/board/settings", { name, options, scope }),
  starred: (name, enabled, scope) =>
    http.post("/plugins/board/starred", { name, enabled, scope }),
  render: (name, mode, scope) =>
    http.post(
      "/plugins/board/render",
      { name, mode, scope },
      { timeout: 90000, responseType: "blob" },
    ),
};
