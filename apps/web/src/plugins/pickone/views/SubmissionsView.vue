<script setup>
import { Refresh } from "@element-plus/icons-vue";
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import { session } from "@/stores/session";
import SubmissionTable from "@/plugins/pickone/components/SubmissionTable.vue";

const emit = defineEmits(["refresh-summary"]);

const loading = ref(true);
const items = ref([]);
const counts = ref({});
const total = ref(0);

const query = reactive({
  status: "open",
  img_key: "",
  scope: "mine",
  page: 1,
  page_size: 20,
});

const statusOptions = [
  { value: "open", label: "全部未生效" },
  { value: "pending", label: "等待审核" },
  { value: "approved", label: "等待下发" },
  { value: "applied", label: "已生效" },
  { value: "rejected", label: "未通过" },
  { value: "all", label: "全部" },
];

const emptyHint = computed(() =>
  query.scope === "all" ? "没有符合条件的提交" : "你还没有提交过修改",
);

async function load() {
  loading.value = true;
  try {
    const data = await api.pickone.submissions({ ...query });
    items.value = data.items;
    // 统计卡永远显示「我自己的」数量，跟列表范围无关
    counts.value = data.mine ?? data.counts ?? {};
    total.value = data.total;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    loading.value = false;
  }
}

function applyQuery() {
  query.page = 1;
  load();
}

function changeScope() {
  // 切回「只看自己」时兜底，避免普通用户落到 all
  if (query.scope === "all" && !session.isAdmin.value) {
    query.scope = "mine";
  }
  applyQuery();
}

async function withdraw(row) {
  try {
    await ElMessageBox.confirm(
      "撤回后这条修改会从审核队列移除，确定吗？",
      "撤回提交",
      {
        type: "warning",
      },
    );
  } catch {
    return;
  }

  try {
    await api.pickone.withdraw(row.id);
    ElMessage.success("已撤回");
    await load();
    emit("refresh-summary");
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
        <h1 class="ep-title">我的提交</h1>
        <p class="ep-subtitle">审核通过后由管理员下发到数据文件</p>
      </div>
      <div class="ep-actions">
        <el-button :icon="Refresh" size="large" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">等待审核</div>
        <div class="ep-stat-value" :class="{ 'is-warn': counts.pending > 0 }">
          {{ counts.pending ?? 0 }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">等待下发</div>
        <div class="ep-stat-value">{{ counts.approved ?? 0 }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">已生效</div>
        <div class="ep-stat-value" :class="{ 'is-ok': counts.applied > 0 }">
          {{ counts.applied ?? 0 }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">未通过</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-danger': counts.rejected > 0 }"
        >
          {{ counts.rejected ?? 0 }}
        </div>
      </div>
    </div>

    <!-- 同一行的控件统一 size，保证高度齐平 -->
    <div class="toolbar ep-mb">
      <el-select
        v-model="query.status"
        size="large"
        style="width: 170px"
        @change="applyQuery"
      >
        <el-option
          v-for="option in statusOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>

      <el-input
        v-model="query.img_key"
        size="large"
        placeholder="类别标识"
        clearable
        style="width: 180px"
        @keyup.enter="applyQuery"
        @clear="applyQuery"
      />

      <el-checkbox
        v-if="session.isAdmin.value"
        v-model="query.scope"
        true-value="all"
        false-value="mine"
        border
        size="large"
        @change="changeScope"
      >
        查看全部用户
      </el-checkbox>

      <span class="ep-small ep-faint">共 {{ total }} 条</span>
    </div>

    <div class="ep-card ep-card--flush">
      <el-empty v-if="!loading && !items.length" :description="emptyHint" />
      <SubmissionTable
        v-else
        :items="items"
        :loading="loading"
        show-author
        @withdraw="withdraw"
      />

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
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.pager {
  margin: 16px;
  justify-content: flex-end;
}
</style>
