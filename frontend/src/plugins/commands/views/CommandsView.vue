<script setup>
import { computed, onMounted, ref } from "vue";
import { Search, CopyDocument } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { pluginApi } from "../api";
const data = ref(null),
  error = ref(""),
  loading = ref(true),
  query = ref("");
const visible = computed(() =>
  (data.value?.commands ?? []).filter((command) =>
    [command.name, command.summary, ...command.aliases]
      .join(" ")
      .toLowerCase()
      .includes(query.value.toLowerCase()),
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
        <p class="eyebrow">EXPLORE / COMMANDS</p>
        <h1 class="ep-title">指令手册</h1>
        <p class="ep-subtitle">按照你的当前权限，找到每一句指令的用法。</p>
      </div>
      <span class="ep-chip">{{ visible.length }} 条可用</span>
    </div>
    <el-input
      v-model="query"
      placeholder="搜索指令、别名或说明"
      aria-label="搜索指令"
      clearable
      class="command-search"
      ><template #prefix
        ><el-icon><Search /></el-icon></template></el-input
    ><el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <div v-loading="loading">
      <section v-for="group in groups" :key="group.name" class="command-group">
        <h2 class="ep-section-label">{{ group.name }}</h2>
        <div class="command-grid">
          <article
            class="ep-card command-card"
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
            <div v-if="command.aliases.length" class="command-aliases">
              <span v-for="alias in command.aliases" :key="alias">{{
                alias
              }}</span>
            </div>
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
      />
    </div>
  </div>
</template>
