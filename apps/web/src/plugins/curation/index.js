import api from "./api";
export default {
  manifest: {
    slug: "curation",
    name: "内容操作日志",
    summary: "查看表情包和比赛信息的提交、审核与应用记录。",
    home: "/curation",
    order: 80,
    color: "#78664e",
    superuser: true,
  },
  routes: [
    {
      path: "/curation",
      name: "curation-logs",
      component: () => import("./views/LogsView.vue"),
      meta: { title: "内容操作日志", admin: true },
    },
  ],
  api,
};
