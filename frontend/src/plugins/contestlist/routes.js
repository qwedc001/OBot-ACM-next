/**
 * 算法竞赛列表的路由。
 *
 * 每条 path 都要以 `manifest.home`（`/contestlist`）开头 —— 导航栏靠这个前缀判断
 * 「当前在哪个工具里」。
 */
export default [
  {
    path: "/contestlist",
    name: "contestlist-home",
    component: () => import("./views/ItemsView.vue"),
    meta: { title: "算法竞赛列表" },
  },
  {
    path: "/contestlist/submissions",
    name: "contestlist-submissions",
    component: () => import("./views/SubmissionsView.vue"),
    meta: { title: "我的提交" },
  },
  {
    // 通用审核台由框架提供（它只认提交单，不认具体工具）。冲突裁定需要领域专属的
    // 三方对比，所以这里挂本插件自己的审核视图（路由名字与组件都自成一套）。
    path: "/contestlist/review",
    name: "contestlist-review",
    component: () => import("./views/ReviewView.vue"),
    meta: { title: "比赛列表 审核台", admin: true },
  },
];
