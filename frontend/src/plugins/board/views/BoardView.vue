<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { pluginApi } from "../api";
import { bindingOptions } from "../form";
import { session } from "@/stores/session";
const configs = ref([]),
  scopes = ref([{ id: "personal", label: "个人榜单" }]),
  scope = ref("personal"),
  loading = ref(true),
  error = ref(""),
  image = ref(""),
  previewTitle = ref(""),
  preview = ref(null),
  generating = ref(""),
  updating = ref(""),
  checking = ref(false),
  check = ref(null),
  editing = ref(null),
  saving = ref(false);
const form = reactive({ excludeUid: "", excludeBefore: "", showUnrated: true });
let loadRevision = 0;
function clearImage() {
  if (image.value) URL.revokeObjectURL(image.value);
  image.value = "";
}
async function loadConfigs() {
  const revision = ++loadRevision,
    selected = scope.value;
  loading.value = true;
  error.value = "";
  configs.value = [];
  editing.value = null;
  clearImage();
  try {
    const result = await pluginApi.configs(selected);
    if (revision === loadRevision) {
      configs.value = result.configs;
      if (selected !== "personal") check.value = null;
    }
  } catch (failure) {
    if (revision === loadRevision) error.value = failure.message;
  } finally {
    if (revision === loadRevision) loading.value = false;
  }
}
async function load() {
  try {
    scopes.value = (await pluginApi.scopes()).scopes;
    if (!scopes.value.some((item) => item.id === scope.value)) {
      scope.value = scopes.value[0]?.id ?? "";
      if (!session.state.user?.tools)
        ElMessage.info("群管理权限已失效，请重新验证。");
    }
    await loadConfigs();
  } catch (failure) {
    configs.value = [];
    editing.value = null;
    clearImage();
    error.value = failure.message;
    loading.value = false;
  }
}
async function starred(config, enabled) {
  updating.value = config.name;
  try {
    const result = await pluginApi.starred(config.name, enabled, scope.value);
    config.show_unrated = enabled;
    config.options.show_unrated = enabled;
    ElMessage.success(result.message);
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    updating.value = "";
  }
}
async function render(config, mode) {
  if (generating.value) return;
  const selected = scope.value;
  const revision = loadRevision;
  generating.value = config.name + mode;
  error.value = "";
  try {
    const blob = await pluginApi.render(config.name, mode, selected);
    if (selected !== scope.value || revision !== loadRevision) return;
    clearImage();
    image.value = URL.createObjectURL(blob);
    previewTitle.value =
      config.name + (mode === "today" ? " · 今日榜单" : " · 昨日榜单");
    await nextTick();
    preview.value?.focus({ preventScroll: true });
    preview.value?.scrollIntoView({
      block: "start",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  } catch (failure) {
    if (selected === scope.value && revision === loadRevision)
      error.value = failure.message;
  } finally {
    generating.value = "";
  }
}
async function groupCheck() {
  checking.value = true;
  try {
    check.value = await pluginApi.groupCheck();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    checking.value = false;
  }
}
async function copyCheck() {
  try {
    await navigator.clipboard.writeText(check.value.command);
    ElMessage.success("验证指令已复制");
  } catch {
    ElMessage.info("请手动选择并复制验证指令。");
  }
}
function edit(config) {
  editing.value = { name: config.name, scope: scope.value };
  form.excludeUid = (config.options.exclude_uid ?? []).join(", ");
  form.excludeBefore =
    config.options.exclude_before === "1970-01-01"
      ? ""
      : (config.options.exclude_before ?? "");
  form.showUnrated = config.show_unrated;
}
async function save() {
  if (!editing.value || saving.value) return;
  saving.value = true;
  try {
    const result = await pluginApi.settings(
      editing.value.name,
      bindingOptions(form.excludeUid, form.excludeBefore, form.showUnrated),
      editing.value.scope,
    );
    ElMessage.success(result.message);
    editing.value = null;
    await loadConfigs();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    saving.value = false;
  }
}
onMounted(load);
onUnmounted(() => {
  loadRevision++;
  clearImage();
});
</script>
<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">训练榜单</h1>
        <p class="ep-subtitle">选择配置，生成榜单或调整显示设置。</p>
      </div>
      <el-button :loading="loading" @click="load"
        ><el-icon><Refresh /></el-icon>刷新</el-button
      >
    </div>
    <section class="ep-card board-scope">
      <label for="board-scope">配置范围</label>
      <el-select
        id="board-scope"
        v-model="scope"
        aria-label="配置范围"
        @change="loadConfigs"
      >
        <el-option
          v-for="item in scopes"
          :key="item.id"
          :label="item.label"
          :value="item.id"
        />
      </el-select>
      <el-button
        v-if="!session.state.user?.tools"
        :loading="checking"
        @click="groupCheck"
        >验证管理群</el-button
      >
      <p class="ep-muted">
        {{
          session.state.user?.tools
            ? "本次登录仅可查看和修改发起登录的群榜单。"
            : "管理群榜单前，请先验证群管理权限。"
        }}
      </p>
      <div v-if="check" class="group-check" role="status">
        <p>
          请在目标群
          @机器人发送以下指令，再点击刷新。验证指令五分钟内有效，仅群主、群管理员或超管可执行。
        </p>
        <code>{{ check.command }}</code
        ><el-button size="small" @click="copyCheck">复制验证指令</el-button>
      </div>
    </section>
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
        :key="scope + config.name"
      >
        <div class="config-title">
          <div>
            <h2>{{ config.name }}</h2>
            <a :href="config.host" target="_blank" rel="noreferrer noopener">{{
              config.host
            }}</a>
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
            :disabled="Boolean(updating)"
            :aria-label="config.name + ' 显示打星用户'"
            @update:model-value="starred(config, $event)"
          />
        </div>
        <p class="ep-muted">
          每日推送：{{ config.push_enabled ? "已开启" : "未开启" }} · 排除
          {{ config.options.exclude_uid?.length ?? 0 }} 个 UID
        </p>
        <div class="board-actions">
          <el-button
            type="primary"
            :loading="generating === config.name + 'today'"
            :disabled="Boolean(generating)"
            @click="render(config, 'today')"
            >今日榜单</el-button
          >
          <el-button
            :loading="generating === config.name + 'yesterday'"
            :disabled="Boolean(generating)"
            @click="render(config, 'yesterday')"
            >昨日榜单</el-button
          >
          <el-button @click="edit(config)">显示设置</el-button>
        </div>
      </article>
      <section
        v-if="!loading && !configs.length && !error"
        class="ep-card board-empty"
      >
        <h2>还没有榜单配置</h2>
        <p class="ep-muted">
          私聊机器人发送
          <code>/board bind 配置名</code
          >，打开返回的链接完成个人绑定；群榜单请在群中绑定后验证管理权限。
        </p>
      </section>
    </div>
    <p v-if="generating" role="status" class="render-progress">
      正在获取训练记录并生成榜单…
    </p>
    <section
      v-if="image"
      ref="preview"
      tabindex="-1"
      role="region"
      :aria-label="previewTitle"
      class="board-preview"
    >
      <div class="section-heading">
        <h2>{{ previewTitle }}</h2>
        <a :href="image" download="leaderboard.png" class="text-link"
          >下载图片</a
        >
      </div>
      <el-image
        :src="image"
        alt="训练榜单图片"
        :preview-src-list="[image]"
        preview-teleported
      />
      <p class="ep-muted preview-hint">点击图片放大查看。</p>
    </section>
    <el-dialog
      :model-value="Boolean(editing)"
      title="榜单显示设置"
      width="min(520px, 92vw)"
      @update:model-value="!$event && (editing = null)"
    >
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="排除 UID" for="settings-uid"
          ><el-input
            id="settings-uid"
            v-model="form.excludeUid"
            placeholder="逗号或空格分隔，例如 2, 3"
        /></el-form-item>
        <el-form-item label="排除此日期前注册的用户" for="settings-before"
          ><el-input
            id="settings-before"
            v-model="form.excludeBefore"
            type="date"
        /></el-form-item>
        <el-form-item label="显示打星用户"
          ><el-switch v-model="form.showUnrated" aria-label="设置显示打星用户"
        /></el-form-item>
        <p class="ep-muted">
          此处只修改榜单显示，管理员账号和密码不会在此显示。每日推送开关继续使用
          QQ 中的 /board push 指令。
        </p>
        <el-button native-type="submit" type="primary" :loading="saving"
          >保存显示设置</el-button
        >
      </el-form>
    </el-dialog>
  </div>
</template>
