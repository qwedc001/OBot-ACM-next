<script setup>
import { computed } from "vue";
import {
  TopRight,
  Histogram,
  Notebook,
  DataLine,
  Grid,
  Calendar,
  Tickets,
} from "@element-plus/icons-vue";
import { plugins } from "@/plugins/registry";
import { permitted } from "@/plugins/registry-core";
import { session } from "@/stores/session";
const available = computed(() =>
  permitted(plugins, session.isAdmin.value, session.state.user?.tools),
);
const icons = {
  board: Histogram,
  commands: Notebook,
  runtime: DataLine,
  pickone: Grid,
  contestlist: Calendar,
  curation: Tickets,
};
</script>
<template>
  <div class="ep-page hub-page">
    <section class="hub-intro">
      <h1>
        {{
          session.state.user?.role === "group_admin"
            ? "群榜单工作台"
            : session.isAdmin.value
              ? "超级管理员工作台"
              : "个人工作台"
        }}
      </h1>
      <p>
        {{
          session.state.user?.role === "group_admin"
            ? "查看本群训练榜单，调整榜单设置。"
            : "查看训练榜单、维护表情包和比赛信息，或查找机器人指令。"
        }}
      </p>
    </section>
    <section class="tool-section">
      <div class="section-heading">
        <h2>可用工具</h2>
      </div>
      <div class="hub-grid">
        <router-link
          v-for="plugin in available"
          :key="plugin.manifest.slug"
          :to="plugin.manifest.home"
          class="ep-tile hub-tile"
          :style="{ '--tool-accent': plugin.manifest.color }"
          ><div class="tile-top">
            <el-icon :size="27"
              ><component :is="icons[plugin.manifest.slug]"
            /></el-icon>
            <h2>{{ plugin.manifest.name }}</h2>
          </div>
          <p>{{ plugin.manifest.summary }}</p>
          <span class="tile-link"
            >进入工具 <el-icon><TopRight /></el-icon></span
        ></router-link>
      </div>
    </section>
    <aside class="hub-note">
      <p>
        需要重新登录时，私聊机器人发送
        <code>/dashboard</code>。
      </p>
    </aside>
  </div>
</template>
