<script setup>
import { onBeforeUnmount, ref, watch } from "vue";

/**
 * 图片放大查看。
 *
 * 为什么不直接用 `el-image` 的 `preview-src-list`：它的 `el-image-viewer` 是被
 * `v-if` 挂上去的，关闭时整个 viewer 组件一起卸载 —— 而 Vue 对「正在卸载的组件里的
 * `<transition>`」会直接跳过离场动画（`state.isUnmounting` 那条分支里 `return remove()`）。
 * 结果就是放大图淡入进来、却「啪」地消失。
 *
 * 所以离场不交给它的内部过渡：外面这层 host 自己淡出 200ms，动画走完才把 viewer 卸掉。
 * 缩放 / 旋转 / 左右翻页仍然全部由 `el-image-viewer` 提供。
 *
 * host 用 `<Teleport to="body">`：调用方可能在卡片、抽屉里，那些祖先带 transform /
 * overflow:hidden，`position: fixed` 会被它们当成包含块而裁掉。
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  /** 可翻看的原图列表 */
  list: { type: Array, default: () => [] },
  /** 从第几张开始 */
  index: { type: Number, default: 0 },
});

const emit = defineEmits(["close"]);

/** 淡出动画的时长，与下面 keyframes 里的 .2s 必须一致 */
const LEAVE_MS = 200;

const closing = ref(false);
let timer = 0;

// 下次打开时把状态清干净（上一次的定时器也要收掉，否则会把新开的那次关掉）
watch(
  () => props.open,
  (open) => {
    if (!open) return;
    window.clearTimeout(timer);
    closing.value = false;
  },
);

function close() {
  if (closing.value) return;
  closing.value = true;
  timer = window.setTimeout(() => {
    closing.value = false;
    emit("close");
  }, LEAVE_MS);
}

onBeforeUnmount(() => window.clearTimeout(timer));
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="lightbox" :class="{ 'is-closing': closing }">
      <el-image-viewer
        :url-list="list"
        :initial-index="index"
        hide-on-click-modal
        @close="close"
      />
    </div>
  </Teleport>
</template>

<style scoped>
.lightbox {
  position: fixed;
  inset: 0;
  /* 压在抽屉（2000+）之上 */
  z-index: 3000;
  animation: lightbox-in 0.24s ease-out;
}

.lightbox.is-closing {
  animation: lightbox-out 0.2s ease-in forwards;
  /* 淡出期间别再点到它，免得点两下 */
  pointer-events: none;
}

@keyframes lightbox-in {
  from {
    opacity: 0;
  }
}

@keyframes lightbox-out {
  to {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .lightbox,
  .lightbox.is-closing {
    animation: none;
  }
}
</style>
