export function defineRegistry(modules) {
  const plugins = Object.values(modules).map((item) => item.default);
  const slugs = new Set(),
    names = new Set(),
    paths = new Set();
  for (const plugin of plugins) {
    const slug = plugin?.manifest?.slug;
    if (!/^[a-z][a-z0-9-]*$/.test(slug) || slugs.has(slug))
      throw new Error("Invalid or duplicate plugin slug");
    slugs.add(slug);
    for (const route of plugin.routes ?? []) {
      if (
        !(
          route.path === plugin.manifest.home ||
          route.path.startsWith(plugin.manifest.home + "/")
        ) ||
        names.has(route.name) ||
        paths.has(route.path)
      )
        throw new Error("Invalid or duplicate plugin route");
      names.add(route.name);
      paths.add(route.path);
    }
  }
  return plugins.sort(
    (a, b) => (a.manifest.order ?? 100) - (b.manifest.order ?? 100),
  );
}
export function permitted(plugins, admin, tools = null) {
  return plugins.filter(
    (plugin) =>
      (!plugin.manifest.superuser || admin) &&
      (!tools || tools.includes(plugin.manifest.slug)),
  );
}
