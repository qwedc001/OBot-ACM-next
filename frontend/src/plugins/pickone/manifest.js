/**
 * PickOne 的对外介绍。字段与后端 `backend/web/plugins/pickone.py` 里的
 * `PluginManifest` 一一对应；`nav` / `adminNav` 只在前端使用。
 */
export default {
  slug: "pickone",
  name: "PickOne 表情包",
  tag: "表情包数据",
  icon: "Grid",
  summary: "维护「来只」表情包的图片描述、类别别名与点赞评论。",
  home: "/pickone",
  order: 20,
  color: "#764156",
  accent: {
    tint: "rgba(158, 87, 114, 0.07)",
    tint_strong: "rgba(158, 87, 114, 0.14)",
    ink: "#764156",
    track: "rgba(158, 87, 114, 0.16)",
    glow: "rgba(158, 87, 114, 0.24)",
  },
  /** 进入本工具后，导航栏左边这几个入口 */
  nav: [
    { path: "/pickone", label: "所有表情", icon: "Grid" },
    { path: "/pickone/submissions", label: "我的提交", icon: "Tickets" },
  ],
  /**
   * 管理员在本工具内额外看到的入口。
   *
   * 「审核台」是一个区块，`children` 是它的第二级导航：两个工作面各占一格，
   * 点一下就到。不做落地页，也不把它们并列成两个顶级导航项 ——
   * 那样会被读成「同一个审核台的两个标签页」，而它们其实毫无关系
   * （一个是站内提交单，一个是上游塞进来的图片文件）。
   */
  adminNav: [
    {
      path: "/pickone/review",
      label: "审核台",
      icon: "Stamp",
      badge: true,
      children: [
        { path: "/pickone/review/submissions", label: "提交审核" },
        { path: "/pickone/review/images", label: "待审图片" },
      ],
    },
  ],
};
