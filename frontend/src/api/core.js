import http from "./http";
export const coreApi = {
  meta: () => http.get("/meta"),
  plugins: () => http.get("/plugins"),
  me: () => http.get("/auth/me"),
  exchange: (token) => http.post("/auth/exchange", { token }),
  logout: () => http.post("/auth/logout", {}),
};
