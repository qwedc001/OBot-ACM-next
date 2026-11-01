<script setup>
import { Refresh, Search } from "@element-plus/icons-vue";
/**
 * 「我的提交」：我发出去的改动都在这里。
 *
 * 结构照 PickOne 的同一页：状态筛选 + 标识搜索 + 「查看全部用户」（只对管理员）
 * + 统计卡 + 提交表。三条容易做错的地方特别注意：
 *
 *   1. 默认 `scope=mine`。管理员也先看自己的，勾上「查看全部用户」才看全部。
 *   2. **统计卡口径跟着当前范围走**：看自己时用 `mine`，看全部时用 `counts`。
 *      两边的分母不一样（一个是我的、一个是所有人的），混着用就会出现
 *      「已生效」比「比赛总数」还大这种读不懂的数字。
 *   3. 搜索走 `q`（匹配比赛），由后端做 —— 只在前端过滤的话，翻到第 2 页会漏。
 */
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import { session } from "@/stores/session";
import SubmissionTable from "../components/SubmissionTable.vue";

const router = useRouter();

const loading = ref(true);
const items = ref([]);
const counts = ref({});
const mine = ref({});
const total = ref(0);

const query = reactive({
  status: "open",
  q: "",
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

/** 统计卡的数字：看自己就用自己的，看全部用户就用全站的 */
const shown = computed(() =>
  query.scope === "all" ? counts.value : mine.value,
);

const emptyHint = computed(() =>
  query.scope === "all" ? "没有符合条件的提交" : "你还没有提交过修改",
);

async function load() {
  loading.value = true;
  try {
    const data = await api.contestlist.listSubmissions({ ...query });
    items.value = data.items;
    total.value = data.total;
    counts.value = data.counts ?? {};
    mine.value = data.mine ?? {};
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

/**
 * 点提交表里的比赛名 -> 去列表页，并把搜索框填成**这场比赛的身份哈希**。
 *
 * 用 `row.img_key`（提交时那条的身份）而不是 `submitted_value` 里的字段：
 * 改过平台/开始时间/名称之后哈希会变，按新哈希去列表里搜是搜不到原条目的，
 * 而提交单的 `img_key` 始终是「当时那一场」。
 */
function openContest(row) {
  router.push({ name: "contestlist-home", query: { q: row.img_key } });
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
    await api.contestlist.withdraw(row.id);
    ElMessage.success("已撤回");
    await load();
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
        <p class="ep-subtitle">审核通过后由管理员下发到比赛列表</p>
      </div>
      <div class="ep-actions">
        <el-button :icon="Refresh" size="large" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">等待审核</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-warn': (shown.pending ?? 0) > 0 }"
        >
          {{ shown.pending ?? 0 }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">等待下发</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-ok': (shown.approved ?? 0) > 0 }"
        >
          {{ shown.approved ?? 0 }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">已生效</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-ok': (shown.applied ?? 0) > 0 }"
        >
          {{ shown.applied ?? 0 }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">未通过</div>
        <div
          class="ep-stat-value"
          :class="{ 'is-danger': (shown.rejected ?? 0) > 0 }"
        >
          {{ shown.rejected ?? 0 }}
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
        v-model="query.q"
        size="large"
        placeholder="比赛（平台 / 简称 / 名称）"
        clearable
        style="width: 240px"
        @keyup.enter="applyQuery"
        @clear="applyQuery"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>

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

      <span class="ep-small ep-faint result-count">共 {{ total }} 条</span>
    </div>

    <div class="ep-card ep-card--flush">
      <el-empty v-if="!loading && !items.length" :description="emptyHint" />
      <SubmissionTable
        v-else
        :items="items"
        :loading="loading"
        :show-author="query.scope === 'all'"
        show-withdraw
        @withdraw="withdraw"
        @open="openContest"
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

@media (max-width: 720px) {
  .result-count {
    margin-left: 0;
  }
}
</style>
