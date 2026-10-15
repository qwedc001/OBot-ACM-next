<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { Refresh, ArrowRight, Histogram } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { pluginApi } from "../api";
const configs = ref([]),
  loading = ref(true),
  error = ref(""),
  image = ref(""),
  generating = ref(""),
  updating = ref("");
async function load() {
  loading.value = true;
  error.value = "";
  try {
    configs.value = (await pluginApi.configs()).configs;
  } catch (failure) {
    error.value = failure.message;
  } finally {
    loading.value = false;
  }
}
async function starred(config, enabled) {
  updating.value = config.name;
  try {
    const result = await pluginApi.starred(config.name, enabled);
    config.show_unrated = enabled;
    ElMessage.success(result.message);
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    updating.value = "";
  }
}
async function render(config, mode) {
  if (generating.value) return;
  generating.value = config.name + mode;
  error.value = "";
  try {
    const blob = await pluginApi.render(config.name, mode);
    if (image.value) URL.revokeObjectURL(image.value);
    image.value = URL.createObjectURL(blob);
  } catch (failure) {
    error.value = failure.message;
  } finally {
    generating.value = "";
  }
}
onMounted(load);
onUnmounted(() => {
  if (image.value) URL.revokeObjectURL(image.value);
});
</script>
<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <p class="eyebrow">TRAINING / PERSONAL</p>
        <h1 class="ep-title">训练榜单</h1>
        <p class="ep-subtitle">
          这里展示个人 QQ 账号绑定的榜单。群榜单请在对应群里管理。
        </p>
      </div>
      <el-button :loading="loading" @click="load"
        ><el-icon><Refresh /></el-icon>刷新</el-button
      >
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
      class="ep-mb"
    />
    <div v-loading="loading" class="board-grid">
      <article
        class="ep-card board-config"
        v-for="config in configs"
        :key="config.name"
      >
        <div class="config-title">
          <span class="tool-icon-soft"
            ><el-icon><Histogram /></el-icon
          ></span>
          <div>
            <h2>{{ config.name }}</h2>
            <a
              :href="config.host"
              target="_blank"
              rel="noopener noreferrer"
              class="ep-muted"
              >{{ config.host }}</a
            >
          </div>
        </div>
        <div class="switch-row">
          <div>
            <strong>显示打星用户</strong>
            <p>设置将同步用于机器人回复和每日推送。</p>
          </div>
          <el-switch
            :model-value="config.show_unrated"
            :loading="updating === config.name"
            :aria-label="config.name + ' 显示打星用户'"
            @update:model-value="starred(config, $event)"
          />
        </div>
        <div class="board-actions">
          <el-button
            type="primary"
            :loading="generating === config.name + 'today'"
            :disabled="Boolean(generating)"
            @click="render(config, 'today')"
            >今日榜单</el-button
          ><el-button
            :loading="generating === config.name + 'yesterday'"
            :disabled="Boolean(generating)"
            @click="render(config, 'yesterday')"
            >昨日榜单</el-button
          >
        </div>
      </article>
      <section
        v-if="!loading && !configs.length && !error"
        class="ep-card board-empty"
      >
        <p class="eyebrow">A NEW START</p>
        <h2>从连接一个 OJ 开始。</h2>
        <p class="ep-muted">
          私聊机器人发送
          <code>/board bind 配置名</code>，打开返回的链接完成绑定。
        </p>
      </section>
    </div>
    <section v-if="image" class="ep-card board-preview">
      <div class="section-heading">
        <h2>榜单预览</h2>
        <a :href="image" download="leaderboard.png" class="text-link"
          >下载图片 ↗</a
        >
      </div>
      <img :src="image" alt="训练榜单图片" />
    </section>
  </div>
</template>
