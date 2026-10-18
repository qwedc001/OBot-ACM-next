<script setup>
import { Refresh, Search } from "@element-plus/icons-vue";
/**
 * 算法竞赛列表的主页面：看列表 + 提交新增/修改（删除只开给管理员）。
 *
 * 三条约定：
 *   - 接口调用统一走 `api.contestlist.*`（见 ../api.js）
 *   - 页面外层用 `ep-page` / `ep-page-head` / `ep-title` / `ep-stat` 这些全站样式类
 *     （定义在 web/src/styles/main.css）
 *   - 提交只是「排队」：真正的写盘要等管理员在审核台点「一键下发」
 *
 * 条目身份是后端算出来的身份哈希（`平台 + 开始时间 + 名称`），不是下标，
 * 所以这里排不排序都不会让提交落到别的比赛上。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Plus } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import { session } from "@/stores/session";
import ContestTable from "../components/ContestTable.vue";
import ContestFormDrawer from "../components/ContestFormDrawer.vue";
import { contestBadge, contestFieldLabel } from "../format";
import { formatTime, statusMeta } from "@/utils/format";

const route = useRoute();
const router = useRouter();

const loading = ref(true);
const items = ref([]);
const drafts = ref([]);
const counts = ref({});
const mine = ref({});
const fileAvailable = ref(true);
const invalidCount = ref(0);
const duplicateCount = ref(0);

// 「我的提交」页点比赛名会带 `?q=<简称>` 过来，落地就是搜这场比赛
const keyword = ref(String(route.query.q ?? ""));
const platformFilter = ref("");
const phaseFilter = ref("");

const dialogOpen = ref(false);
const dialogMode = ref("create");
const editing = ref(null);
/** 正在改的那条待新增草稿的提交单 ID（新增抽屉改草稿时才非空） */
const editingDraftId = ref(null);

/**
 * 平台是自由文本（上游没有受控词表），所以筛选下拉与输入建议都从数据里现推：
 * 列表里出现过的写法按场次从多到少排。这样既不会漏掉新平台，
 * 也不会出现「下拉里有 ICPC，但列表里一条都没有」这种空选项。
 */
const platformOptions = computed(() => {
  const counter = new Map();
  for (const item of items.value) {
    const name = String(item.platform ?? "").trim();
    if (name) counter.set(name, (counter.get(name) ?? 0) + 1);
  }
  return [...counter.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name]) => name);
});

const visible = computed(() => {
  const needle = keyword.value.trim().toLowerCase();
  return items.value.filter((item) => {
    if (platformFilter.value && item.platform !== platformFilter.value)
      return false;
    if (phaseFilter.value && item.phase !== phaseFilter.value) return false;
    if (!needle) return true;
    // 身份哈希按前缀匹配：ID 列显示的就是前 6 位，照着它搜要能搜到。
    // 界面里粘进来的是「提交时那条」的哈希，改过平台/时间/名称之后它会变，
    // 所以这里连 badge 一起搜，用户也还能按名字找到。
    if (item.hash.toLowerCase().startsWith(needle)) return true;
    return [item.badge, item.platform, item.abbr, item.name, item.supplement]
      .filter(Boolean)
      .some((text) => String(text).toLowerCase().includes(needle));
  });
});

const visiblePending = computed(
  () => visible.value.filter((item) => item.has_pending_change).length,
);
const runningCount = computed(
  () => items.value.filter((item) => item.phase === "running").length,
);
/** 我自己的提交里「还在流程中」的条数（统计卡只说我的，跟列表范围无关） */
const myOpenCount = computed(
  () =>
    (mine.value.pending ?? 0) +
    (mine.value.approved ?? 0) +
    (mine.value.conflict ?? 0),
);

async function load() {
  loading.value = true;
  try {
    const data = await api.contestlist.listItems();
    items.value = data.items;
    drafts.value = data.drafts;
    counts.value = data.counts ?? {};
    mine.value = data.mine ?? {};
    fileAvailable.value = data.file_available !== false;
    invalidCount.value = data.invalid ?? 0;
    duplicateCount.value = data.duplicates ?? 0;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  dialogMode.value = "create";
  editing.value = null;
  editingDraftId.value = null;
  dialogOpen.value = true;
}

function openEdit(item) {
  dialogMode.value = "update";
  editing.value = item;
  editingDraftId.value = null;
  dialogOpen.value = true;
}

/**
 * 改自己「待新增」的草稿。
 *
 * 复用新增抽屉：起点换成草稿那六个字段。提交时走「先撤掉旧草稿，再发一条新的」——
 * 草稿的身份就是内容哈希，改了平台/时间/名称它就会变成另一个身份，
 * 那样原草稿会留在队列里，看起来像录了两场。
 *
 * **只有还没审过的草稿能进这里**（见 `canEditDraft`）：已经过审的那份是管理员看过的
 * 内容，而且撤回接口只收「待审核」的提交，改完旧的那条撤不掉，会在下发时变成两条
 * 重复的比赛。
 */
function openDraftEdit(draft) {
  dialogMode.value = "create";
  editing.value = { ...(draft.value ?? {}) };
  editingDraftId.value = draft.submission_id;
  dialogOpen.value = true;
}

/** 自己的、且还没过审的草稿才改得动（后端也只允许撤回待审核的提交） */
function canEditDraft(draft) {
  return Boolean(draft.mine) && draft.status === "pending";
}

/** 改不动时右边显示的状态：待审核 / 待下发，别人提的就直接不显示按钮 */
function draftStatus(draft) {
  return statusMeta(draft.status).short;
}

function resetQuery() {
  keyword.value = "";
  platformFilter.value = "";
  phaseFilter.value = "";
  // 清掉「我的提交」带过来的 `?q=`，否则刷新又会把关键词填回来
  if (route.query.q) {
    router.replace({ name: "contestlist-home" });
  }
}

/** 从「我的提交」再次点进来时（路由不变、只换 query）也要把搜索词换过去 */
watch(
  () => route.query.q,
  (value) => {
    if (value !== undefined) keyword.value = String(value);
  },
);

async function submitForm(payload) {
  // 只撤回（没有新改动）时不调提交接口
  if (!payload) {
    await load();
    return;
  }
  try {
    const draftId = editingDraftId.value;
    const result =
      dialogMode.value === "create"
        ? await api.contestlist.submitCreate(payload)
        : await api.contestlist.submitUpdate({
            hash: editing.value.hash,
            ...payload,
          });

    // 改草稿：内容变了之后身份哈希也会变，旧草稿就留在队列里了（看起来像录了两场），
    // 所以把它撤掉。但**只有它真的没被复用**时才撤 —— 内容没变时 submitCreate 覆盖的
    // 就是同一条，这时撤掉等于把刚提交的东西删了。
    if (
      draftId &&
      !(result.submissions ?? []).some((row) => row.id === draftId)
    ) {
      try {
        await api.contestlist.withdraw(draftId);
      } catch (error) {
        // 400 = 这条草稿在提交过程中被过审了，撤不掉了。得说一声：旧的那条留在
        // 「待下发」里，不管它就会和刚提交的这条一起下发成两场重复的比赛。
        if (error?.status === 400) {
          ElMessage.warning(
            "原来那条草稿已经过审，撤不掉了，请让管理员先处理它",
          );
        }
      }
    }
    editingDraftId.value = null;

    if (result.reason) {
      // 「新增」的内容和已有比赛是同一场时后端会改发「修改」，得说清发生了什么
      ElMessage.warning(result.reason);
    } else {
      // 改了几个字段就是几条提交单，说清条数，管理员那边才是一条条审
      const count = result.submissions?.length ?? 1;
      const labels = (result.fields ?? [])
        .map((name) => contestFieldLabel(name))
        .join("、");
      ElMessage.success("已提交，等待管理员审核");
    }
    await load();
  } catch (error) {
    ElMessage.error(error.message);
  }
}

/**
 * 删除：只开给管理员（后端 `/admin/delete` 会再挡一次）。
 * 和其它改动一样先排队，审核台「一键下发」之后才真的从文件里移除。
 */
async function remove(item) {
  let note = "";
  try {
    const result = await ElMessageBox.prompt(
      `删除后这条比赛会从列表里移除：${formatTime(item.start_time)}\n确定删除「${item.badge}」吗？`,
      "提交删除",
      {
        type: "warning",
        confirmButtonText: "提交删除",
        inputPlaceholder: "删除理由（可选，给审核者看）",
        inputValidator: (value) => (value ?? "").length <= 200 || "最多 200 字",
      },
    );
    note = result.value ?? "";
  } catch {
    return;
  }

  try {
    await api.contestlist.submitDelete({ hash: item.hash, note });
    ElMessage.success("已提交删除，等待下发");
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
        <h1 class="ep-title">算法竞赛列表</h1>
        <p class="ep-subtitle">手动录入的算法竞赛列表，可进行添加与修改</p>
      </div>
      <div class="ep-actions">
        <el-button :icon="Refresh" size="large" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" size="large" @click="openCreate"
          >新增比赛</el-button
        >
      </div>
    </div>

    <el-alert
      v-if="!fileAvailable"
      type="warning"
      :closable="false"
      show-icon
      class="ep-mb"
      title="比赛列表文件还不存在"
      description="可以先提交比赛，经管理员审核并应用后建立列表。"
    />

    <el-alert
      v-if="invalidCount || duplicateCount"
      type="warning"
      :closable="false"
      show-icon
      class="ep-mb"
      :title="`数据文件里有 ${invalidCount} 条格式不合规、${duplicateCount} 组重复的比赛`"
    >
      这些条目仍然可以照常改；表格里标了出来。
    </el-alert>

    <div class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">比赛</div>
        <div class="ep-stat-value">{{ items.length }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">进行中</div>
        <div class="ep-stat-value" :class="{ 'is-ok': runningCount > 0 }">
          {{ runningCount }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">审核中</div>
        <div class="ep-stat-value" :class="{ 'is-warn': visiblePending > 0 }">
          {{ visiblePending }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">流程中</div>
        <div class="ep-stat-value">{{ myOpenCount }}</div>
      </div>
    </div>

    <!-- 同一行的控件统一 size，保证高度齐平 -->
    <div class="toolbar ep-mb">
      <el-input
        v-model="keyword"
        size="large"
        placeholder="搜索 ID、平台、简称或名称"
        clearable
        style="width: 260px"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>

      <el-select
        v-model="platformFilter"
        size="large"
        placeholder="平台"
        clearable
        filterable
        style="width: 150px"
      >
        <el-option
          v-for="name in platformOptions"
          :key="name"
          :label="name"
          :value="name"
        />
      </el-select>

      <el-select
        v-model="phaseFilter"
        size="large"
        placeholder="状态"
        clearable
        style="width: 140px"
      >
        <el-option label="未开始" value="upcoming" />
        <el-option label="进行中" value="running" />
        <el-option label="已结束" value="ended" />
      </el-select>

      <el-button
        v-if="keyword || platformFilter || phaseFilter"
        text
        size="large"
        @click="resetQuery"
      >
        重置
      </el-button>

      <span class="ep-small ep-faint result-count"
        >共 {{ visible.length }} 场</span
      >
    </div>

    <div class="ep-card ep-card--flush">
      <el-empty
        v-if="!loading && !visible.length"
        description="没有匹配的比赛"
      />

      <ContestTable
        v-else
        :items="visible"
        :loading="loading"
        :can-delete="session.isAdmin.value"
        @edit="openEdit"
        @remove="remove"
      />
    </div>

    <div v-if="drafts.length" class="ep-section-label draft-label">
      待新增（{{ drafts.length }} 条，还在审核队列里，尚未写入文件）
    </div>
    <div v-if="drafts.length" class="ep-card">
      <div v-for="draft in drafts" :key="draft.submission_id" class="draft-row">
        <span
          class="ep-chip"
          :class="draft.mine ? 'ep-chip--ok' : 'ep-chip--accent'"
        >
          {{ draft.mine ? "我的" : draft.author_name }}
        </span>
        <span class="ep-small ep-break">
          {{ contestBadge(draft.value) }} {{ draft.value?.name }}
        </span>
        <!--
          自己的、还没过审的草稿能接着改；别人的只能看（后端也只允许撤回自己的提交）；
          已经过审的不能再改，右边只显示它当前的状态。
        -->
        <el-button
          v-if="canEditDraft(draft)"
          size="small"
          text
          class="draft-action"
          @click="openDraftEdit(draft)"
        >
          修改
        </el-button>
        <span v-else class="draft-action ep-small ep-faint">{{
          draftStatus(draft)
        }}</span>
      </div>
    </div>

    <ContestFormDrawer
      v-model="dialogOpen"
      :mode="dialogMode"
      :item="editing"
      :platform-options="platformOptions"
      @submitted="submitForm"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.result-count {
  margin-left: auto;
}

.draft-label {
  margin-top: 34px;
}

.draft-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 0;
  flex-wrap: wrap;
}

.draft-action {
  margin-left: auto;
}

@media (max-width: 720px) {
  .result-count {
    margin-left: 0;
  }
}
</style>
