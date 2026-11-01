/**
 * PickOne 的路由。
 *
 * 路径都以 manifest.home（`/pickone`）为前缀，`currentPlugin()` 靠这个前缀
 * 判断当前在哪个工具里。这里只写相对路径的部分会更容易看错，所以写全。
 */
export default [
  {
    path: "/pickone/groups",
    name: "pickone-groups",
    component: () => import("./views/GroupsView.vue"),
    meta: { title: "群图库" },
  },
  {
    path: "/pickone",
    name: "pickone-home",
    component: () => import("./views/CategoriesView.vue"),
    meta: { title: "Pick-one 类别" },
  },
  {
    path: "/pickone/categories/:imgKey",
    name: "pickone-category",
    component: () => import("./views/CategoryView.vue"),
    meta: { title: "Pick-one 类别详情" },
  },
  {
    path: "/pickone/submissions",
    name: "pickone-submissions",
    component: () => import("./views/SubmissionsView.vue"),
    meta: { title: "我的提交" },
  },
  // 审核台是一个区块、两个工作面：区块入口直接落在默认工作面上（不停留在一张
  // 只有两个按钮的落地页），两个工作面之间靠导航栏的第二级页签切换。
  {
    path: "/pickone/review",
    name: "pickone-review",
    redirect: "/pickone/review/submissions",
  },
  {
    path: "/pickone/review/submissions",
    name: "pickone-review-submissions",
    component: () => import("./views/ReviewView.vue"),
    meta: { title: "提交审核", admin: true },
  },
  {
    path: "/pickone/review/images",
    name: "pickone-review-images",
    component: () => import("./views/AuditView.vue"),
    meta: { title: "待审图片", admin: true },
  },
  // 上游审核一度挂在 /pickone/audit：老地址（书签、发出去的链接）转到新地址
  {
    path: "/pickone/audit",
    name: "pickone-audit",
    redirect: "/pickone/review/images",
  },
];
