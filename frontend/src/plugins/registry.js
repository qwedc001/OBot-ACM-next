import { defineRegistry } from "./registry-core";
const modules = import.meta.glob(["./*/index.js", "!./_*/index.js"], {
  eager: true,
});
export const plugins = defineRegistry(modules);
export const pluginApis = Object.fromEntries(
  plugins.map((plugin) => [plugin.manifest.slug, plugin.api]),
);
export const pluginRoutes = plugins.flatMap((plugin) =>
  plugin.routes.map((route) => ({
    ...route,
    meta: {
      ...route.meta,
      plugin: plugin.manifest.slug,
      admin: plugin.manifest.superuser ?? false,
    },
  })),
);
export function currentPlugin(path) {
  return (
    plugins
      .filter(
        (plugin) =>
          path === plugin.manifest.home ||
          path.startsWith(plugin.manifest.home + "/"),
      )
      .sort((a, b) => b.manifest.home.length - a.manifest.home.length)[0] ??
    null
  );
}
