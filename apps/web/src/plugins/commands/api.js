import http from "@/api/http";
export const pluginApi = { list: () => http.get("/plugins/commands") };
