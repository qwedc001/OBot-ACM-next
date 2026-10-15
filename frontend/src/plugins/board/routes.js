export default [
  {
    path: "/board",
    name: "board",
    component: () => import("./views/BoardView.vue"),
    meta: { title: "训练榜单" },
  },
  {
    path: "/board/bind",
    name: "board-bind",
    component: () => import("./views/BindView.vue"),
    meta: { public: true, title: "绑定训练榜单" },
  },
];
