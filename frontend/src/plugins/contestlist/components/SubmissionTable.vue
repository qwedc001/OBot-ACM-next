<script setup>
import { Right } from "@element-plus/icons-vue";
/**
 * 提交单列表。
 *
 * 「我的提交」与审核台共用一份，靠 props 切换视角：
 *   用户视角 —— 我改了什么、到哪一步了、能不能撤回；
 *   审核视角 —— 多出类型、备注、审核/裁定入口。
 *
 * 列的顺序与 PickOne 的提交表一致（ID -> 比赛 -> 类型 -> 改动 -> 提交者 -> 状态 -> …），
 * 这样两个工具的「我的提交」页看起来是同一个东西。
 *
 * 改动列不用 show-overflow-tooltip：自定义模板它管不到，交给 .diff-new 自己单行
 * 省略（新值占满剩余宽度，旧值让位），同时挂 title，鼠标悬停能看到完整内容。
 */
import { computed } from "vue";
import { formatTime, fromNow, renderValue, statusMeta } from "@/utils/format";
import { session } from "@/stores/session";
import { contestFieldLabel, formatDuration } from "../format";

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  /** 审核视角：列更多，操作是「通过 / 驳回 / 撤回审核 / 处理冲突」 */
  showReview: { type: Boolean, default: false },
  /** 用户视角：待审核的行给一个「撤回」 */
  showWithdraw: { type: Boolean, default: false },
  /** 显示提交者（管理员「查看全部用户」或审核台） */
  showAuthor: { type: Boolean, default: false },
  /** 支持勾选多条做批量审核（只有审核台会传） */
  selectable: { type: Boolean, default: false },
  /** 当前标签页能不能批量操作：不能时干脆不显示勾选列 */
  batchEnabled: { type: Boolean, default: true },
});

const emit = defineEmits([
  "review",
  "unreview",
  "withdraw",
  "resolve",
  "open",
  "selection-change",
]);

/**
 * 管理信息列（ID、类型、备注）的显示条件。
 *
 * `session.isAdmin` 就够判断该不该给管理信息；`showReview` 是显式开关，
 * 给「非管理员也走到这个表」的场合用。
 *
 * 注意：**审核动作列不用它** —— 那是「审核台」专有的入口，
 * 「我的提交」页即使是管理员也不该出现通过/驳回按钮（见 showActionColumn）。
 */
const verbose = computed(() => session.isAdmin.value || props.showReview);

const STATUS_CHIP = {
  pending: "ep-chip--warn",
  applied: "ep-chip--ok",
  conflict: "ep-chip--danger",
};

/** 改动列里只给可读的短文案；完整时刻挂在 title 上（与 PickOne 的提交表一致） */
function renderField(name, value) {
  if (name === "start_time") return value ? formatTime(value) : "（空）";
  if (name === "duration") return formatDuration(value);
  return renderValue(value);
}

/**
 * 一行提交改了哪些字段。
 *
 * 形状是固定的：**修改一定是单字段**（`submitted_value` 是那个字段的标量，
 * `field` 是字段名），新增是整条对象，删除没有值。所以直接按类型分支，
 * 不去猜 `submitted_value` 长什么样。
 *
 * `before` 为 null 表示「没有原值可对比」（新增类）：模板据此只显示新值，
 * 不画那个 `（空）→ xxx` 的箭头 —— 一条新比赛每一格都是新的，逐个写「（空）」
 * 只是噪音。
 */
function diffFields(row) {
  if (row.type === "contest_delete") return [];

  if (row.type === "contest_create") {
    const after = row.submitted_value ?? {};
    return Object.keys(after).map((name) => ({
      field: name,
      label: contestFieldLabel(name),
      before: null,
      after: renderField(name, after[name]),
    }));
  }

  const name = row.field;
  if (!name) return [];
  return [
    {
      field: name,
      label: contestFieldLabel(name),
      before: renderField(name, (row.base_value ?? {})[name]),
      after: renderField(name, row.submitted_value),
    },
  ];
}

const emptyHint = computed(() =>
  props.showReview ? "没有符合条件的提交" : "你还没有提交过修改",
);

/** 审核列只在「本页真的有事可做」时出现，否则一整列都是「—」 */
const showActionColumn = computed(() =>
  props.items.some((row) =>
    ["pending", "conflict", "approved"].includes(row.status),
  ),
);

/** 只有待审核的行可勾选：已通过/已下发的没有「批量审核」这回事 */
function isRowSelectable(row) {
  return row.status === "pending";
}

/** 本页是否存在可勾选的行 */
const hasSelectableRow = computed(() =>
  props.items.some((row) => isRowSelectable(row)),
);

/**
 * 勾选列的显示条件：既要支持多选、当前场景又能批量操作，而且本页真的有可勾的行。
 * 三者缺一就不显示这一列 —— 一列全灰的勾选框只会让人以为坏了。
 */
const showSelectionColumn = computed(
  () => props.selectable && props.batchEnabled && hasSelectableRow.value,
);
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
      width="46"
      :selectable="isRowSelectable"
    />

    <!-- 提交单自己的自增 ID（所有工具共用一张表），不是比赛的身份哈希 -->
    <el-table-column v-if="verbose" label="ID" width="90">
      <template #default="{ row }">
        <span class="ep-mono ep-small ep-muted">{{ row.id }}</span>
      </template>
    </el-table-column>

    <el-table-column label="比赛" min-width="180">
      <template #default="{ row }">
        <!-- 点它去列表页看这场比赛（带着它的身份哈希去搜），和 PickOne 点类别标识一样 -->
        <a
          class="target ep-break"
          :title="row.target"
          @click="emit('open', row)"
        >
          {{ row.target }}
        </a>
      </template>
    </el-table-column>

    <!-- 删除类提交没有字段对比，说明它删的是哪一场即可 -->
    <el-table-column label="改动" min-width="240">
      <template #default="{ row }">
        <span v-if="row.type === 'contest_delete'" class="ep-small ep-faint">
          删除这一条比赛
        </span>
        <div v-for="field in diffFields(row)" :key="field.field" class="diff">
          <span class="ep-small ep-faint diff-label">{{ field.label }}</span>
          <!-- 新增类没有原值可对比（field.before 为 null），只给新值 -->
          <template v-if="field.before !== null">
            <span class="diff-old" :title="field.before">{{
              field.before
            }}</span>
            <el-icon class="diff-arrow"><Right /></el-icon>
          </template>
          <span class="diff-new" :title="field.after">{{ field.after }}</span>
        </div>
      </template>
    </el-table-column>

    <el-table-column
      v-if="showAuthor"
      label="提交者"
      width="110"
      show-overflow-tooltip
    >
      <template #default="{ row }">{{ row.author_name || "—" }}</template>
    </el-table-column>

    <!--
      类型：逐字段拆分提交之后，这里显示**具体字段**（时长、比赛全称…），
      比笼统的「修改比赛」有用 —— 一条提交单只负责一个字段。
      删除/新增这类整条动作没有字段，退回显示动作名。
    -->
    <el-table-column label="类型" width="128">
      <template #default="{ row }">
        <span class="ep-chip type-chip">{{
          row.field_label || row.type_label
        }}</span>
      </template>
    </el-table-column>

    <!-- 状态：chip 只放短文案，完整说明挂在 tooltip 上 -->
    <el-table-column label="状态" width="132">
      <template #default="{ row }">
        <span class="ep-chip" :class="STATUS_CHIP[row.status]">
          {{ statusMeta(row.status).short }}
        </span>
      </template>
    </el-table-column>

    <el-table-column
      v-if="showReview"
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
      v-else-if="showWithdraw"
      label="操作"
      width="112"
      fixed="right"
    >
      <template #default="{ row }">
        <div class="row-actions">
          <!-- 只有待审核的能撤回：已通过但还没下发的要改，走「更新那条提交」 -->
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

    <template #empty>{{ emptyHint }}</template>
  </el-table>
</template>

<style scoped>
/* 比赛列做成链接：点它去列表页搜这场比赛（PickOne 的类别标识也是这么做的） */
.target {
  color: var(--el-color-primary);
  cursor: pointer;
}

.target:hover {
  text-decoration: underline;
}

.type-chip {
  flex: none;
}

/*
 * 行内操作按钮：统一成有边框、有底色、悬停有过渡的小按钮。
 * 纯 text 样式看起来就是几个蓝字，点没点上去完全没感觉。
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

.diff {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  line-height: 1.7;
}

.diff-label {
  flex: none;
  min-width: 56px;
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
