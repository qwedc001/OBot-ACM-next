import http from "@/api/http";
export const pluginApi = { overview: () => http.get("/plugins/runtime") };
