import axios from "axios";
import { getCsrf } from "@/stores/auth-token";
const http = axios.create({
  baseURL: "/api",
  timeout: 15000,
  withCredentials: true,
});
http.interceptors.request.use((config) => {
  if (!["get", "head"].includes(config.method) && getCsrf())
    config.headers["X-CSRF-Token"] = getCsrf();
  return config;
});
http.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    let body = error.response?.data;
    if (body instanceof Blob) {
      try {
        body = JSON.parse(await body.text());
      } catch {
        body = null;
      }
    }
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : typeof body?.error === "string"
          ? body.error
          : error.code === "ECONNABORTED"
            ? "请求超时，请稍后重试。"
            : "暂时无法完成请求，请稍后重试。";
    const safe = new Error(message);
    safe.status = error.response?.status;
    return Promise.reject(safe);
  },
);
export default http;
