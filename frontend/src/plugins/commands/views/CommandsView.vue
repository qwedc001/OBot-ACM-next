<script setup>
import { computed, onMounted, ref } from "vue";
import { Search, CopyDocument } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { pluginApi } from "../api";
const data = ref(null),
  error = ref(""),
  loading = ref(true),
  query = ref(""),
  directory = ref("");
const directories = computed(() => [
  ...new Set(
    (data.value?.commands ?? []).map(
      (command) =>
        command.directory.join(data.value?.separator ?? ".") || "通用功能",
    ),
  ),
]);
const visible = computed(() =>
  (data.value?.commands ?? []).filter(
    (command) =>
      (!directory.value ||
        (command.directory.join(data.value?.separator ?? ".") || "通用功能") ===
          directory.value) &&
      [
        command.name,
        command.summary,
        command.usage,
        ...command.aliases,
        ...command.examples,
      ]
        .join(" ")
        .toLowerCase()
        .includes(query.value.trim().toLowerCase()),
  ),
);
const groups = computed(() => {
  const map = new Map();
  for (const command of visible.value) {
    const key =
      command.directory.join(data.value?.separator ?? ".") || "通用功能";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(command);
  }
  return [...map].map(([name, commands]) => ({ name, commands }));
});
async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await pluginApi.list();
  } catch (failure) {
    error.value = failure.message;
  } finally {
    loading.value = false;
  }
}
async function copy(value) {
  try {
    await navigator.clipboard.writeText(value);
    ElMessage.success("已复制指令");
  } catch {
    ElMessage.info(value);
  }
}
onMounted(load);
</script>
<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">指令手册</h1>
        <p class="ep-subtitle">查找指令，点击示例即可复制。</p>
      </div>
      <span class="ep-muted" role="status">{{ visible.length }} 条可用</span>
    </div>
    <div class="command-toolbar">
      <el-input
        v-model="query"
        placeholder="搜索指令、别名或说明"
        aria-label="搜索指令"
        clearable
        class="command-search"
        ><template #prefix
          ><el-icon><Search /></el-icon></template
      ></el-input>
      <el-select
        v-model="directory"
        aria-label="指令模块"
        placeholder="全部模块"
      >
        <el-option label="全部模块" value="" />
        <el-option
          v-for="name in directories"
          :key="name"
          :label="name"
          :value="name"
        />
      </el-select>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <div v-loading="loading">
      <section v-for="group in groups" :key="group.name" class="command-group">
        <h2 class="command-module">{{ group.name }}</h2>
        <div class="command-grid">
          <article
            class="command-card"
            v-for="command in group.commands"
            :key="command.id"
          >
            <div class="section-heading">
              <h3>
                <code
                  >{{ command.mode === "keyword" ? "" : "/"
                  }}{{ command.name }}</code
                >
              </h3>
              <span v-if="command.permission !== 'everyone'" class="ep-chip">{{
                command.permission === "superuser" ? "超管" : "群管"
              }}</span>
            </div>
            <p>{{ command.summary }}</p>
            <p class="command-usage ep-muted">{{ command.usage }}</p>
            <p v-if="command.aliases.length" class="command-aliases">
              别名：{{ command.aliases.join("、") }}
            </p>
            <button
              v-for="example in command.examples"
              :key="example"
              class="command-example"
              :aria-label="'复制 ' + example"
              @click="copy(example)"
            >
              <code>{{ example }}</code
              ><el-icon><CopyDocument /></el-icon>
            </button>
          </article>
        </div>
      </section>
      <el-empty
        v-if="!loading && !error && !visible.length"
        description="没有找到匹配的可用指令。"
        ><el-button
          @click="
            query = '';
            directory = '';
          "
          >清除筛选</el-button
        ></el-empty
      >
      <el-button v-if="error" @click="load">重新加载</el-button>
    </div>
  </div>
</template>
