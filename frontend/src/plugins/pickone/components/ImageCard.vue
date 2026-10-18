<script setup>
import { ChatDotRound, Star } from "@element-plus/icons-vue";
import { computed } from "vue";
import { api } from "@/api";
import {
  fieldLabel,
  renderLikeDelta,
  renderValue,
  statusMeta,
  truncate,
} from "@/utils/format";

const props = defineProps({
  imgKey: { type: String, required: true },
  image: { type: Object, required: true },
});

defineEmits(["open"]);

const thumbUrl = computed(() =>
  api.pickone.thumbUrl(props.imgKey, props.image.name),
);
const text = computed(() => String(props.image.ocr_text ?? ""));
const empty = computed(() => !text.value.trim());
const alias = computed(() =>
  empty.value ? "缺文字" : truncate(text.value, 34),
);

/**
 * 审核中的改动：卡片上照常显示磁盘原值，另外把每条在途改动连作者一起列出来。
 * 同一张图可能有好几个人同时在改，所以是一个列表，不是「有没有人改」的一个标记。
 */
const pending = computed(() => props.image.pending_changes ?? []);
const shownPending = computed(() => pending.value.slice(0, 2));
const hiddenPending = computed(
  () => pending.value.length - shownPending.value.length,
);

function who(item) {
  return item.mine ? "我" : item.author_name;
}

function pendingLine(item) {
  const value =
    item.field === "likes"
      ? `${renderLikeDelta(item.value)} 赞`
      : renderValue(item.value);
  return `${fieldLabel(item.field)}：${truncate(value, 18)}`;
}

function pendingTitle(item) {
  return `${who(item)} · ${statusMeta(item.status).short} · ${pendingLine(item)}`;
}
</script>

<template>
  <button
    class="shot"
    :class="{ 'is-empty': empty, 'is-pending': image.has_pending_change }"
    @click="$emit('open', image)"
  >
    <span class="shot-media">
      <img :src="thumbUrl" :alt="image.name" loading="lazy" />
      <span v-if="image.has_pending_change" class="shot-flag">
        {{ pending.length > 1 ? `审核中 ${pending.length}` : "待审核" }}
      </span>
    </span>

    <span class="shot-body">
      <span class="shot-text" :class="{ 'is-empty': empty }" :title="text">
        {{ alias }}
      </span>

      <span v-if="pending.length" class="shot-pending">
        <span
          v-for="item in shownPending"
          :key="item.submission_id"
          class="shot-pending-row"
          :title="pendingTitle(item)"
        >
          <b>{{ who(item) }}</b>
          <i>{{ statusMeta(item.status).short }}</i>
          <em>{{ pendingLine(item) }}</em>
        </span>
        <span v-if="hiddenPending" class="shot-pending-more"
          >还有 {{ hiddenPending }} 条</span
        >
      </span>

      <span class="shot-meta">
        <span class="shot-id ep-mono">{{ image.hash_id }}</span>
        <span class="shot-nums">
          <el-icon><Star /></el-icon>{{ image.likes }}
          <el-icon><ChatDotRound /></el-icon>{{ image.comments.length }}
        </span>
      </span>
    </span>

    <span class="shot-bar"
      ><i :style="{ width: empty ? '100%' : '0%' }"
    /></span>
  </button>
</template>

<style scoped>
.shot {
  display: flex;
  flex-direction: column;
  padding: 0;
  font: inherit;
  color: inherit;
  text-align: left;
  background: var(--ep-surface);
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius);
  overflow: hidden;
  cursor: pointer;
  box-shadow: var(--ep-shadow-sm);
  transition:
    border-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.shot:hover {
  transform: translateY(-3px);
  border-color: var(--ep-border-strong);
  box-shadow: var(--ep-shadow-md);
}

.shot.is-empty {
  border-color: rgba(178, 106, 0, 0.35);
}

.shot.is-pending {
  border-color: var(--el-color-primary);
  box-shadow:
    0 0 0 1px var(--el-color-primary-light-7),
    var(--ep-shadow-sm);
}

.shot-media {
  position: relative;
  aspect-ratio: 1 / 1;
  display: grid;
  place-items: center;
  /* 棋盘格底，透明 GIF 看得出来 */
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
  overflow: hidden;
}

.shot-media img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.shot-flag {
  position: absolute;
  top: 9px;
  left: 9px;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background-image: var(--ep-brand-gradient-deep);
  border-radius: 999px;
  padding: 2px 10px;
  box-shadow: 0 2px 8px rgba(78, 127, 112, 0.35);
}

.shot-body {
  display: block;
  padding: 13px 15px 15px;
}

.shot-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  /* 固定两行高度，卡片就不会因为文字长短而高低不齐 */
  height: 41px;
  font-size: 13.5px;
  line-height: 1.52;
  color: var(--ep-ink);
  word-break: break-all;
}

.shot-text.is-empty {
  color: var(--ep-warn);
}

/* 审核中的改动：卡片上直接列出内容与作者，不用点开才知道改了什么 */
.shot-pending {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--ep-border);
}

.shot-pending-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
  min-width: 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--ep-ink-muted);
}

.shot-pending-row b {
  flex: none;
  max-width: 60px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 700;
  color: var(--ep-ink);
}

.shot-pending-row i {
  flex: none;
  font-style: normal;
  font-size: 10.5px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--ep-accent-tint, rgba(158, 87, 114, 0.07));
  color: var(--el-color-primary);
}

.shot-pending-row em {
  flex: 1 1 auto;
  min-width: 0;
  font-style: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shot-pending-more {
  font-size: 11px;
  color: var(--ep-ink-faint);
}

.shot-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--ep-ink-faint);
}

.shot-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shot-nums {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex: none;
}

.shot-nums .el-icon {
  font-size: 12px;
}

.shot-nums .el-icon + .el-icon {
  margin-left: 5px;
}

.shot-bar {
  display: block;
  height: 4px;
  /* 网格里同一行的卡片会被拉成等高，有审核信息的那张更高：
     用 auto margin 把进度条压到底部，免得旁边没审核信息的卡片下面空一截 */
  margin-top: auto;
  background: rgba(178, 106, 0, 0.14);
}

.shot-bar > i {
  display: block;
  height: 100%;
  background-image: linear-gradient(90deg, #e0a33c, var(--ep-warn));
  opacity: 0.9;
}
</style>
