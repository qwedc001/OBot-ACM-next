<script setup>
import { Check, Close, Refresh, Search } from "@element-plus/icons-vue";
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import ImageLightbox from "../components/ImageLightbox.vue";

const loading = ref(true);
const items = ref([]);
const total = ref(0);
const query = reactive({ img_key: "", page: 1, page_size: 48 });

/** 放大查看：开着没、从第几张开始 */
const lightbox = reactive({ open: false, index: 0 });

const emptyHint = computed(() =>
  query.img_key.trim() ? "这个类别没有待审图片" : "当前没有待审图片",
);

/**
 * 放大查看用的原图列表。
 *
 * 不是只放大点中的那一张：把整页待审图都挂上，放大后可以左右翻看，
 * 一轮审下来不用退回列表再点下一张。
 */
const previewList = computed(() => items.value.map((item) => rawUrl(item)));

function openLightbox(index) {
  lightbox.index = index;
  lightbox.open = true;
}

function thumbUrl(item) {
  return api.pickone.auditThumbUrl(item.img_key, item.name);
}

function rawUrl(item) {
  return api.pickone.auditRawUrl(item.img_key, item.name);
}

function formatTime(value) {
  if (!value) return "未知时间";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

async function load() {
  loading.value = true;
  try {
    const data = await api.pickone.auditQueue({
      ...query,
      img_key: query.img_key.trim(),
    });
    items.value = data.items;
    total.value = data.total;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    loading.value = false;
  }
}

function applyFilter() {
  query.page = 1;
  load();
}

async function approve(item) {
  try {
    await ElMessageBox.confirm(
      `通过后会把这张图片移动到正式类别「${item.category_id}」，确定吗？`,
      "通过上游图片",
      { type: "warning", confirmButtonText: "确认通过" },
    );
  } catch {
    return;
  }
  try {
    const result = await api.pickone.approveAudit(item.img_key, item.name);
    ElMessage.success(
      result.status === "duplicate"
        ? "正式目录已有同图，已清理重复待审文件"
        : "已通过并写入正式目录",
    );
    await load();
  } catch (error) {
    ElMessage.error(error.message);
  }
}

async function reject(item) {
  try {
    await ElMessageBox.confirm(
      "驳回会删除待审目录里的原文件，本站无法恢复。确定吗？",
      "驳回上游图片",
      { type: "error", confirmButtonText: "确认驳回" },
    );
  } catch {
    return;
  }
  try {
    await api.pickone.rejectAudit(item.img_key, item.name);
    ElMessage.success("已驳回并删除待审文件");
    await load();
  } catch (error) {
    ElMessage.error(error.message);
  }
}

onMounted(load);
</script>

<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">待审图片</h1>
        <p class="ep-subtitle">
          上游 Bot 投递进 __AUDIT__ 的图片，通过即移入正式类别
        </p>
      </div>
      <div class="ep-actions">
        <el-button
          :loading="loading"
          :icon="Refresh"
          size="large"
          @click="load"
        >
          刷新
        </el-button>
      </div>
    </div>

    <!-- 与「提交审核」同一条工具条：同样的控件尺寸，总数一样靠右 -->
    <div class="toolbar ep-mb">
      <el-input
        v-model="query.img_key"
        size="large"
        placeholder="按类别标识过滤"
        clearable
        style="width: 220px"
        @keyup.enter="applyFilter"
        @clear="applyFilter"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>
      <el-button size="large" @click="applyFilter">查询</el-button>
      <span class="ep-small ep-faint total-hint">共 {{ total }} 张</span>
    </div>

    <!-- 没有 tag 就没有包裹元素：卡片仍是 grid 的直接子项，动效不影响排版 -->
    <div class="audit-grid">
      <transition-group name="audit-card">
        <!--
          卡片结构照图鉴那张来（ImageCard.vue）：图片顶到卡片最上面、占满整行，
          信息与操作在下面。用 el-card 做不到 —— 它自带一圈 body 内边距，
          图永远缩在中间。
        -->
        <div
          v-for="(item, index) in items"
          :key="`${item.img_key}/${item.name}`"
          class="audit-card"
        >
          <!--
            点图放大：不再跳新标签页，也不再用 el-image 自带的预览
            （它的离场动画被 Vue 跳过，见 ImageLightbox.vue 里的说明）。
          -->
          <button
            type="button"
            class="audit-media"
            @click="openLightbox(index)"
          >
            <img :src="thumbUrl(item)" :alt="item.name" loading="lazy" />
          </button>
          <div class="audit-info">
            <div class="audit-category">
              <span class="ep-mono">{{ item.img_key }}</span>
              <span>{{ item.category_id }}</span>
            </div>
            <div class="audit-id ep-mono" :title="item.hash_id">
              {{ item.hash_id }}
            </div>
            <div class="audit-time">
              加入待审：{{ formatTime(item.add_time) }}
            </div>
          </div>
          <div class="audit-actions">
            <el-button type="success" plain :icon="Check" @click="approve(item)"
              >通过</el-button
            >
            <el-button type="danger" plain :icon="Close" @click="reject(item)"
              >驳回</el-button
            >
          </div>
        </div>
      </transition-group>
    </div>

    <el-empty v-if="!loading && !items.length" :description="emptyHint" />
    <el-pagination
      v-if="total > query.page_size"
      v-model:current-page="query.page"
      :page-size="query.page_size"
      :total="total"
      layout="prev, pager, next, total"
      class="pager"
      @current-change="load"
    />

    <ImageLightbox
      :open="lightbox.open"
      :list="previewList"
      :index="lightbox.index"
      @close="lightbox.open = false"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.total-hint {
  margin-left: auto;
}

.audit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 16px;
}

/* 卡片外壳与图鉴那张（ImageCard.vue 的 .shot）保持一致 */
.audit-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--ep-surface);
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius);
  overflow: hidden;
  box-shadow: var(--ep-shadow-sm);
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease,
    border-color 0.16s ease;
}

.audit-card:hover {
  transform: translateY(-3px);
  border-color: var(--ep-border-strong);
  box-shadow: var(--ep-shadow-md);
}

/*
 * 通过 / 驳回之后卡片是「真的少了」：淡出并缩一点，剩下的位置由 -move 平滑补上，
 * 而不是整片网格硬闪一下。
 */
.audit-card-enter-active,
.audit-card-leave-active,
.audit-card-move {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}

.audit-card-enter-from,
.audit-card-leave-to {
  opacity: 0;
  transform: scale(0.94);
}

/* 图片顶到卡片最上面、占满整行（棋盘格与图鉴那张同一套四层渐变）。
   这块是 <button>：点图放大，键盘也能聚焦回车。 */
.audit-media {
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 1 / 1;
  padding: 0;
  border: 0;
  overflow: hidden;
  /* 可点：点开是放大查看，不是跳链接 */
  cursor: zoom-in;
  background-color: var(--ep-surface-sunken);
  background-image:
    linear-gradient(45deg, rgba(20, 22, 31, 0.035) 25%, transparent 25%),
    linear-gradient(-45deg, rgba(20, 22, 31, 0.035) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, rgba(20, 22, 31, 0.035) 75%),
    linear-gradient(-45deg, transparent 75%, rgba(20, 22, 31, 0.035) 75%);
  background-size: 18px 18px;
  background-position:
    0 0,
    0 9px,
    9px -9px,
    -9px 0;
}

.audit-media img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.25s ease;
}

/* 悬停轻微放大，先告诉人「这里可以点开看大的」 */
.audit-media:hover img {
  transform: scale(1.04);
}

/* 图鉴那边是 13px 15px；这里拆成信息 / 操作两块，左右沿用同一组缩进 */
.audit-info {
  padding: 13px 15px 0;
  min-width: 0;
}

.audit-category,
.audit-time {
  color: var(--ep-ink-muted);
  font-size: 12px;
}

.audit-category {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
}

.audit-id {
  margin-top: 7px;
  overflow: hidden;
  color: var(--ep-ink);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-time {
  margin-top: 5px;
  color: var(--ep-ink-faint);
  font-size: 11px;
}

/* margin-top: auto 把操作区压到底：同一行卡片被拉等高时按钮也对齐 */
.audit-actions {
  display: flex;
  gap: 8px;
  margin-top: auto;
  padding: 12px 15px 15px;
}

.audit-actions .el-button {
  flex: 1 1 0;
  margin-left: 0;
}

.pager {
  justify-content: flex-end;
  margin-top: 18px;
}

@media (max-width: 560px) {
  .audit-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }

  .audit-info {
    padding: 10px 10px 0;
  }

  .audit-actions {
    display: grid;
    grid-template-columns: 1fr;
    padding: 10px;
  }

  /* 换行之后「共 N 张」不该再顶着右边 */
  .total-hint {
    margin-left: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .audit-card,
  .audit-card-enter-active,
  .audit-card-leave-active,
  .audit-card-move {
    transition: none;
  }
}
</style>
