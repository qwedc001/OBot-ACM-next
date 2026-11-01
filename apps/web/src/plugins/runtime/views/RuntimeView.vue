<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { pluginApi } from "../api";
const data = ref(null),
  loading = ref(true),
  error = ref(""),
  auto = ref(false);
const telemetry = computed(() => data.value?.telemetry ?? {});
const runs = computed(() => [...(telemetry.value.recent_runs ?? [])].reverse());
function duration(seconds) {
  return seconds == null
    ? "—"
    : seconds < 3600
      ? Math.floor(seconds / 60) + " 分钟"
      : Math.floor(seconds / 3600) +
        " 小时 " +
        Math.floor((seconds % 3600) / 60) +
        " 分钟";
}
function date(value) {
  return value ? new Date(value * 1000).toLocaleString("zh-CN") : "暂无记录";
}
async function load() {
  if (loading.value && data.value) return;
  loading.value = true;
  error.value = "";
  try {
    data.value = await pluginApi.overview();
  } catch (failure) {
    error.value = failure.message;
  } finally {
    loading.value = false;
  }
}
let timer;
onMounted(() => {
  load();
  timer = setInterval(() => {
    if (auto.value && !loading.value) load();
  }, 10000);
});
onUnmounted(() => clearInterval(timer));
</script>
<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">运行状态</h1>
        <p class="ep-subtitle">机器人连接、任务队列和最近的指令处理情况。</p>
      </div>
      <div class="ep-actions">
        <el-switch v-model="auto" active-text="自动刷新" /><el-button
          :loading="loading"
          @click="load"
          ><el-icon><Refresh /></el-icon>刷新</el-button
        >
      </div>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <div v-loading="loading">
      <template v-if="data"
        ><div class="runtime-stats">
          <article class="ep-card metric">
            <span>机器人连接</span
            ><strong>{{ telemetry.connected_bot_count }}</strong
            ><small
              :class="telemetry.connected_bot_count ? 'is-ok' : 'ep-muted'"
              >{{
                telemetry.connected_bot_count ? "已连接" : "尚未连接"
              }}</small
            >
          </article>
          <article class="ep-card metric">
            <span>运行时长</span
            ><strong class="metric-text">{{
              duration(telemetry.uptime_seconds)
            }}</strong
            ><small
              >v{{ data.version }} ·
              {{ data.ready ? "服务就绪" : "正在启动" }}</small
            >
          </article>
          <article class="ep-card metric">
            <span>执行中 / 排队中</span
            ><strong
              >{{ data.jobs.running }} <i>/ {{ data.jobs.pending }}</i></strong
            ><small
              >后台并发上限 {{ data.jobs.workers }} · 排队容量
              {{ data.jobs.capacity }}</small
            >
          </article>
          <article class="ep-card metric">
            <span>任务完成 / 失败</span
            ><strong
              >{{ data.jobs.completed }} <i>/ {{ data.jobs.failed }}</i></strong
            ><small>本次进程的任务统计</small>
          </article>
        </div>
        <div class="runtime-times ep-card">
          <p>
            <span>最近收到消息</span
            ><strong>{{ date(telemetry.last_received_at) }}</strong>
          </p>
          <p>
            <span>最近发送消息</span
            ><strong>{{ date(telemetry.last_sent_at) }}</strong>
          </p>
        </div>
        <section class="ep-card run-table">
          <div class="table-heading">
            <h2>最近执行</h2>
            <p class="ep-muted">最多 100 条 · 不包含消息正文</p>
          </div>
          <el-table :data="runs" empty-text="还没有指令执行记录。"
            ><el-table-column
              prop="command_id"
              label="指令"
              min-width="160"
            /><el-table-column
              prop="outcome"
              label="结果"
              width="110"
            /><el-table-column
              v-for="item in [
                ['context_ms', '身份检查'],
                ['handling_ms', '处理'],
                ['delivery_ms', '发送'],
                ['duration_ms', '总耗时'],
              ]"
              :key="item[0]"
              :label="item[1]"
              min-width="105"
              ><template #default="scope"
                >{{ scope.row[item[0]].toFixed(1) }} ms</template
              ></el-table-column
            ></el-table
          >
        </section></template
      >
    </div>
  </div>
</template>
