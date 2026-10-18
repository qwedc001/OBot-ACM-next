<script setup>
import { computed, reactive, ref, watch } from "vue";
import { Plus } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import {
  fieldLabel,
  fromNow,
  renderLikeDelta,
  renderValue,
  statusMeta,
  truncate,
} from "@/utils/format";
import ImageLightbox from "./ImageLightbox.vue";

/**
 * 一张表情包的编辑表单。
 *
 * 之前每个字段旁边各有一个提交按钮，现在改成「改完一起提交」：
 * 只有一个提交按钮，只把真正改动过的字段发出去（api.pickone.submitBatch）。
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  imgKey: { type: String, required: true },
  image: { type: Object, default: null },
});

const emit = defineEmits(["update:modelValue", "submitted"]);

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

/** 原图地址：抽屉里显示的那张，和点开放大的是同一张 */
const rawUrl = computed(() =>
  props.image ? api.pickone.rawUrl(props.imgKey, props.image.name) : "",
);

/** 放大查看开着没 */
const lightboxOpen = ref(false);

const form = reactive({
  ocr_text: "",
  likes: 0,
  comments: [],
  note: "",
});

const commentDraft = ref("");
const busy = ref(false);
const loadedFor = ref(null);

/** 磁盘上的原值。点赞的原值单独留着，提交的增量要相对它算。 */
const baseLikes = computed(() => Number(props.image?.likes ?? 0));

/** 我自己在途的那条改动：可以接着改；别人的改动只能看。 */
function myPending(field) {
  return (props.image?.pending_changes ?? []).find(
    (item) => item.mine && item.field === field,
  );
}

/**
 * 表单起点：优先「我自己的在途改动」，否则磁盘原值。
 *
 * 别人的在途改动绝不进表单：那样等于替别人「打补丁」，最后整条改动都算在我头上。
 */
const baseline = computed(() => {
  const image = props.image ?? {};
  return {
    ocr_text: myPending("ocr_text")?.value ?? String(image.ocr_text ?? ""),
    likes: baseLikes.value + Number(myPending("likes")?.value ?? 0),
    comments: [...(myPending("comments")?.value ?? image.comments ?? [])],
  };
});

/** 相对磁盘原值的增量，就是提交给后端的值 */
const likeDelta = computed(() => Number(form.likes) - baseLikes.value);

function seed() {
  form.ocr_text = baseline.value.ocr_text;
  form.likes = baseline.value.likes;
  form.comments = [...baseline.value.comments];
  form.note = "";
  commentDraft.value = "";
  loadedFor.value = props.image?.name ?? null;
}

watch(
  () => [props.image, props.modelValue],
  ([image, open]) => {
    if (open && image && image.name !== loadedFor.value) seed();
    if (!open) loadedFor.value = null;
  },
  { immediate: true },
);

/** 单次提交最多能加多少（对应 Bot 一条 /点赞 加 1 个）；总数不封顶 */
const MAX_LIKE_STEP = 10;

const changes = computed(() => {
  const base = baseline.value;
  const reverted = revertedFields.value;
  const list = [];

  if (form.ocr_text !== base.ocr_text && !reverted.includes("ocr_text")) {
    list.push({ field: "ocr_text", label: fieldLabel("ocr_text") });
  }
  // 点赞是增量，必须为正；减回原值属于「改回去了」，提交时走撤销
  if (likeDelta.value > 0 && Number(form.likes) !== base.likes) {
    list.push({ field: "likes", label: fieldLabel("likes") });
  }

  if (
    !sameList(form.comments, base.comments) &&
    !reverted.includes("comments")
  ) {
    list.push({ field: "comments", label: fieldLabel("comments") });
  }

  return list;
});

/** 底部那行说明：点提交会发生什么（更新 / 新增 / 撤销改回去的那几条），一眼能看出来 */
const footHint = computed(() => {
  if (changes.value.length || revertedFields.value.length) {
    // changes 里是 {field, label}，revertedFields 里是字段名本身
    const labels = [
      ...changes.value.map((item) => item.label),
      ...revertedFields.value.map(fieldLabel),
    ];
    const text = `待提交：${labels.join("、")}`;
    return myPendingChanges.value.length ? `${text}（更新你上次的提交）` : text;
  }
  if (myPendingChanges.value.length)
    return "还没有改动（你上次的提交仍在审核中）";
  return "还没有改动";
});

const pendingFields = computed(() => props.image?.pending_fields ?? []);
const pendingChanges = computed(() => props.image?.pending_changes ?? []);

/** 我自己的在途提交：列表里标成绿色 */
const myPendingChanges = computed(() =>
  pendingChanges.value.filter((item) => item.mine),
);

/**
 * 改回去了的字段：我有在途提交，但表单又被改回了磁盘原值。
 *
 * 这类字段不能当成「新改动」提交（后端会判定成没变化），提交时应该把我那条
 * 在途提交撤掉，免得审核台看到一条「改了等于没改」的提交。
 */
const revertedFields = computed(() => {
  const image = props.image ?? {};
  const fields = [];
  if (myPending("ocr_text") && form.ocr_text === String(image.ocr_text ?? "")) {
    fields.push("ocr_text");
  }
  if (myPending("likes") && likeDelta.value <= 0) {
    fields.push("likes");
  }
  if (myPending("comments") && sameList(form.comments, image.comments ?? [])) {
    fields.push("comments");
  }
  return fields;
});

/** 表单现在就是磁盘原值（「还原」没什么可还原的） */
const formAtOriginal = computed(() => {
  const image = props.image ?? {};
  return (
    form.ocr_text === String(image.ocr_text ?? "") &&
    Number(form.likes) === baseLikes.value &&
    sameList(form.comments, image.comments ?? [])
  );
});

function sameList(left, right) {
  return (
    left.length === right.length &&
    left.every((item, index) => item === right[index])
  );
}

/** 在途改动的可读值：点赞是增量，其它是目标值 */
function pendingText(item) {
  if (item.field === "likes") return renderLikeDelta(item.value);
  return truncate(renderValue(item.value), 40);
}

function pendingWho(item) {
  return item.mine ? "我" : item.author_name;
}

/** 该字段的磁盘原值，和每一条在途改动对照着看 */
function originalText(field) {
  const image = props.image ?? {};
  if (field === "likes") return `${baseLikes.value} 个赞`;
  return truncate(renderValue(image[field]), 24);
}

function addLike() {
  const next = Number(form.likes) + 1;
  if (next - baseLikes.value > MAX_LIKE_STEP) {
    ElMessage.warning(`一次最多加 ${MAX_LIKE_STEP} 个赞`);
    return;
  }
  form.likes = next;
}

function undoLike() {
  // 可以一直减回磁盘原值；减到原值就是「这条加赞我不要了」，提交时会撤销那条
  if (Number(form.likes) > baseLikes.value) form.likes = Number(form.likes) - 1;
}

function addComment() {
  const text = commentDraft.value.trim();
  if (!text) return;
  if (text.length > 32) {
    ElMessage.warning("单条评论不能超过 32 字");
    return;
  }
  if (form.comments.includes(text)) {
    ElMessage.warning("这条评论已经有了");
    return;
  }
  form.comments.push(text);
  commentDraft.value = "";
}

function removeComment(index) {
  form.comments.splice(index, 1);
}

/**
 * 还原：只把这个对话框里的输入复位到磁盘原值，不动任何提交。
 *
 * 有在途提交时，复位后表单就和「原值」一致了 —— 这时提交会把那条在途提交撤掉
 * （见 submit），而不是提交一条没有变化的改动。
 */
function reset() {
  const image = props.image ?? {};
  form.ocr_text = String(image.ocr_text ?? "");
  form.likes = Number(image.likes ?? 0);
  form.comments = [...(image.comments ?? [])];
  form.note = "";
  commentDraft.value = "";
}

async function submit() {
  const reverted = revertedFields.value;
  if (!changes.value.length && !reverted.length) return;

  if (reverted.length) {
    try {
      await ElMessageBox.confirm(
        `${reverted.map(fieldLabel).join("、")} 已经被你改回原值，提交后会撤销这些审核中的改动`,
        "有字段改回了原值",
        { type: "warning", confirmButtonText: "确定" },
      );
    } catch {
      return;
    }
  }

  busy.value = true;
  try {
    for (const field of reverted) {
      const item = myPending(field);
      if (item) await api.pickone.withdraw(item.submission_id);
    }

    if (changes.value.length) {
      const payload = { name: props.image.name, note: form.note.trim() };
      for (const item of changes.value) {
        if (item.field === "ocr_text") payload.ocr_text = form.ocr_text;
        if (item.field === "likes") payload.likes_delta = likeDelta.value;
        if (item.field === "comments") payload.comments = [...form.comments];
      }

      const result = await api.pickone.submitBatch(props.imgKey, payload);
      const skipped = result.skipped?.length ?? 0;
      if (skipped) {
        ElMessage.warning(
          `${result.message}。${result.skipped.map((item) => `${item.reason}`).join("；")}`,
        );
      } else {
        ElMessage.success(
          myPendingChanges.value.length
            ? "已更新你上次的提交，等待管理员审核"
            : "已提交，等待管理员审核",
        );
      }
    } else {
      ElMessage.success("已撤销改回原值的提交");
    }

    visible.value = false;
    emit("submitted");
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    busy.value = false;
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    ElMessage.success("已复制");
  } catch {
    ElMessage.warning("复制失败");
  }
}
</script>

<template>
  <el-drawer
    v-model="visible"
    size="min(600px, 92vw)"
    :title="image ? image.hash_id : '编辑'"
    destroy-on-close
  >
    <template v-if="image">
      <div class="editor-top">
        <!-- 点图放大：抽屉里这张只有 200px 宽，要看细节不必另开标签页 -->
        <button type="button" class="editor-media" @click="lightboxOpen = true">
          <img :src="rawUrl" :alt="image.name" />
        </button>
        <div class="editor-facts">
          <div class="fact">
            <span class="ep-faint ep-small">展示 ID</span>
            <span class="ep-mono">
              {{ image.hash_id }}
            </span>
          </div>
          <div class="fact">
            <span class="ep-faint ep-small">图片 MD5</span>
            <span class="ep-mono ep-small ep-break">
              {{ image.md5 }}
            </span>
          </div>
          <div class="fact">
            <span class="ep-faint ep-small">提起次数 / 添加时间</span>
            <span class="ep-small"
              >{{ image.pickup_times }} 次 · {{ fromNow(image.add_time) }}</span
            >
          </div>
          <div v-if="pendingFields.length" class="fact">
            <span class="ep-faint ep-small">
              {{
                myPendingChanges.length ? "审核中（包含你的更改）" : "审核中"
              }}
            </span>
            <span class="ep-chip ep-chip--accent">
              {{ pendingFields.map(fieldLabel).join("、") }}
            </span>
          </div>
        </div>
      </div>

      <!--
        审核中的改动：磁盘原值 → 每条在途改动，连作者一起列出来（可能不止一个人）。
        绿色的是我自己的那条 —— 它已经回填进下面的表单，继续改就是给它打补丁。
        别人的内容只读：能看见，但不能作为修改起点，否则会把他们的改动并进我这条提交里。
      -->
      <div v-if="pendingChanges.length" class="pending-box">
        <div class="pending-title ep-small">
          审核中的改动（{{ pendingChanges.length }} 条）
        </div>
        <div
          v-for="item in pendingChanges"
          :key="item.submission_id"
          class="pending-row"
        >
          <span class="ep-chip" :class="item.mine ? 'ep-chip--ok' : ''">
            {{ fieldLabel(item.field) }}
          </span>
          <span class="ep-small ep-muted pending-who">
            {{ pendingWho(item) }} · {{ statusMeta(item.status).short }}
          </span>
          <span
            class="pending-value ep-small"
            :title="`${originalText(item.field)} → ${pendingText(item)}`"
          >
            <span class="pending-old">{{ originalText(item.field) }}</span>
            <span class="pending-arrow">→</span>
            <span class="pending-new">{{ pendingText(item) }}</span>
          </span>
        </div>
      </div>

      <el-form
        label-position="top"
        class="editor-form"
        @submit.prevent="submit"
      >
        <el-form-item label="图片描述">
          <el-input
            v-model="form.ocr_text"
            type="textarea"
            :rows="3"
            maxlength="512"
            show-word-limit
            placeholder="图片里的文字，或对图片内容的简短描述"
            size="large"
          />
          <div v-if="!String(image.ocr_text ?? '').trim()" class="field-hint">
            这张图还没有描述
          </div>
          <div v-else class="field-hint">
            磁盘当前值：{{ renderValue(image.ocr_text) }}
          </div>
        </el-form-item>

        <el-form-item label="点赞">
          <div class="like-box">
            <span class="like-count">
              <b>{{ form.likes }}</b>
              <span class="ep-small ep-faint">个赞</span>
            </span>

            <el-button plain :icon="Plus" @click="addLike" size="large"
              >加一个赞</el-button
            >

            <el-button
              text
              :disabled="likeDelta <= 0"
              @click="undoLike"
              size="large"
              >撤销</el-button
            >

            <span class="ep-small ep-faint">
              <template v-if="likeDelta > 0"
                >本次 +{{ likeDelta }}，提交后 {{ form.likes }}</template
              >
              <template v-else
                >原值 {{ baseLikes }} 个（一次最多加
                {{ MAX_LIKE_STEP }} 个）</template
              >
            </span>
          </div>
        </el-form-item>

        <el-form-item label="评论">
          <div class="comment-box">
            <el-tag
              v-for="(comment, index) in form.comments"
              :key="`${comment}-${index}`"
              closable
              size="large"
              @close="removeComment(index)"
            >
              {{ comment }}
            </el-tag>
            <span v-if="!form.comments.length" class="ep-small ep-faint"
              >暂无评论</span
            >
          </div>
          <div class="comment-add">
            <el-input
              v-model="commentDraft"
              maxlength="32"
              show-word-limit
              placeholder="最多 32 字，回车添加"
              @keyup.enter="addComment"
              size="large"
            />
            <el-button @click="addComment" size="large">添加</el-button>
          </div>
        </el-form-item>

        <el-form-item label="备注给审核者（可选）">
          <el-input
            v-model="form.note"
            maxlength="200"
            placeholder="比如：这几张图的文字是同一个梗"
            size="large"
          />
        </el-form-item>
      </el-form>
    </template>

    <!--
      底部操作栏放在 drawer 的 footer 插槽里，而不是 body 内的 sticky 元素：
      sticky 版本会在滚动内容与按钮之间留出一条缝，底下的表单内容会透出来。
    -->
    <template #footer>
      <div class="editor-foot">
        <span
          class="ep-small"
          :class="
            changes.length || revertedFields.length ? 'ep-muted' : 'ep-faint'
          "
          >{{ footHint }}</span
        >
        <div class="editor-foot-actions">
          <el-button :disabled="formAtOriginal || busy" @click="reset"
            >还原</el-button
          >
          <el-button
            type="primary"
            :disabled="!changes.length && !revertedFields.length"
            :loading="busy"
            @click="submit"
          >
            {{ myPendingChanges.length ? "更新我的提交" : "提交审核" }}
          </el-button>
        </div>
      </div>
    </template>
  </el-drawer>

  <ImageLightbox
    :open="lightboxOpen"
    :list="rawUrl ? [rawUrl] : []"
    @close="lightboxOpen = false"
  />
</template>

<style scoped>
/*
 * 左图与右侧信息区等高：图片拉满整列高度，右侧信息 justify-content: space-between
 * 撑开。之前图片是 max-height 自适应，右侧内容一多就比图片高，看着不平衡。
 */
.editor-top {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 22px;
  margin-bottom: 28px;
  align-items: stretch;
}

/* 棋盘格边框留在 el-image 的根元素上，图片本身由内层 img 撑满 */
.editor-media {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 196px;
  max-height: 236px;
  overflow: hidden;
  cursor: zoom-in;
  border-radius: var(--ep-radius);
  border: 1px solid var(--ep-border);
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

.editor-media img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.editor-facts {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.fact {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.editor-form :deep(.el-form-item) {
  margin-bottom: 12px;
}

/* 在途改动：只读信息块，跟可编辑的表单在视觉上分开 */
.pending-box {
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius-sm);
  background: var(--ep-surface-sunken);
  padding: 10px 12px;
  margin-bottom: 18px;
}

.pending-title {
  font-weight: 700;
  color: var(--ep-ink);
  margin-bottom: 8px;
}
.pending-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 2px 0;
  min-width: 0;
}

.pending-who {
  flex: none;
}

.pending-value {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
  overflow: hidden;
  white-space: nowrap;
}

.pending-old {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--ep-ink-faint);
  text-decoration: line-through;
}

.pending-arrow {
  flex: none;
  color: var(--ep-ink-faint);
}

.pending-new {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  color: var(--ep-ink);
}

.pending-note {
  margin-top: 8px;
  line-height: 1.6;
}

.editor-form :deep(.el-form-item__label) {
  font-weight: 700;
  color: var(--ep-ink);
  padding-bottom: 6px;
}

.field-hint {
  font-size: 12.5px;
  color: var(--ep-ink-faint);
  line-height: 1.6;
  margin-top: 6px;
  margin-bottom: 16px;
}

.field-inline {
  margin-left: 12px;
}

/* 点赞：只加不减，所以是「计数 + 加一 + 撤销」而不是数字输入框 */
.like-box {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
  margin-bottom: 16px;
}

.like-count {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  min-width: 62px;
}

.like-count b {
  font-size: 24px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.comment-box {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 28px;
  align-items: center;
}

.comment-add {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  width: 100%;
  margin-bottom: 16px;
}

.editor-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  width: 100%;
}

.editor-foot-actions {
  display: flex;
  gap: 10px;
}

/* flex + gap 已经给足间距，不要再叠加 .el-button 的相邻 margin */
.editor-foot-actions .el-button + .el-button,
.like-box .el-button + .el-button {
  margin-left: 0;
}
</style>
