<script setup>
import { Right } from "@element-plus/icons-vue";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { session } from "@/stores/session";
import {
  fieldLabel,
  fromNow,
  renderLikeDelta,
  renderValue,
  statusMeta,
} from "@/utils/format";

/**
 * 提交单列表。
 *
 * 管理员看到完整的改动对比与审核入口；普通用户只看「我改了什么、现在到哪一步了」，
 * 不展示内部状态与冲突细节（那些是管理员的事）。
 */
const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectable: { type: Boolean, default: false },
  /**
   * 当前场景能不能批量操作。不能批量时干脆不显示勾选列 ——
   * 否则勾上了也没有后续动作，反而像坏了。
   */
  batchEnabled: { type: Boolean, default: true },
  showAuthor: { type: Boolean, default: false },
  showReview: { type: Boolean, default: false },
});

const emit = defineEmits([
  "selection-change",
  "review",
  "unreview",
  "withdraw",
  "resolve",
]);

const router = useRouter();

/** 管理员视角：列多一些；用户视角：简化 */
const verbose = computed(() => session.isAdmin.value || props.showReview);

function targetLabel(row) {
  return row.target || "（类别信息）";
}

function diffFields(row) {
  if (row.type === "category" || row.type === "category_create") {
    const before = row.base_value ?? {};
    const after = row.submitted_value ?? {};
    return [
      {
        field: "id",
        before: before.id ?? "（无）",
        after: after.id ?? "（无）",
      },
      { field: "keys", before: before.keys ?? [], after: after.keys ?? [] },
    ];
  }

  // 点赞提交的是增量：原值是磁盘上的总数，新值显示成 +3
  if (row.type === "likes") {
    return [
      {
        field: "likes",
        before: row.base_value,
        after: renderLikeDelta(row.submitted_value),
      },
    ];
  }

  const field = row.type === "comments" ? "comments" : "ocr_text";
  return [{ field, before: row.base_value, after: row.submitted_value }];
}

function isChanged(field) {
  return renderValue(field.before) !== renderValue(field.after);
}

function openTarget(row) {
  const query = row.target ? { q: row.target.replace(/\.gif$/, "") } : {};
  router.push({
    name: "pickone-category",
    params: { imgKey: row.img_key },
    query,
  });
}

/** 只有待审核的行可勾选 */
function isRowSelectable(row) {
  return row.status === "pending";
}

/** 本页是否存在可勾选的行 */
const hasSelectableRow = computed(() =>
  props.items.some((row) => isRowSelectable(row)),
);

/**
 * 勾选列的显示条件：
 * 既要支持多选、当前场景又能批量操作，而且本页真的有可勾的行。
 * 三者缺一就不显示这一列 —— 一列全灰的勾选框只会让人以为坏了。
 */
const showSelectionColumn = computed(
  () => props.selectable && props.batchEnabled && hasSelectableRow.value,
);

/**
 * 审核列只在「本页真的有事可做」时才出现。
 * 已下发之外的每一行都是「—」，占一列没意义；
 * 待审核要留（通过 / 驳回）、冲突要留（处理冲突）、已驳回要留（改为通过）、
 * 待下发也要留（撤回审核）。
 */
const showActionColumn = computed(() =>
  props.items.some((row) =>
    ["pending", "conflict", "approved", "rejected"].includes(row.status),
  ),
);

/** 操作列同理：只有待审核的行才会有「撤回」 */
const hasWithdrawableRow = computed(() => hasSelectableRow.value);
</script>

<template>
  <el-table
    :data="items"
    v-loading="loading"
    row-key="id"
    size="small"
    @selection-change="(rows) => emit('selection-change', rows)"
  >
    <el-table-column
      v-if="showSelectionColumn"
      type="selection"
      width="56"
      :selectable="isRowSelectable"
    />

    <el-table-column v-if="verbose" label="ID" prop="id" width="90" />

    <el-table-column label="类别 / 目标" min-width="252" show-overflow-tooltip>
      <template #default="{ row }">
        <a class="target ep-mono" @click="openTarget(row)">{{ row.img_key }}</a>
        <div
          v-if="row.target"
          class="ep-small ep-faint target-name"
          :title="row.target"
        >
          {{ row.target }}
        </div>
      </template>
    </el-table-column>

    <!--
      改动列不用 show-overflow-tooltip：自定义模板它管不到，
      交给 .diff-new 自己单行省略（新值占满剩余宽度，旧值让位），
      同时挂 title，鼠标悬停能看到完整内容。
    -->
    <el-table-column label="改动" min-width="240">
      <template #default="{ row }">
        <div v-for="field in diffFields(row)" :key="field.field" class="diff">
          <span class="ep-small ep-faint diff-label">{{
            fieldLabel(field.field)
          }}</span>
          <template v-if="verbose && isChanged(field)">
            <span class="diff-old" :title="renderValue(field.before)">
              {{ renderValue(field.before) }}
            </span>
            <el-icon class="diff-arrow"><Right /></el-icon>
          </template>
          <span class="diff-new" :title="renderValue(field.after)">
            {{ renderValue(field.after) }}
          </span>
        </div>
      </template>
    </el-table-column>

    <el-table-column
      v-if="showAuthor"
      label="提交者"
      width="110"
      show-overflow-tooltip
    >
      <template #default="{ row }">{{ row.author_name }}</template>
    </el-table-column>

    <!-- 类型：单独一列一个 chip -->
    <el-table-column v-if="verbose" label="类型" width="128">
      <template #default="{ row }">
        <span class="ep-chip type-chip">{{ row.type_label }}</span>
      </template>
    </el-table-column>

    <!-- 状态：chip 只放短文案，完整说明挂在 tooltip 上 -->
    <el-table-column label="状态" width="132">
      <template #default="{ row }">
        <span
          class="ep-chip"
          :class="{
            'ep-chip--warn': row.status === 'pending',
            'ep-chip--ok': row.status === 'applied',
            'ep-chip--danger': row.status === 'conflict',
          }"
        >
          {{ statusMeta(row.status).short }}
        </span>
      </template>
    </el-table-column>

    <el-table-column
      v-if="verbose"
      label="备注"
      min-width="110"
      show-overflow-tooltip
    >
      <template #default="{ row }">
        <span class="ep-small">{{ row.note || "—" }}</span>
      </template>
    </el-table-column>

    <el-table-column label="提交时间" width="130">
      <template #default="{ row }">
        <span class="ep-small ep-faint">{{ fromNow(row.created_at) }}</span>
      </template>
    </el-table-column>

    <el-table-column
      v-if="showReview && showActionColumn"
      label="审核"
      width="168"
      fixed="right"
    >
      <template #default="{ row }">
        <div class="row-actions">
          <template v-if="row.status === 'pending'">
            <el-button
              size="small"
              type="success"
              @click="emit('review', row, true)"
            >
              通过
            </el-button>
            <el-button size="small" plain @click="emit('review', row, false)"
              >驳回</el-button
            >
          </template>
          <el-button
            v-else-if="row.status === 'conflict'"
            size="small"
            type="danger"
            @click="emit('resolve', row)"
          >
            处理冲突
          </el-button>
          <el-button
            v-else-if="row.status === 'rejected'"
            size="small"
            type="primary"
            plain
            @click="emit('review', row, true)"
          >
            改为通过
          </el-button>
          <el-button
            v-else-if="row.status === 'approved'"
            size="small"
            plain
            @click="emit('unreview', row)"
          >
            撤回审核
          </el-button>
          <span v-else class="ep-small ep-faint">—</span>
        </div>
      </template>
    </el-table-column>

    <el-table-column
      v-else-if="!showReview && hasWithdrawableRow"
      label="操作"
      width="112"
      fixed="right"
    >
      <template #default="{ row }">
        <div class="row-actions">
          <el-button
            v-if="row.status === 'pending'"
            size="small"
            type="danger"
            plain
            @click="emit('withdraw', row)"
          >
            撤回
          </el-button>
          <span v-else class="ep-small ep-faint">—</span>
        </div>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.target {
  color: var(--el-color-primary);
  cursor: pointer;
  font-size: 12.5px;
}

.target:hover {
  text-decoration: underline;
}

/* 类型 chip：不压缩、不换行，宽度由列给定 */
.type-chip {
  flex: none;
}

/*
 * 行内操作按钮。
 *
 * 之前这些按钮是 text 样式（无边框、无底、无悬停反馈），看起来就是几个
 * 蓝字，点没点上去完全没感觉。这里统一成有边框、有底色、
 * 悬停/按下有过渡的小按钮。
 */
.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.row-actions .el-button + .el-button {
  margin-left: 0;
}

.row-actions :deep(.el-button) {
  transition:
    background-color 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.12s ease;
}

.row-actions :deep(.el-button:hover) {
  box-shadow: 0 3px 10px rgba(23, 26, 38, 0.12);
}

.row-actions :deep(.el-button:active) {
  transform: translateY(0);
  box-shadow: none;
}

.target-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.diff {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  line-height: 1.7;
}

.diff-label {
  flex: none;
  min-width: 52px;
}

/* 旧值是次要信息，允许多让一点位置给新值 */
.diff-old {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--ep-ink-faint);
  text-decoration: line-through;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.diff-new {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--ep-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.diff-arrow {
  flex: none;
  font-size: 11px;
  color: var(--ep-ink-faint);
}

.conflict-note {
  color: var(--ep-danger);
  line-height: 1.5;
  margin-top: 3px;
}
</style>
