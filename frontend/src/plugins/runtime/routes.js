export default [
  {
    path: "/runtime",
    name: "runtime",
    component: () => import("./views/RuntimeView.vue"),
    meta: { title: "运行状态" },
  },
];
