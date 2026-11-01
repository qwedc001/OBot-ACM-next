<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import { formatTime } from "@/utils/format";

const loading = ref(true);
const logs = ref([]);
const total = ref(0);
const query = reactive({ page: 1, page_size: 50 });

const ACTION_LABELS = {
  submit_image_change: "提交图片修改",
  submit_category: "提交类别修改",
  withdraw_submission: "撤回提交",
  review_revoked: "撤回审核",
  review_approved: "审核通过",
  review_rejected: "审核驳回",
  review_batch: "批量审核",
  apply_changes: "一键应用",
  resolve_conflict: "处理冲突",
  submit_contest: "提交比赛修改",
  apply_contest_changes: "应用比赛修改",
  resolve_contest_conflict: "处理比赛冲突",
  approve_upstream_sticker: "通过待审图片",
  accept_duplicate_upstream_sticker: "确认重复图片",
  reject_upstream_sticker: "驳回待审图片",
};

function actionLabel(action) {
  return ACTION_LABELS[action] ?? action;
}

async function load() {
  loading.value = true;
  try {
    const data = await api.curation.logs({ ...query });
    logs.value = data.items;
    total.value = data.total;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">操作日志</h1>
        <p class="ep-subtitle">共 {{ total }} 条记录</p>
      </div>
    </div>

    <div class="ep-card ep-card--flush">
      <el-table :data="logs" v-loading="loading" size="small" row-key="id">
        <el-table-column label="时间" width="200">
          <template #default="{ row }">
            <span class="ep-small">{{ formatTime(row.created_at, true) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作者" width="200">
          <template #default="{ row }">
            <span class="ep-mono ep-small">{{ row.username || "—" }}</span>
            <span
              v-if="row.is_admin"
              class="ep-chip ep-chip--danger operator-chip"
              >管理员</span
            >
          </template>
        </el-table-column>

        <el-table-column label="动作" width="200">
          <template #default="{ row }">
            <span class="ep-chip">{{ actionLabel(row.action) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="详情" min-width="340" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="ep-small ep-break">{{ row.detail || "—" }}</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="total > query.page_size"
        v-model:current-page="query.page"
        :page-size="query.page_size"
        :total="total"
        layout="prev, pager, next, total"
        class="pager"
        @current-change="load"
      />
    </div>
  </div>
</template>

<style scoped>
.operator-chip {
  margin-left: 6px;
}

.pager {
  margin: 16px;
  justify-content: flex-end;
}
</style>
