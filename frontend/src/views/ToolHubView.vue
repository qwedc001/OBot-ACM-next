<script setup>
import { computed } from "vue";
import {
  TopRight,
  Histogram,
  Notebook,
  DataLine,
} from "@element-plus/icons-vue";
import { plugins } from "@/plugins/registry";
import { permitted } from "@/plugins/registry-core";
import { session } from "@/stores/session";
const available = computed(() =>
  permitted(plugins, session.isAdmin.value, session.state.user?.tools),
);
const icons = { board: Histogram, commands: Notebook, runtime: DataLine };
</script>
<template>
  <div class="ep-page hub-page">
    <section class="hub-intro">
      <p class="eyebrow">OBOT / WORKSPACE</p>
      <h1>每一段进步，<br /><span>都值得被看见。</span></h1>
      <p>从一条提交记录，到一份训练榜单。<br />在这里，继续你的 AC 旅程。</p>
      <div class="hub-badges">
        <span class="ep-chip ep-chip--ok">QQ 身份已连接</span
        ><span class="ep-chip">{{
          session.state.user?.role === "group_admin"
            ? "群榜单工作台"
            : session.isAdmin.value
              ? "超级管理员工作台"
              : "个人工作台"
        }}</span>
      </div>
    </section>
    <section class="tool-section">
      <div class="section-heading">
        <span class="ep-section-label">你的工具</span
        ><span class="ep-muted">{{ available.length }} 项可用</span>
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
              ><component :is="icons[plugin.manifest.slug]" /></el-icon
            ><span class="tool-tag">{{ plugin.manifest.tag }}</span>
          </div>
          <h2>{{ plugin.manifest.name }}</h2>
          <p>{{ plugin.manifest.summary }}</p>
          <span class="tile-link"
            >进入工具 <el-icon><TopRight /></el-icon></span
        ></router-link>
      </div>
    </section>
    <aside class="hub-note">
      <span class="note-dot" />
      <p>
        这里的身份和权限与机器人保持一致。需要新的登录链接时，私聊发送
        <code>/dashboard</code>。
      </p>
    </aside>
  </div>
</template>
