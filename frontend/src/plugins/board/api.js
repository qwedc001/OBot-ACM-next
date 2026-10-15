import http from "@/api/http";
export const pluginApi = {
  inspect: (token) => http.post("/plugins/board/bind/inspect", { token }),
  submit: (payload) =>
    http.post("/plugins/board/bind/submit", payload, { timeout: 60000 }),
  configs: () => http.get("/plugins/board/configs"),
  starred: (name, enabled) =>
    http.post("/plugins/board/starred", { name, enabled }),
  render: (name, mode) =>
    http.post(
      "/plugins/board/render",
      { name, mode },
      { timeout: 90000, responseType: "blob" },
    ),
};
