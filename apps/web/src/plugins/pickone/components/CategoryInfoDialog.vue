<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import { fieldLabel, renderValue, statusMeta } from "@/utils/format";

/**
 * 类别显示名与别名的编辑表单（一个表单一个提交按钮）。
 *
 * 别名用 chips 而不是一段文本：用户不会知道该用什么分隔符，
 * 输中文逗号「，」是常事。现在每次输入一个别名，回车/逗号都能收起成一个 chip，
 * 逗号只作为「收下这一项」的触发键，不会进到值里。
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  category: { type: Object, default: null },
});

const emit = defineEmits(["update:modelValue", "submitted"]);

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const form = reactive({ category_id: "", keys: [], note: "" });
const draft = ref("");
const busy = ref(false);
const loadedFor = ref(null);
const draftInput = ref(null);

/** 我自己在途的那条改动：可以接着改；别人的只能看。 */
function myPending() {
  return (
    (props.category?.pending_changes ?? []).find((item) => item.mine) ?? null
  );
}

/** 有我的在途提交时，表单起点就是它 —— 提交是更新那条，不是新建 */
const myPendingItem = computed(() => myPending());

/** 表单起点：我的在途改动优先，否则磁盘原值（别人的在途改动不能当起点） */
const baseline = computed(() => {
  const mine = myPending();
  return {
    id: mine?.value?.id ?? props.category?.id ?? "",
    keys: [...(mine?.value?.keys ?? props.category?.keys ?? [])],
  };
});

/** 审核中的改动：所有人的都列出来，我自己的标绿 */
const allPending = computed(() => props.category?.pending_changes ?? []);

/**
 * 在途改动展开成逐字段的行，和图片编辑抽屉一套提示方式：
 * 字段 chip（绿色是我的）· 作者 + 状态 · 磁盘原值 → 这条改成的值。
 * 一条类别改动同时带显示名和别名，所以可能出两行。
 */
const pendingRows = computed(() => {
  const category = props.category ?? {};
  const rows = [];

  for (const item of allPending.value) {
    const value = item.value ?? {};
    const who = item.mine ? "我" : item.author_name;
    const status = statusMeta(item.status).short;

    const beforeId = category.id ?? "";
    const afterId = value.id ?? "";
    if (beforeId !== afterId) {
      rows.push({
        key: `${item.submission_id}-id`,
        field: "id",
        who,
        status,
        mine: item.mine,
        before: renderValue(beforeId),
        after: renderValue(afterId),
      });
    }

    const beforeKeys = Array.isArray(category.keys) ? category.keys : [];
    const afterKeys = Array.isArray(value.keys) ? value.keys : [];
    if (beforeKeys.join("|") !== afterKeys.join("|")) {
      rows.push({
        key: `${item.submission_id}-keys`,
        field: "keys",
        who,
        status,
        mine: item.mine,
        before: renderValue(beforeKeys),
        after: renderValue(afterKeys),
      });
    }
  }

  return rows;
});

function seed() {
  form.category_id = baseline.value.id;
  form.keys = [...baseline.value.keys];
  form.note = "";
  draft.value = "";
  loadedFor.value = props.category?.img_key ?? null;
}

watch(
  () => [props.category, props.modelValue],
  ([category, open]) => {
    if (open && category && category.img_key !== loadedFor.value) seed();
    if (!open) loadedFor.value = null;
  },
  { immediate: true },
);

const isNew = computed(() => props.category?.is_pending_new);

/**
 * 别名列表是否等价。
 *
 * 后端判断「和现值相同」时是按集合比的（别名换个顺序不算改动），
 * 前端必须用同一套规则，否则会出现「提交后被告知没有改动」这种自相矛盾的提示。
 */
function sameKeys(left, right) {
  return [...left].sort().join("|") === [...right].sort().join("|");
}

/**
 * 改回原值了：我有在途提交，但表单又和磁盘原值一致。
 * 这时不该再提交一条「改了等于没改」的改动，而是把我那条撤掉。
 */
const reverted = computed(() => {
  // 待创建的新类别没有「原值」可比，不存在改回去这回事
  if (isNew.value || !myPendingItem.value) return false;
  const category = props.category ?? {};
  return (
    form.category_id.trim() === (category.id ?? "") &&
    sameKeys(form.keys, category.keys ?? [])
  );
});

const changes = computed(() => {
  if (!props.category || reverted.value) return [];
  const base = baseline.value;
  const list = [];

  if (form.category_id.trim() !== base.id) list.push("显示名");
  if (!sameKeys(form.keys, base.keys)) list.push("别名");

  return list;
});

/** 我那条在途提交实际动了哪些字段：改回原值 / 撤销时用它来提示，不能把没动过的字段也算上 */
const myPendingFields = computed(() => {
  const mine = myPendingItem.value;
  if (!mine) return [];
  const category = props.category ?? {};
  const value = mine.value ?? {};
  const fields = [];
  if ((value.id ?? "") !== (category.id ?? "")) fields.push("显示名");
  if (!sameKeys(value.keys ?? [], category.keys ?? [])) fields.push("别名");
  return fields;
});

/** 底部那行说明：和图片编辑抽屉一套说法（待提交 / 还没有改动） */
const footHint = computed(() => {
  const labels = [
    ...changes.value,
    ...(reverted.value ? myPendingFields.value : []),
  ];
  if (labels.length) {
    const text = `待提交：${labels.join("、")}`;
    return myPendingItem.value ? `${text}（更新你上次的提交）` : text;
  }
  if (myPendingItem.value) return "还没有改动（你上次的提交仍在审核中）";
  return "还没有改动";
});

/** 把输入框里的一段文本按中英文逗号/顿号/换行切开，逐个收下 */
function commitDraft() {
  const raw = draft.value;
  if (!raw) return;

  let added = 0;
  for (const piece of raw.split(/[,，、\n]/)) {
    const text = piece.trim();
    if (!text) continue;
    if (text.length > 64) {
      ElMessage.warning(`别名太长：${text.slice(0, 12)}…`);
      continue;
    }
    // 大小写不敏感去重
    if (form.keys.some((item) => item.toLowerCase() === text.toLowerCase())) {
      ElMessage.warning(`「${text}」已经在列表里了`);
      continue;
    }
    form.keys.push(text);
    added += 1;
  }

  draft.value = "";
  return added;
}

function addAlias() {
  commitDraft();
  nextTick(() => draftInput.value?.focus());
}

function removeAlias(index) {
  form.keys.splice(index, 1);
}

async function submit() {
  commitDraft();
  if (!changes.value.length && !reverted.value) return;
  if (!form.keys.length) {
    ElMessage.warning("至少保留一个别名");
    return;
  }

  if (reverted.value) {
    try {
      await ElMessageBox.confirm(
        `${myPendingFields.value.join("、")} 已经改回原值，提交后会撤销你上次的类别改动`,
        "改回了原值",
        { type: "warning", confirmButtonText: "确定" },
      );
    } catch {
      return;
    }
  }

  busy.value = true;
  try {
    if (reverted.value) {
      await api.pickone.withdraw(myPendingItem.value.submission_id);
      ElMessage.success("已撤销改回原值的提交");
    } else {
      await api.pickone.submit(
        props.category.img_key,
        isNew.value ? "category_create" : "category",
        {
          category_id: form.category_id.trim() || props.category.img_key,
          keys: [...form.keys],
          note: form.note.trim(),
        },
      );
      ElMessage.success(
        myPendingItem.value
          ? "已更新你上次的提交，等待管理员审核"
          : "已提交，等待管理员审核",
      );
    }
    visible.value = false;
    emit("submitted");
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="isNew ? '编辑待创建类别' : `修改「${category?.id ?? ''}」`"
    size="min(520px, 92vw)"
  >
    <div v-if="pendingRows.length" class="pending-box">
      <div class="pending-title ep-small">
        审核中的改动（{{ allPending.length }} 条）
      </div>
      <div v-for="row in pendingRows" :key="row.key" class="pending-row">
        <span class="ep-chip" :class="row.mine ? 'ep-chip--ok' : ''">
          {{ fieldLabel(row.field) }}
        </span>
        <span class="ep-small ep-muted pending-who"
          >{{ row.who }} · {{ row.status }}</span
        >
        <span
          class="ep-small pending-value"
          :title="`${row.before} → ${row.after}`"
        >
          <span class="pending-old">{{ row.before }}</span>
          <span class="pending-arrow">→</span>
          <span class="pending-new">{{ row.after }}</span>
        </span>
      </div>
    </div>

    <el-form label-position="top">
      <el-form-item label="类别标识">
        <el-input :model-value="category?.img_key" disabled size="large" />
      </el-form-item>

      <el-form-item label="显示名">
        <el-input
          v-model="form.category_id"
          placeholder="Bot 回复里显示的名字"
          size="large"
        />
      </el-form-item>

      <el-form-item label="别名">
        <div class="alias-box">
          <el-tag
            v-for="(alias, index) in form.keys"
            :key="alias"
            closable
            size="large"
            class="alias-tag"
            @close="removeAlias(index)"
          >
            {{ alias }}
          </el-tag>
          <span v-if="!form.keys.length" class="ep-small ep-faint"
            >还没有别名</span
          >
        </div>

        <div class="alias-add">
          <el-input
            ref="draftInput"
            v-model="draft"
            placeholder="输入一个别名后回车"
            maxlength="64"
            @keyup.enter="addAlias"
            @blur="commitDraft"
            size="large"
          />
          <el-button @click="addAlias" size="large">添加</el-button>
        </div>
      </el-form-item>

      <el-form-item label="备注给审核者（可选）">
        <el-input v-model="form.note" maxlength="200" size="large" />
      </el-form-item>
    </el-form>

    <template #footer>
      <span
        class="foot-note ep-small"
        :class="changes.length ? 'ep-muted' : 'ep-faint'"
      >
        {{ footHint }}
      </span>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!changes.length && !reverted"
        :loading="busy"
        @click="submit"
      >
        {{ myPendingItem ? "更新我的提交" : "提交审核" }}
      </el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.alias-box {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  width: 100%;
  min-height: 36px;
  padding: 10px 12px;
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius-sm);
  background: var(--ep-surface-sunken);
}

.alias-tag {
  max-width: 100%;
}

.alias-add {
  display: flex;
  gap: 10px;
  width: 100%;
  margin-top: 10px;
}

/* 和图片编辑抽屉同一套排版：chip / 作者+状态 / 原值 → 新值 */
.pending-box {
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius-sm);
  background: var(--ep-surface-sunken);
  padding: 10px 12px;
  margin-bottom: 16px;
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

.foot-note {
  float: left;
  line-height: 32px;
}
</style>
