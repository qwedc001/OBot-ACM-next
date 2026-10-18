<script setup>
import { Refresh, Search, SortDown, SortUp } from "@element-plus/icons-vue";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowLeft, EditPen } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import ImageCard from "@/plugins/pickone/components/ImageCard.vue";
import ImageEditorDrawer from "@/plugins/pickone/components/ImageEditorDrawer.vue";
import CategoryInfoDialog from "@/plugins/pickone/components/CategoryInfoDialog.vue";

const emit = defineEmits(["refresh-summary"]);
const route = useRoute();
const router = useRouter();

const imgKey = computed(() => String(route.params.imgKey ?? ""));

const loading = ref(false);
const category = ref(null);
const stats = ref(null);
const items = ref([]);
const total = ref(0);
const missingInFilter = ref(0);

const query = reactive({
  page: 1,
  page_size: 60,
  q: "",
  filter: "all",
  sort: "hash",
  order: "asc",
});

const editorOpen = ref(false);
const editing = ref(null);
const infoOpen = ref(false);

const accent = computed(() => category.value?.accent ?? null);

const accentStyle = computed(() => ({
  "--ep-accent-tint": accent.value?.tint ?? "rgba(0,0,0,0.04)",
  "--ep-accent-strong": accent.value?.tint_strong ?? "rgba(0,0,0,0.09)",
  "--ep-accent-ink": accent.value?.ink ?? "var(--ep-ink)",
  "--ep-accent-track": accent.value?.track ?? "rgba(0,0,0,0.06)",
  "--ep-accent-glow": accent.value?.glow ?? "rgba(0,0,0,0.1)",
}));

const sortOptions = [
  { value: "hash", label: "文件名" },
  { value: "add_time", label: "添加时间" },
  { value: "likes", label: "点赞数" },
  { value: "comments", label: "评论数" },
  { value: "pickup_times", label: "提起次数" },
];

/** 待审核改动数量：stats 里没有，用「筛出来能查到」这个事实做提示 */
const hasChanges = computed(() => stats.value?.pending_changes ?? null);

const filterOptions = computed(() => [
  { value: "all", label: "全部", count: stats.value?.total ?? null },
  {
    value: "missing_ocr",
    label: "缺描述",
    count: stats.value?.missing_ocr ?? null,
  },
  {
    value: "has_change",
    label: "待审核",
    count: stats.value?.pending_changes ?? null,
  },
]);

const doneCount = computed(() => {
  if (!stats.value) return 0;
  return Math.max(0, stats.value.total - stats.value.missing_ocr);
});

const doneRatio = computed(() => {
  if (!stats.value?.total) return 0;
  return doneCount.value / stats.value.total;
});

function pickFilter(value) {
  query.filter = value;
  query.page = 1;
  loadImages();
}

async function loadCategory() {
  try {
    category.value = (await api.pickone.category(imgKey.value)).category;
  } catch (error) {
    ElMessage.error(error.message);
    category.value = null;
  }
}

async function loadStats() {
  try {
    stats.value = await api.pickone.imageStats(imgKey.value);
  } catch {
    stats.value = null;
  }
}

async function loadImages() {
  loading.value = true;
  try {
    const data = await api.pickone.images(imgKey.value, { ...query });
    items.value = data.items;
    total.value = data.total;
    missingInFilter.value = data.missing_ocr_in_view ?? 0;
  } catch (error) {
    ElMessage.error(error.message);
    items.value = [];
    total.value = 0;
  } finally {
    loading.value = false;
  }
}

async function loadAll() {
  await Promise.all([loadCategory(), loadStats(), loadImages()]);
}

function resetQuery() {
  query.q = "";
  query.filter = "all";
  query.sort = "hash";
  query.order = "asc";
  query.page = 1;
  loadImages();
}

function openEditor(image) {
  editing.value = image;
  editorOpen.value = true;
}

async function onSubmitted() {
  await Promise.all([loadImages(), loadStats()]);
  emit("refresh-summary");
}

async function onInfoSubmitted() {
  infoOpen.value = false;
  await loadAll();
  emit("refresh-summary");
}

watch(
  () => route.params.imgKey,
  (next) => {
    if (!next) return;
    query.page = 1;
    query.q = String(route.query.q ?? "");
    query.filter = String(route.query.filter ?? "all");
    loadAll();
  },
);

onMounted(() => {
  if (route.query.q) query.q = String(route.query.q);
  if (route.query.filter) query.filter = String(route.query.filter);
  loadAll();
});
</script>

<template>
  <div class="ep-page" :style="accentStyle">
    <div class="ep-page-head">
      <div>
        <el-button
          text
          class="back"
          @click="router.push({ name: 'pickone-home' })"
          :icon="ArrowLeft"
        >
          返回图鉴
        </el-button>
        <h1 class="ep-title">
          {{ category?.id || imgKey }}
          <span v-if="category?.is_pending_new" class="ep-chip ep-chip--warn"
            >待创建</span
          >
        </h1>
        <p class="ep-subtitle">
          <span class="ep-mono">{{ imgKey }}</span>
          <template v-if="category?.keys?.length">
            · 别名 {{ category.keys.slice(0, 4).join("、") }}
            <template v-if="category.keys.length > 4">
              等 {{ category.keys.length }} 个</template
            >
          </template>
        </p>
      </div>
      <div class="ep-actions">
        <el-button @click="infoOpen = true" :icon="EditPen" size="large">
          修改类别信息
        </el-button>
        <el-button :icon="Refresh" @click="loadAll" size="large"
          >刷新</el-button
        >
      </div>
    </div>

    <div v-if="stats" class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">图片总数</div>
        <div class="ep-stat-value">{{ stats.total }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">缺描述文字</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-warn': stats.missing_ocr > 0 }"
        >
          {{ stats.missing_ocr }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">描述覆盖率</div>
        <div class="ep-stat-value is-ok">
          {{ Math.round(doneRatio * 100) }}%
        </div>
        <div class="progress">
          <i :style="{ width: `${doneRatio * 100}%` }" />
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">点赞合计</div>
        <div class="ep-stat-value">{{ stats.total_likes }}</div>
      </div>
    </div>

    <!-- 同一行控件统一 size，保证高度齐平 -->
    <div class="toolbar ep-mb">
      <el-input
        v-model="query.q"
        size="large"
        placeholder="搜索图片文字或 ID"
        clearable
        style="width: 240px"
        @keyup.enter="((query.page = 1), loadImages())"
        @clear="((query.page = 1), loadImages())"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>

      <div class="filter-group">
        <button
          v-for="option in filterOptions"
          :key="option.value"
          class="filter-btn"
          :class="{ 'is-active': query.filter === option.value }"
          @click="pickFilter(option.value)"
        >
          {{ option.label }}
          <em v-if="option.count !== null">{{ option.count }}</em>
        </button>
      </div>

      <el-select
        v-model="query.sort"
        size="large"
        style="width: 140px"
        @change="((query.page = 1), loadImages())"
      >
        <el-option
          v-for="option in sortOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>

      <el-button
        text
        :icon="query.order === 'asc' ? SortUp : SortDown"
        @click="
          ((query.order = query.order === 'asc' ? 'desc' : 'asc'),
          (query.page = 1),
          loadImages())
        "
      >
        {{ query.order === "asc" ? "升序" : "降序" }}
      </el-button>

      <el-button
        v-if="query.q || query.filter !== 'all'"
        text
        @click="resetQuery"
        >重置</el-button
      >

      <span class="ep-small ep-faint result-count">{{ total }} 张</span>
    </div>

    <div v-loading="loading" class="gallery-wrap">
      <el-empty
        v-if="!loading && !items.length"
        :description="
          query.filter === 'missing_ocr'
            ? '这个类别没有缺描述的图片'
            : '没有匹配的图片'
        "
      />

      <div v-else class="gallery">
        <ImageCard
          v-for="image in items"
          :key="image.name"
          :img-key="imgKey"
          :image="image"
          @open="openEditor"
        />
      </div>
    </div>

    <el-pagination
      v-if="total > query.page_size"
      v-model:current-page="query.page"
      :page-size="query.page_size"
      :total="total"
      :pager-count="7"
      layout="prev, pager, next, jumper, total"
      class="pager"
      @current-change="loadImages"
    />

    <ImageEditorDrawer
      v-model="editorOpen"
      :img-key="imgKey"
      :image="editing"
      @submitted="onSubmitted"
    />

    <CategoryInfoDialog
      v-model="infoOpen"
      :category="category"
      @submitted="onInfoSubmitted"
    />
  </div>
</template>

<style scoped>
.back {
  margin: 0 0 6px -8px;
  color: var(--ep-ink-muted);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/*
 * 筛选组做成 40px 高、与同行的 large 输入框/下拉齐平。
 * 3px 边框内边距 + 32px 按钮 = 40px。
 */
.filter-group {
  display: inline-flex;
  align-items: center;
  height: 40px;
  background: var(--ep-surface);
  border: 1px solid var(--ep-border);
  border-radius: 999px;
  padding: 0 3px;
  gap: 2px;
}

.filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  border: none;
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--ep-ink-muted);
  padding: 0 14px;
  border-radius: 999px;
  cursor: pointer;
}

.filter-btn:hover {
  background: var(--ep-surface-sunken);
  color: var(--ep-ink);
}

.filter-btn.is-active {
  /* 选中态是「反色药丸」：底色用 ink，字色就得用背景色 ——
     写死 #fff 的话深色下 ink 变成近白，就成了白底白字 */
  background: var(--ep-ink);
  color: var(--ep-bg);
}

.filter-btn em {
  font-style: normal;
  font-size: 11.5px;
  font-weight: 700;
  opacity: 0.6;
}

.filter-btn.is-active em {
  opacity: 0.75;
}

.result-count {
  margin-left: auto;
}

.gallery-wrap {
  min-height: 200px;
}

.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(176px, 1fr));
  gap: 16px;
}

.pager {
  margin-top: 30px;
  justify-content: center;
}

.progress {
  margin-top: 7px;
  height: 5px;
  border-radius: 999px;
  background: var(--ep-accent-track);
  overflow: hidden;
}

.progress > i {
  display: block;
  height: 100%;
  background: var(--ep-ok);
  opacity: 0.85;
}

@media (max-width: 720px) {
  .result-count {
    margin-left: 0;
  }
}
</style>
