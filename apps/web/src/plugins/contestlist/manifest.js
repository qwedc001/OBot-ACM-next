/**
 * 算法竞赛列表的对外介绍。字段与后端 `backend/web/plugins/contestlist.py` 里的
 * `PluginManifest` 一一对应；`nav` / `adminNav` 只在前端使用。slug 两边必须一致。
 */
export default {
  slug: "contestlist",
  name: "算法竞赛列表",
  tag: "算法竞赛",
  icon: "Trophy",
  summary: "维护手动录入的算法竞赛列表，通常为 XCPC 等比赛。",
  home: "/contestlist",
  order: 30,
  color: "#637b91",
  accent: {
    tint: "rgba(124, 143, 79, 0.07)",
    tint_strong: "rgba(124, 143, 79, 0.14)",
    ink: "#5d6b3b",
    track: "rgba(124, 143, 79, 0.16)",
    glow: "rgba(124, 143, 79, 0.24)",
  },
  /** 进入本工具后，导航栏左边这几个入口 */
  nav: [
    { path: "/contestlist", label: "比赛列表", icon: "Trophy" },
    { path: "/contestlist/submissions", label: "我的提交", icon: "Tickets" },
  ],
  /** 管理员在本工具内额外看到的入口 */
  adminNav: [
    {
      path: "/contestlist/review",
      label: "审核台",
      icon: "Stamp",
      badge: true,
    },
  ],
};
