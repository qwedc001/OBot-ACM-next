<script setup>
import { CloseBold, Select } from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import {
  fieldLabel,
  fromNow,
  renderLikeDelta,
  renderValue,
  statusMeta,
} from "@/utils/format";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 冲突对象：{submission_id, img_key, target, reason, base_value, current_value, submitted_value, conflict_detail?} */
  conflict: { type: Object, default: null },
});

const emit = defineEmits(["update:modelValue", "resolved"]);

const submitting = ref(null); // 'keep' | 'discard' | null

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const openConflict = computed(() => {
  if (!props.conflict) return null;
  // 冲突既可能来自 /admin/conflicts 的扁平结构，
  // 也可能来自提交单自带的 conflict_detail（含更细的 reason）
  return props.conflict;
});

const detail = computed(() => openConflict.value?.conflict_detail ?? null);

const reason = computed(
  () => openConflict.value?.reason ?? detail.value?.reason ?? "原值已被改动",
);

/** 对手：同一个字段上更早过审、现在排在「待下发」里的那条（没有就是被 Bot 改过） */
const rivalId = computed(() => openConflict.value?.rival_id ?? null);

const currentLabel = computed(() =>
  rivalId.value
    ? `另一条待下发的修改（#${rivalId.value} · ${openConflict.value?.rival_author_name || "另一位用户"}）`
    : "磁盘当前值（可能已被 Bot 改动）",
);

/** 展开成逐字段的三方对比：提交时原值 / 磁盘现值 / 提交的新值 */
const rows = computed(() => {
  const conflict = openConflict.value;
  if (!conflict) return [];

  const submitted = conflict.submitted_value;
  const current = conflict.current_value;
  const base = conflict.base_value;

  // 类别提交的值形如 {id, keys}，展开成两行更直观
  if (submitted && typeof submitted === "object" && !Array.isArray(submitted)) {
    return ["id", "keys"].map((field) => ({
      field,
      base: base?.[field],
      current: current?.[field],
      submitted: submitted[field],
    }));
  }

  const field =
    conflict.field ??
    (conflict.type === "comments"
      ? "comments"
      : conflict.type === "likes"
        ? "likes"
        : "ocr_text");

  // 点赞提交的是增量，展示成 +3（老数据里可能还留着绝对值）
  return [
    {
      field,
      base,
      current,
      submitted: field === "likes" ? renderLikeDelta(submitted) : submitted,
    },
  ];
});

function isChanged(row) {
  return renderValue(row.current) !== renderValue(row.submitted);
}

async function resolve(keepNew) {
  if (!openConflict.value) return;
  submitting.value = keepNew ? "keep" : "discard";
  try {
    const result = await api.pickone.resolveConflict(
      openConflict.value.submission_id,
      keepNew,
    );
    ElMessage.success(resolveMessage(result, keepNew));
    visible.value = false;
    emit("resolved", result);
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    submitting.value = null;
  }
}

/**
 * 裁定结果的提示文案。
 *
 * 「保留新值」不再立刻写盘，而是一次**交换**：这条排回「待下发」，被它取代的那条
 * （还在待下发的对手）改成「已驳回」，真正写盘交给下一次一键下发。
 */
function resolveMessage(result, keepNew) {
  if (!keepNew) return "已丢弃该提交，磁盘保持当前值";
  const superseded = (result.superseded ?? []).map(
    (item) => `#${item.submission_id}`,
  );
  const tail = superseded.length ? `，${superseded.join("、")} 已驳回` : "";
  return `已裁定：本条进入「待下发」${tail}。点「一键下发」写入 OBot-ACM。`;
}
</script>

<template>
  <el-drawer v-model="visible" title="冲突处理" size="min(720px, 92vw)">
    <template v-if="openConflict">
      <el-alert
        type="error"
        :closable="false"
        show-icon
        class="mb-14"
        :title="reason"
      >
      </el-alert>
      <el-descriptions :column="2" size="small" border class="mb-14">
        <el-descriptions-item label="提交 ID">
          #{{ openConflict.submission_id }}
        </el-descriptions-item>
        <el-descriptions-item label="类型">
          {{ openConflict.type_label ?? openConflict.type }}
        </el-descriptions-item>
        <el-descriptions-item label="类别 / 目标">
          <span class="mono text-small break-all">
            {{ openConflict.img_key }}
            <template v-if="openConflict.target"
              >/ {{ openConflict.target }}</template
            >
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="提交者">
          {{ openConflict.author_name || "—" }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="openConflict.note"
          label="提交备注"
          :span="2"
        >
          {{ openConflict.note }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="openConflict.conflict_at"
          label="冲突时间"
          :span="2"
        >
          {{ fromNow(openConflict.conflict_at) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-table :data="rows" size="small" border>
        <el-table-column label="字段" width="90">
          <template #default="{ row }">{{ fieldLabel(row.field) }}</template>
        </el-table-column>

        <el-table-column
          label="提交时看到的原值"
          min-width="160"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="diff-value is-old">{{ renderValue(row.base) }}</span>
          </template>
        </el-table-column>

        <el-table-column
          :label="currentLabel"
          min-width="180"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="diff-value" :class="{ 'is-current': isChanged(row) }">
              {{ renderValue(row.current) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column
          label="提交的新值"
          min-width="160"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="diff-value is-new">{{
              renderValue(row.submitted)
            }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="choice-row">
        <el-card
          shadow="never"
          class="choice-card"
          :class="{ 'is-primary': true }"
          @click="resolve(true)"
        >
          <div class="choice-title">
            <el-icon><Select /></el-icon> 保留提交的新值
          </div>
          <div class="choice-desc text-small text-muted">
            本条提交进入待下发流程，另一条提交将被驳回。
            仅在你确认提交内容比其他人的改动更准确时选择。
          </div>
          <el-button
            type="primary"
            class="choice-btn"
            :loading="submitting === 'keep'"
            @click.stop="resolve(true)"
          >
            采用这条修改
          </el-button>
        </el-card>

        <el-card shadow="never" class="choice-card" @click="resolve(false)">
          <div class="choice-title">
            <el-icon><CloseBold /></el-icon> 丢弃这条提交
          </div>
          <div class="choice-desc text-small text-muted">
            本条提交将被驳回， 提交者可以在浏览页看到最新值后重新提交。
          </div>
          <el-button
            class="choice-btn"
            :loading="submitting === 'discard'"
            @click.stop="resolve(false)"
          >
            保留磁盘现值
          </el-button>
        </el-card>
      </div>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mt-14"
        title="也可以什么都不做：冲突单会一直留在「冲突处理」里，不会自己写入。"
      />
    </template>

    <template #footer>
      <el-button @click="visible = false">稍后处理</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.mb-14 {
  margin-bottom: 14px;
}

.mt-14 {
  margin-top: 14px;
}

.diff-value.is-current {
  color: var(--el-color-warning);
  font-weight: 600;
}

.choice-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 14px;
}

.choice-card {
  cursor: pointer;
  border-radius: 10px;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.choice-card:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
}

.choice-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 6px;
}

.choice-desc {
  line-height: 1.6;
  min-height: 58px;
}

.choice-btn {
  width: 100%;
  margin-top: 10px;
}

@media (max-width: 720px) {
  .choice-row {
    grid-template-columns: 1fr;
  }
}
</style>
