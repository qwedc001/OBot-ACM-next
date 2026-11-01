import axios from "axios";
import { client } from "@obot/contracts/client";
import { getCsrf } from "@/stores/auth-token";

// Generated SDK URLs already include the `/api` prefix.
const contractsAxios = axios.create({ timeout: 15000, withCredentials: true });
contractsAxios.interceptors.request.use((config) => {
  if (!["get", "head"].includes(config.method) && getCsrf())
    config.headers["X-CSRF-Token"] = getCsrf();
  return config;
});
client.setConfig({ axios: contractsAxios });

export * from "@obot/contracts";
export { client };
