import { captureRouteCapability } from "@/security/capability";
import { createRouter, createWebHistory } from "vue-router";
import { session } from "@/stores/session";
import { pluginRoutes } from "@/plugins/registry";
const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: "/",
      name: "hub",
      component: () => import("@/views/ToolHubView.vue"),
      meta: { title: "工具首页" },
    },
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { public: true, title: "登录" },
    },
    ...pluginRoutes,
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: () => import("@/views/NotFoundView.vue"),
      meta: { public: true, title: "页面不存在" },
    },
  ],
});
router.beforeEach(async (to) => {
  if (
    to.meta.public &&
    to.hash &&
    ["/login", "/board/bind"].includes(to.path)
  ) {
    const target = {
      path: to.path,
      query: to.query,
      hash: "",
      replace: true,
      force: true,
    };
    window.history.replaceState(
      window.history.state,
      "",
      router.resolve(target).href,
    );
    captureRouteCapability(to.path, to.hash);
    return target;
  }
  if (to.meta.public) return true;
  try {
    await session.loadMe(true);
  } catch {
    return { name: "login" };
  }
  if (!session.isLoggedIn.value) return { name: "login" };
  if (to.meta.admin && !session.isAdmin.value) return { name: "hub" };
  return true;
});
router.afterEach((to) => {
  document.title = `${to.meta.title ?? "工具"} · OBot's Endpoint`;
});
export default router;
