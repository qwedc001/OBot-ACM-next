<script setup>
/**
 * 比赛列表表格。
 *
 * 「比赛列表」（浏览）与审核台共用一份：管理员在审核台上看到的，应该和浏览页
 * 是同一张表，否则改了列之后两处的说法就会不一致。
 *
 * 表格顺序就是文件里的顺序，**不按时间重排** —— 条目身份是身份哈希（见后端
 * hashing.py），排序不会让提交落错地方，但让人对着文件找某一场时更容易。
 *
 * 列的顺序与 PickOne 的类别表对齐：ID（= 身份哈希前 6 位）-> 比赛 -> 名称 -> …
 */
import { computed } from "vue";
import { formatTime, fromNow } from "@/utils/format";
import { formatDuration, phaseChip, shortId } from "../format";

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  /** 显示「审核中」标记 */
  showPending: { type: Boolean, default: true },
  /**
   * 当前用户能不能改这条列表。
   * 删除只有管理员能发起（后端 `/admin/delete` 会挡），普通用户只看得到「修改」。
   */
  canDelete: { type: Boolean, default: false },
});

defineEmits(["edit", "remove"]);

/** 只有本页真的有事可做时才给操作列 —— 一整列都是「—」不如不要 */
const hasActions = computed(() => props.items.length > 0);
const actionsWidth = computed(() => (props.canDelete ? 168 : 112));
</script>

<template>
  <el-table
    :data="items"
    v-loading="loading"
    row-key="hash"
    size="small"
    stripe
    @row-click="(row) => $emit('edit', row)"
  >
    <el-table-column label="ID" width="120">
      <template #default="{ row }">
        <!-- 完整身份哈希挂在 title 上；列里只放前 6 位，够人对着提交单核对 -->
        <el-tooltip :content="row.hash" placement="top" :show-after="150">
          <span class="ep-mono ep-small ep-muted">{{ shortId(row.hash) }}</span>
        </el-tooltip>
      </template>
    </el-table-column>

    <el-table-column label="比赛" min-width="170">
      <template #default="{ row }">
        <!-- `平台 · 简称`，和提交单里指代同一场比赛的写法一致 -->
        <span class="badge ep-break">{{ row.badge }}</span>
      </template>
    </el-table-column>

    <el-table-column label="名称" min-width="280">
      <template #default="{ row }">
        <div class="name-cell">
          <span class="ep-break">{{ row.name }}</span>
          <span v-if="row.supplement" class="ep-small ep-faint ep-break">{{
            row.supplement
          }}</span>
        </div>
      </template>
    </el-table-column>

    <el-table-column label="开始时间" width="150">
      <template #default="{ row }">
        <el-tooltip
          :content="formatTime(row.start_time)"
          placement="top"
          :show-after="150"
          :disabled="!row.start_time"
        >
          <span class="ep-small">{{ fromNow(row.start_time) }}</span>
        </el-tooltip>
      </template>
    </el-table-column>

    <el-table-column label="时长" width="106">
      <template #default="{ row }">
        <span class="ep-small">{{ formatDuration(row.duration) }}</span>
      </template>
    </el-table-column>

    <el-table-column label="状态" width="140">
      <template #default="{ row }">
        <div class="cell-line">
          <span class="ep-chip" :class="phaseChip(row.phase)">{{
            row.phase_label
          }}</span>
          <el-tooltip
            v-if="showPending && row.has_pending_change"
            content="有审核中的改动，尚未写入文件"
            placement="top"
            :show-after="150"
          >
            <span class="ep-chip ep-chip--warn">审核中</span>
          </el-tooltip>
          <el-tooltip
            v-if="!row.valid"
            :content="row.problems.join('；')"
            placement="top"
            :show-after="150"
          >
            <span class="ep-chip ep-chip--danger">格式异常</span>
          </el-tooltip>
        </div>
      </template>
    </el-table-column>

    <el-table-column
      v-if="hasActions"
      label="操作"
      :width="actionsWidth"
      fixed="right"
    >
      <template #default="{ row }">
        <div class="row-actions">
          <el-button size="small" @click.stop="$emit('edit', row)"
            >修改</el-button
          >
          <el-button
            v-if="canDelete"
            size="small"
            type="danger"
            plain
            @click.stop="$emit('remove', row)"
          >
            删除
          </el-button>
        </div>
      </template>
    </el-table-column>

    <template #empty>列表还是空的</template>
  </el-table>
</template>

<style scoped>
/*
 * 行内操作按钮：和 PickOne 的提交表一样，统一成有边框、悬停有反馈的小按钮
 * （纯 text 样式看起来就是几个蓝字，点没点上去完全没感觉）。
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
    box-shadow 0.18s ease;
}

.row-actions :deep(.el-button:hover) {
  box-shadow: 0 3px 10px rgba(23, 26, 38, 0.12);
}

.cell-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

/* `平台 · 简称`：和提交单里指代同一场比赛的写法一致，但不做成胶囊 ——
   一列全是彩色胶囊会把注意力从状态列抢走 */
.badge {
  font-weight: 600;
}

/* 名称与补充信息各占一行：挨着排会读成一句话（「…站山东大学」） */
.name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
