import { coreApi } from "./core";
import { pluginApis } from "@/plugins/registry";
// Plugin indexes import local APIs, never this facade.
export const api = { ...coreApi, ...pluginApis };
