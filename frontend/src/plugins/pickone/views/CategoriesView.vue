<script setup>
import { Right, Search } from "@element-plus/icons-vue";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { Plus } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import { session } from "@/stores/session";

const emit = defineEmits(["refresh-summary"]);
const router = useRouter();

const loading = ref(true);
const categories = ref([]);
const keyword = ref("");
const sortBy = ref("count");
const onlyTodo = ref(false);

const createOpen = ref(false);
const creating = ref(false);
const createForm = reactive({
  img_key: "",
  category_id: "",
  keysText: "",
  note: "",
});

const maxCount = computed(() =>
  categories.value.reduce((peak, item) => Math.max(peak, item.image_count), 0),
);

const filtered = computed(() => {
  const needle = keyword.value.trim().toLowerCase();
  let list = categories.value;

  if (onlyTodo.value) {
    list = list.filter((item) => item.missing_ocr > 0 || item.is_pending_new);
  }

  if (needle) {
    list = list.filter(
      (item) =>
        item.img_key.toLowerCase().includes(needle) ||
        item.id.toLowerCase().includes(needle) ||
        item.keys.some((key) => key.toLowerCase().includes(needle)),
    );
  }

  const sorted = [...list];
  if (sortBy.value === "name") {
    sorted.sort((a, b) => a.id.localeCompare(b.id, "zh-Hans-CN"));
  } else if (sortBy.value === "todo") {
    sorted.sort(
      (a, b) => b.missing_ocr - a.missing_ocr || b.image_count - a.image_count,
    );
  } else {
    sorted.sort((a, b) => b.image_count - a.image_count);
  }
  return sorted;
});

const totalImages = computed(() =>
  categories.value.reduce((sum, item) => sum + item.image_count, 0),
);
const totalMissing = computed(() =>
  categories.value.reduce((sum, item) => sum + item.missing_ocr, 0),
);
const todoCategories = computed(
  () => categories.value.filter((item) => item.missing_ocr > 0).length,
);

/** 覆盖率的进度条颜色随完成度变化，比纯数字更直观 */
const coverage = computed(() => {
  if (!totalImages.value) return 0;
  return Math.round((1 - totalMissing.value / totalImages.value) * 100);
});

function barWidth(item) {
  if (!maxCount.value) return 0;
  // 和 Bot 一样用平方根压缩，避免 3000 张把 3 张压没
  return Math.max(
    2,
    Math.round((item.image_count / maxCount.value) ** 0.5 * 100),
  );
}

async function load() {
  loading.value = true;
  try {
    categories.value = (await api.pickone.categories()).categories;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  createForm.img_key = "";
  createForm.category_id = "";
  createForm.keysText = "";
  createForm.note = "";
  createOpen.value = true;
}

async function submitCreate() {
  const imgKey = createForm.img_key.trim();
  if (!imgKey) {
    ElMessage.warning("请填写类别标识");
    return;
  }

  const keys = createForm.keysText
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);

  creating.value = true;
  try {
    await api.pickone.submit(imgKey, "category_create", {
      category_id: createForm.category_id.trim() || imgKey,
      keys: keys.length ? keys : [createForm.category_id.trim() || imgKey],
      note: createForm.note.trim(),
    });
    ElMessage.success("已提交新增类别申请，等待管理员审核");
    createOpen.value = false;
    await load();
    emit("refresh-summary");
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    creating.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">PickOne 图鉴</h1>
        <p class="ep-subtitle">
          表情包分类存储，点击任意类别即可查看对应表情包列表
        </p>
      </div>
      <div class="ep-actions">
        <el-button @click="openCreate" :icon="Plus" size="large">
          {{ session.isAdmin.value ? "新增类别" : "申请新增类别" }}
        </el-button>
      </div>
    </div>

    <div class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">类别</div>
        <div class="ep-stat-value">{{ categories.length }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">表情包总数</div>
        <div class="ep-stat-value">{{ totalImages }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">缺描述文字</div>
        <div class="ep-stat-value" :class="{ 'is-warn': totalMissing > 0 }">
          {{ totalMissing }}
        </div>
        <div class="ep-stat-hint">分布在 {{ todoCategories }} 个类别里</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">描述覆盖率</div>
        <div class="ep-stat-value" :class="{ 'is-ok': coverage >= 90 }">
          {{ coverage }}%
        </div>
        <div class="coverage-bar"><i :style="{ width: `${coverage}%` }" /></div>
      </div>
    </div>

    <!-- 输入框 / 下拉 / 勾选框统一 size，保证同一行高度齐平 -->
    <div class="toolbar ep-mb">
      <el-input
        v-model="keyword"
        size="large"
        placeholder="搜索类别、显示名或别名"
        clearable
        style="width: 260px"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>

      <el-select v-model="sortBy" size="large" style="width: 140px">
        <el-option label="按图片数" value="count" />
        <el-option label="按待补数量" value="todo" />
        <el-option label="按名称" value="name" />
      </el-select>

      <el-checkbox v-model="onlyTodo" size="large" border
        >只看缺描述</el-checkbox
      >

      <span class="ep-small ep-faint">共 {{ filtered.length }} 个类别</span>
    </div>

    <el-empty
      v-if="!loading && !filtered.length"
      description="没有匹配的类别"
    />

    <div class="ep-grid" v-loading="loading">
      <button
        v-for="item in filtered"
        :key="item.img_key"
        class="ep-tile"
        :style="{
          '--ep-accent-tint': item.accent.tint,
          '--ep-accent-strong': item.accent.tint_strong,
          '--ep-accent-ink': item.accent.ink,
          '--ep-accent-track': item.accent.track,
          '--ep-accent-glow': item.accent.glow,
        }"
        @click="
          router.push({
            name: 'pickone-category',
            params: { imgKey: item.img_key },
          })
        "
      >
        <span v-if="item.image_count" class="ep-tile-count">{{
          item.image_count
        }}</span>

        <span class="ep-tile-name">{{ item.id || item.img_key }}</span>
        <span class="ep-tile-key">{{ item.img_key }}</span>

        <span class="ep-tile-aliases">
          <template v-if="item.keys.length"
            >别名：{{ item.keys.slice(0, 5).join("、") }}</template
          >
          <template v-else>（没有别名）</template>
          <template v-if="item.keys.length > 5">
            等 {{ item.keys.length }} 个</template
          >
        </span>

        <span class="ep-tile-foot">
          <span v-if="item.is_pending_new" class="ep-status ep-status--new"
            >待创建</span
          >
          <span
            v-else-if="item.missing_ocr > 0"
            class="ep-status ep-status--todo"
          >
            {{ item.missing_ocr }} 张缺描述
          </span>
          <span
            v-else-if="item.image_count === 0"
            class="ep-status ep-status--empty"
            >空类别</span
          >
          <span v-else class="ep-status ep-status--done">描述已补全</span>
          <el-icon><Right /></el-icon>
        </span>

        <span class="ep-tile-bar"
          ><i :style="{ width: `${barWidth(item)}%` }"
        /></span>
      </button>
    </div>

    <el-drawer
      v-model="createOpen"
      :title="session.isAdmin.value ? '新增类别' : '申请新增类别'"
      size="min(480px, 92vw)"
    >
      <el-form label-width="88px">
        <el-form-item label="类别标识">
          <el-input
            v-model="createForm.img_key"
            placeholder="如 my_emoji（字母数字下划线短横线）"
            size="large"
          />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input
            v-model="createForm.category_id"
            placeholder="Bot 回复里显示的名字"
            size="large"
          />
        </el-form-item>
        <el-form-item label="别名">
          <el-input
            v-model="createForm.keysText"
            type="textarea"
            :rows="2"
            placeholder="用逗号或换行分隔"
            size="large"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createForm.note"
            placeholder="给审核者的说明（可选）"
            size="large"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate"
          >提交</el-button
        >
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.ep-tile {
  position: relative;
  min-height: 200px;
  padding: 26px;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  font-family: var(--ep-font);
  color: inherit;
  background: linear-gradient(
    145deg,
    var(--ep-accent-tint),
    var(--ep-surface) 80%
  );
}
.ep-tile-name {
  padding-right: 42px;
  color: var(--ep-accent-text);
  font-size: 23px;
  font-weight: 700;
}
.ep-tile-key {
  margin-top: 7px;
  font-family: var(--ep-font-mono);
  color: var(--ep-ink-faint);
  font-size: 12px;
}
.ep-tile-aliases {
  flex: 1;
  margin-top: 18px;
  color: var(--ep-ink-muted);
  font-size: 13px;
}
.ep-tile-count {
  position: absolute;
  right: 26px;
  top: 25px;
  font-size: 24px;
  color: var(--ep-accent-text);
}
.ep-tile-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 20px;
}
.ep-tile-bar {
  position: absolute;
  inset: auto 0 0;
  height: 4px;
  background: var(--ep-accent-track);
}
.ep-tile-bar > i {
  display: block;
  height: 100%;
  background: var(--ep-accent-text);
}

.coverage-bar {
  margin-top: 10px;
  height: 6px;
  border-radius: 999px;
  background: rgba(20, 22, 31, 0.07);
  overflow: hidden;
}

.coverage-bar > i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background-image: linear-gradient(90deg, #4bbf8a, #2f9e6e);
  transition: width 0.3s ease;
}
</style>
