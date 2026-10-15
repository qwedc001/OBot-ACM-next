export default [
  {
    path: "/commands",
    name: "commands",
    component: () => import("./views/CommandsView.vue"),
    meta: { title: "指令手册" },
  },
];
