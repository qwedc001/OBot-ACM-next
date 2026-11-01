<script setup>
import { CloseBold, Right, Search, Select } from "@element-plus/icons-vue";
/**
 * 比赛列表的审核台。
 *
 * 审核流程本身是框架能力（队列、通过、驳回、撤回都在 `/api/plugins/curation/admin/*`），这里只多了
 * 两件「只有这个工具才看得懂」的事：
 *
 *   1. 改动对比：审核和下发前都要看清「原值 → 新值」，所以审核弹窗和应用预览
 *      都逐字段列出对比（用和浏览页同一个提交表）。
 *   2. 冲突裁定：下发时发现磁盘上同一条被改过（很可能是 Bot 的 /导入比赛），
 *      提交单会挂成 conflict，需要管理员在这里选择保留哪一边。
 */
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Refresh, Upload } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import SubmissionTable from "../components/SubmissionTable.vue";
import { FIELD_ORDER, contestFieldLabel, formatDuration } from "../format";
import { formatTime, renderValue } from "@/utils/format";

const route = useRoute();
const router = useRouter();

const TABS = [
  { name: "pending", label: "待审核" },
  { name: "approved", label: "待下发" },
  { name: "conflict", label: "冲突待裁定" },
  { name: "applied", label: "已下发" },
  { name: "rejected", label: "已驳回" },
  { name: "all", label: "全部" },
];

const loading = ref(true);
const applying = ref(false);
const previewing = ref(false);
const items = ref([]);
const total = ref(0);
const counts = ref({});
const overview = ref(null);
const selection = ref([]);

const query = reactive({ tab: "pending", q: "", page: 1, page_size: 20 });

const preview = ref(null);
const previewOpen = ref(false);

const conflictOpen = ref(false);
const conflictRow = ref(null);
const conflictSubmitting = ref("");
const allowDuplicate = ref(false);

const reviewDialog = reactive({
  open: false,
  row: null,
  approve: true,
  comment: "",
  submitting: false,
});

const pendingCount = computed(() => counts.value.pending ?? 0);
const approvedCount = computed(() => counts.value.approved ?? 0);
const conflictCount = computed(() => counts.value.conflict ?? 0);
const appliedCount = computed(() => counts.value.applied ?? 0);
const rejectedCount = computed(() => counts.value.rejected ?? 0);

const previewConflicts = computed(() => preview.value?.conflicts ?? []);

/** 这些标签页里可以勾选做批量审核 */
const BATCH_TABS = new Set(["pending", "conflict"]);

/** 勾选中的、还是「待审核」的行（冲突/已通过的没有批量通过这回事） */
const batchIds = computed(() =>
  selection.value
    .filter((row) => row.status === "pending")
    .map((row) => row.id),
);

const batchEnabled = computed(() => BATCH_TABS.has(query.tab));

/**
 * 「全部」的条数：各状态之和。
 *
 * `counts` 是本工具各状态的条数，跟当前标签页无关，加起来就是总数。
 * **不能用队列返回的 `total`** —— 那是当前标签页的条数，切个标签页「全部」就跟着变。
 */
const allCount = computed(() =>
  Object.values(counts.value).reduce(
    (sum, value) => sum + (Number(value) || 0),
    0,
  ),
);

const tabLabel = (tab) =>
  `${tab.label} (${tab.name === "all" ? allCount.value : (counts.value[tab.name] ?? 0)})`;

async function load() {
  loading.value = true;
  try {
    // api.contestlist.reviewQueue 已注入 plugin=contestlist，队列不会混进别的工具
    const data = await api.contestlist.reviewQueue({
      status: query.tab,
      q: query.q.trim() || undefined,
      page: query.page,
      page_size: query.page_size,
    });
    items.value = data.items;
    total.value = data.total;
    if (data.counts) counts.value = data.counts;
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

/**
 * 点提交表里的比赛名 -> 去列表页看这场比赛。
 *
 * 搜的是 `row.img_key`（提交时那条的身份哈希）：改过平台/时间/名称之后哈希会变，
 * 用提交值里的新字段去搜是找不到原条目的。
 */
function openContest(row) {
  router.push({ name: "contestlist-home", query: { q: row.img_key } });
}

async function loadOverview() {
  try {
    overview.value = await api.curation.pluginOverview("contestlist");
    counts.value = overview.value.submission_counts ?? counts.value;
  } catch {
    /* 概览失败不影响队列本身 */
  }
}

/**
 * 体检一次「已通过但已经不能直接下发」的提交单，让它们进「冲突待裁定」表。
 *
 * 冲突判定本来只在下发那一瞬间做，而下发预览是只读的（dry-run 不改状态），
 * 所以在那之前冲突表一直是空的 —— 预览里提醒了一句「去处理冲突」，
 * 点过去却没有东西。这里主动跑一遍同一套判定（不写数据文件），幂等。
 */
async function scanConflicts() {
  try {
    const result = await api.contestlist.scanConflicts();
    return result?.total ?? 0;
  } catch {
    /* 体检失败不影响队列本身；真的下发时还会再判一次 */
    return 0;
  }
}

async function reload() {
  // 先体检再取队列：这样新挂起来的冲突在本次渲染里就能看到
  await scanConflicts();
  await Promise.all([load(), loadOverview()]);
}

function switchTab(name) {
  query.tab = name;
  query.page = 1;
  selection.value = [];
  load();
}

async function jumpToConflicts() {
  // 先体检：从下发预览点「去处理冲突」过来时，冲突还没挂进表里
  await scanConflicts();
  switchTab("conflict");
}

function onChangeSelection(rows) {
  selection.value = rows;
}

/**
 * 批量审核：一次通过 / 驳回勾选的多条。
 *
 * 走框架的 `POST /api/plugins/curation/admin/review/batch`（和 PickOne 的批量按钮同一条路），
 * 逐条独立处理，返回成功与失败各自的条数 —— 其中一条失败不影响其它条。
 */
async function batchReview(approve) {
  if (!batchIds.value.length) {
    ElMessage.warning("请先勾选待审核的提交");
    return;
  }

  try {
    await ElMessageBox.confirm(
      `确定批量${approve ? "通过" : "驳回"} ${batchIds.value.length} 条提交吗？`,
      "批量审核",
      { type: "warning" },
    );
  } catch {
    return;
  }

  try {
    const result = await api.curation.reviewBatch({
      ids: batchIds.value,
      approve,
      comment: "",
    });
    ElMessage.success(
      `成功 ${result.succeeded.length} 条，失败 ${result.failed.length} 条`,
    );
    selection.value = [];
    await reload();
  } catch (error) {
    ElMessage.error(error.message);
  }
}

function openReview(row, approve) {
  reviewDialog.open = true;
  reviewDialog.row = row;
  reviewDialog.approve = approve;
  reviewDialog.comment = "";
}

/**
 * 审核弹窗里的改动对比（与 PickOne 的审核弹窗同一套）。
 *
 * 只给新值没法判断这次改动对不对：审核员要看到「原值 → 新值」。
 * 形状是固定的，所以直接按类型分支：
 *   修改 —— 单字段，原值从提交时的快照里取那一格，一个「改动」框；
 *   新增 —— 整条都是新的，用**一个**「新建」框把六个字段列出来，
 *           而不是给每个字段画一个「（空）→ xxx」的改动框（那只是噪音）；
 *   删除 —— 没有值，说明删的是哪一条即可。
 */
function reviewDiff(row) {
  if (!row) return { kind: "none", rows: [] };

  if (row.type === "contest_delete") {
    return {
      kind: "delete",
      rows: [
        {
          field: "deleted",
          label: "这条比赛",
          before: row.target,
          after: "从列表移除",
        },
      ],
    };
  }

  if (row.type === "contest_create") {
    const after = row.submitted_value ?? {};
    return {
      kind: "create",
      rows: FIELD_ORDER.filter((name) => name in after).map((name) => ({
        field: name,
        label: contestFieldLabel(name),
        value: renderValueOf(name, after[name]),
      })),
    };
  }

  const name = row.field;
  if (!name) return { kind: "none", rows: [] };
  return {
    kind: "update",
    rows: [
      {
        field: name,
        label: contestFieldLabel(name),
        before: renderValueOf(name, (row.base_value ?? {})[name]),
        after: renderValueOf(name, row.submitted_value),
      },
    ],
  };
}

/** 某一格在对比里的可读文案（和提交表、编辑抽屉同一套渲染） */
function renderValueOf(name, value) {
  if (name === "start_time") return value ? formatTime(value) : "（空）";
  if (name === "duration") return formatDuration(value);
  if (value === null || value === undefined || value === "") return "（空）";
  return String(value);
}

/**
 * 下发预览里一格的可读文案。
 *
 * 只有修改类会走到这里：摘要里的 old_value / new_value 是那个字段的原值与新值。
 * 新增与删除在模板里单独说（整条新条目 / 整条移除），没有「从什么变成什么」。
 */
function previewValue(item, value) {
  return renderValueOf(item.field, value);
}

/** 当前审核弹窗里那条提交的对比（模板里要多次读，缓存一次） */
const reviewDialogDiff = computed(() => reviewDiff(reviewDialog.row));

/** 审核弹窗的标题：改动类说清改哪个字段，新增/删除说动作 */
const reviewDialogTitle = computed(() => {
  return `${reviewDialog.approve ? "通过" : "驳回"}提交`;
});

async function confirmReview() {
  reviewDialog.submitting = true;
  try {
    await api.curation.review(reviewDialog.row.id, {
      approve: reviewDialog.approve,
      comment: reviewDialog.comment.trim(),
    });
    ElMessage.success(reviewDialog.approve ? "已通过，等待下发" : "已驳回");
    reviewDialog.open = false;
    await reload();
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    reviewDialog.submitting = false;
  }
}

/**
 * 撤回审核：把「已通过待下发」的提交退回「待审核」。
 * 适用场景：审错了、想再等等、想和别的改动一起发。已经下发（写进文件）的退不了。
 */
async function revokeReview(row) {
  try {
    await ElMessageBox.confirm(
      `撤回后 #${row.id}（${row.type_label}）会退回「待审核」，需要重新审核。确定吗？`,
      "撤回审核",
      { type: "warning", confirmButtonText: "撤回审核" },
    );
  } catch {
    return;
  }

  try {
    await api.curation.unreview(row.id);
    ElMessage.success("已撤回审核，退回待审核");
    await reload();
  } catch (error) {
    ElMessage.error(error.message);
  }
}

function openConflict(row) {
  conflictRow.value = row;
  allowDuplicate.value = false;
  conflictOpen.value = true;
}

/**
 * 冲突弹窗里的三方对比。
 *
 * 冲突只可能落在**一个字段**上：新增/删除不参与「谁改了同一处」的竞争，所以这里只
 * 拼一格，用审核弹窗那套「原值 → 新值」的改动框展示。中间那一格是「现在的对手」
 * （对手可能换人，见后端 refresh_conflicts），裁定就是在这两格之间选一个。
 */
const conflictDiff = computed(() => {
  const row = conflictRow.value;
  if (!row) return null;
  const detail = row.conflict_detail ?? {};
  const field = row.field || null;
  // 修改类：base_value / current_value 是整条比赛，取自己那一格；没有字段名就整条渲染
  const cell = (value) =>
    field ? renderValueOf(field, (value ?? {})[field]) : renderValue(value);
  return {
    label: field ? contestFieldLabel(field) : "整条比赛",
    base: cell(detail.base_value),
    current: cell(detail.current_value),
    currentLabel: detail.rival_id ? "另一条待下发的修改" : "文件当前值",
    currentNote: detail.rival_id
      ? `#${detail.rival_id} · ${detail.rival_author_name || "另一位用户"}`
      : "",
    submitted: field
      ? renderValueOf(field, row.submitted_value)
      : renderValue(row.submitted_value),
  };
});

async function resolveConflict(keepNew) {
  if (!conflictRow.value) return;
  conflictSubmitting.value = keepNew ? "keep" : "discard";
  try {
    const result = await api.contestlist.resolveConflict(
      conflictRow.value.id,
      keepNew,
      allowDuplicate.value,
    );
    ElMessage.success(resolveMessage(result, keepNew));
    conflictOpen.value = false;
    await reload();
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    conflictSubmitting.value = "";
  }
}

/**
 * 裁定结果的提示文案。
 *
 * 「保留新值」不再立刻写盘，而是一次**交换**：这条排回「待下发」，被它取代的那条
 * （还在待下发的对手）改成「已驳回」，真正写盘交给下一次一键下发。
 */
function resolveMessage(result, keepNew) {
  if (!keepNew) return "已丢弃该提交，文件保持当前值";
  const superseded = (result.superseded ?? []).map(
    (item) => `#${item.submission_id}`,
  );
  const tail = superseded.length ? `，${superseded.join("、")} 已驳回` : "";
  return `已裁定：本条进入「待下发」${tail}。点「一键下发」写入比赛列表。`;
}

async function openPreview() {
  previewing.value = true;
  try {
    preview.value = await api.curation.pluginApplyPreview("contestlist");
    previewOpen.value = true;
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    previewing.value = false;
  }
}

async function doApply() {
  const count = preview.value?.submissions ?? approvedCount.value;
  const held = previewConflicts.value.length;
  try {
    await ElMessageBox.confirm(
      held
        ? `将写入 ${count} 条改动，另有 ${held} 条会因原值被改动而暂缓下发。确定继续吗？`
        : `将把 ${count} 条已审核的改动写入比赛列表，确定继续吗？`,
      "一键下发",
      { type: "warning", confirmButtonText: "确认写入" },
    );
  } catch {
    return;
  }

  applying.value = true;
  try {
    const result = await api.curation.pluginApply("contestlist");
    const conflicts = result.conflicts?.length ?? 0;
    if (conflicts) {
      ElMessage.warning(
        `已写入 ${result.applied ?? 0} 条；另有 ${conflicts} 条暂缓下发（原值被改动，或同一处已被更早过审的改动占了），请到「冲突待裁定」处理`,
      );
    } else {
      ElMessage.success(
        `已写入 ${result.applied ?? 0} 条：新增 ${result.created ?? 0}、修改 ${result.updated ?? 0}、删除 ${result.deleted ?? 0}`,
      );
    }
    previewOpen.value = false;
    await reload();
    if (conflicts) switchTab("conflict");
  } catch (error) {
    ElMessage.error(error.message);
  } finally {
    applying.value = false;
  }
}

watch(() => route.fullPath, reload);
onMounted(reload);
</script>

<template>
  <div class="ep-page">
    <div class="ep-page-head">
      <div>
        <h1 class="ep-title">审核台</h1>
        <p class="ep-subtitle">审核通过后可一键下发以应用更改</p>
      </div>
      <div class="ep-actions">
        <el-button
          :loading="loading"
          :icon="Refresh"
          size="large"
          @click="reload"
          >刷新</el-button
        >
        <!--
          下发统一走「预览 -> 确认」两步：这里只负责打开预览抽屉，
          真正的写盘动作在抽屉底部确认。
        -->
        <el-button
          type="primary"
          size="large"
          :icon="Upload"
          :disabled="!approvedCount"
          :loading="previewing"
          @click="openPreview"
        >
          一键下发{{ approvedCount ? ` (${approvedCount})` : "" }}
        </el-button>
      </div>
    </div>

    <div class="ep-stats ep-section">
      <div class="ep-stat">
        <div class="ep-stat-label">待审核</div>
        <div class="ep-stat-value" :class="{ 'is-warn': pendingCount > 0 }">
          {{ pendingCount }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">已通过待下发</div>
        <div class="ep-stat-value" :class="{ 'is-ok': approvedCount > 0 }">
          {{ approvedCount }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">冲突待裁定</div>
        <div class="ep-stat-value" :class="{ 'is-danger': conflictCount > 0 }">
          {{ conflictCount }}
        </div>
        <div class="ep-stat-hint">
          {{ overview?.lib_available === false ? "数据文件不可用" : "" }}
        </div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">累计已下发</div>
        <div class="ep-stat-value">{{ appliedCount }}</div>
      </div>
      <div class="ep-stat">
        <div class="ep-stat-label">已驳回</div>
        <div class="ep-stat-value">{{ rejectedCount }}</div>
      </div>
    </div>

    <el-alert
      v-if="overview && overview.lib_available === false"
      type="error"
      :closable="false"
      show-icon
      class="ep-mb"
      title="比赛列表文件不可用"
      :description="'后端读不到比赛列表文件，现在只能审核，下发会失败'"
    />

    <div class="ep-card ep-card--flush">
      <el-tabs v-model="query.tab" class="queue-tabs" @tab-change="switchTab">
        <el-tab-pane
          v-for="tab in TABS"
          :key="tab.name"
          :name="tab.name"
          :label="tabLabel(tab)"
        />
      </el-tabs>

      <div class="queue-body">
        <!-- 同一行的控件统一 size，保证高度齐平 -->
        <div class="toolbar ep-mb">
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

          <el-button size="large" @click="applyQuery">查询</el-button>

          <template v-if="batchIds.length">
            <span class="ep-small ep-muted">已选 {{ batchIds.length }} 条</span>
            <el-button size="large" type="success" @click="batchReview(true)"
              >批量通过</el-button
            >
            <el-button
              size="large"
              type="danger"
              plain
              @click="batchReview(false)"
            >
              批量驳回
            </el-button>
          </template>

          <span class="ep-small ep-faint total-hint">共 {{ total }} 条</span>
        </div>

        <SubmissionTable
          :items="items"
          :loading="loading"
          show-review
          show-author
          selectable
          :batch-enabled="batchEnabled"
          @review="openReview"
          @unreview="revokeReview"
          @resolve="openConflict"
          @open="openContest"
          @selection-change="onChangeSelection"
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

    <!-- 审核确认 -->
    <el-drawer
      v-model="reviewDialog.open"
      :title="reviewDialogTitle"
      size="min(620px, 92vw)"
    >
      <template v-if="reviewDialog.row">
        <el-descriptions :column="2" size="small" border class="ep-mb">
          <el-descriptions-item label="提交">
            #{{ reviewDialog.row.id }} ·
            {{ reviewDialog.row.field_label || reviewDialog.row.type_label }}
          </el-descriptions-item>
          <el-descriptions-item label="目标">
            {{ reviewDialog.row.target }}
            <span class="ep-small ep-faint">{{
              reviewDialog.row.img_key
            }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="提交者">
            {{ reviewDialog.row.author_name || "—" }}
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ formatTime(reviewDialog.row.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item
            v-if="reviewDialog.row.note"
            label="提交备注"
            :span="2"
          >
            {{ reviewDialog.row.note }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 改动前后对比：只给新值没法判断这次改动对不对（与 PickOne 的审核弹窗一致） -->
        <div class="diff-block ep-mb">
          <!-- 新增：一个「新建」框装下整条比赛，不给每个字段画框说「（空）→ xxx」 -->
          <div v-if="reviewDialogDiff.kind === 'create'" class="diff-row">
            <div class="diff-head">
              <span class="diff-field">新建 · 整条比赛</span>
              <span class="ep-small ep-faint"
                >下发后这会成为列表里的一条新比赛</span
              >
            </div>
            <div class="new-entry">
              <div
                v-for="field in reviewDialogDiff.rows"
                :key="field.field"
                class="new-entry-cell"
              >
                <span class="new-entry-label">{{ field.label }}</span>
                <span class="new-entry-value">{{ field.value }}</span>
              </div>
            </div>
          </div>

          <!-- 修改与删除：逐条「原值 → 新值」 -->
          <div
            v-for="field in reviewDialogDiff.kind === 'create'
              ? []
              : reviewDialogDiff.rows"
            :key="field.field"
            class="diff-row"
          >
            <div class="diff-head">
              <span class="diff-field">
                {{ reviewDialogDiff.kind === "delete" ? "删除" : "改动" }} ·
                {{ field.label }}
              </span>
            </div>
            <div class="diff-values">
              <div class="diff-side">
                <span class="ep-small ep-faint">原值</span>
                <span class="diff-old">{{ field.before }}</span>
              </div>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <div class="diff-side">
                <span class="ep-small ep-faint">新值</span>
                <span class="diff-new">{{ field.after }}</span>
              </div>
            </div>
          </div>
        </div>

        <el-alert
          v-if="reviewDialog.row.type === 'contest_delete'"
          type="warning"
          :closable="false"
          show-icon
          class="ep-mb"
          title="这是一条删除提交"
          :description="`通过并下发后，文件里「${reviewDialog.row.target}」会被移除`"
        />

        <!--
          身份哈希变了要显式说：ID 就是「平台 + 开始时间 + 名称」算出来的，
          改了它等于换了一条身份，下发之后列表里那个 ID 会变成新的。
        -->
        <div v-if="reviewDialog.row.identity_changed" class="id-change ep-mb">
          <span class="ep-small ep-faint">ID 变化</span>
          <span class="ep-mono ep-small diff-old">{{
            reviewDialog.row.base_hash
          }}</span>
          <el-icon class="diff-arrow"><Right /></el-icon>
          <span class="ep-mono ep-small diff-new">{{
            reviewDialog.row.new_hash
          }}</span>
        </div>

        <el-input
          v-model="reviewDialog.comment"
          type="textarea"
          :rows="2"
          maxlength="200"
          placeholder="审核备注（可选，会展示给提交者）"
        />
      </template>
      <template #footer>
        <el-button @click="reviewDialog.open = false">取消</el-button>
        <el-button
          :type="reviewDialog.approve ? 'success' : 'danger'"
          :loading="reviewDialog.submitting"
          @click="confirmReview"
        >
          {{ reviewDialog.approve ? "确认通过" : "确认驳回" }}
        </el-button>
      </template>
    </el-drawer>

    <!-- 下发预览（只读 dry-run） -->
    <el-drawer v-model="previewOpen" size="min(620px, 92vw)" title="应用预览">
      <template v-if="preview">
        <el-alert
          v-if="previewConflicts.length"
          type="warning"
          :closable="false"
          show-icon
          class="ep-mb"
          :title="`${previewConflicts.length} 条提交存在冲突，本轮不会下发`"
        >
          <div
            v-for="item in previewConflicts"
            :key="item.submission_id"
            class="ep-small ep-break"
          >
            #{{ item.submission_id }} —— {{ item.reason }}
          </div>
          <el-button
            size="small"
            type="warning"
            class="alert-btn"
            @click="((previewOpen = false), jumpToConflicts())"
          >
            去处理冲突
          </el-button>
        </el-alert>

        <div class="ep-stats ep-mb preview-stats">
          <div class="ep-stat">
            <div class="ep-stat-label">将写入</div>
            <div class="ep-stat-value">{{ preview.submissions }}</div>
          </div>
          <div class="ep-stat">
            <div class="ep-stat-label">新增</div>
            <div class="ep-stat-value">{{ preview.created?.length ?? 0 }}</div>
          </div>
          <div class="ep-stat">
            <div class="ep-stat-label">修改</div>
            <div class="ep-stat-value">{{ preview.updated?.length ?? 0 }}</div>
          </div>
          <div class="ep-stat">
            <div class="ep-stat-label">删除</div>
            <div class="ep-stat-value">{{ preview.deleted?.length ?? 0 }}</div>
          </div>
        </div>

        <div class="ep-section-label">逐条改动</div>
        <el-empty
          v-if="!(preview.details ?? []).length"
          description="没有可下发的改动"
          :image-size="60"
        />
        <div v-else class="preview-list">
          <!--
            下发前要逐条核对，所以每一行都写清「哪一场、哪个字段、从什么变成什么」。
            新增/删除是整条动作，没有字段，退回动作名。
          -->
          <div
            v-for="item in preview.details"
            :key="item.submission_id"
            class="preview-line"
          >
            <span class="ep-chip ep-chip--accent">{{
              item.field_label || item.type_label
            }}</span>
            <span class="ep-small ep-break">{{ item.label }}</span>
            <!-- 新增是整条新的、删除是整条没了：都没有「从什么变成什么」这一说 -->
            <span
              v-if="item.type === 'contest_create'"
              class="ep-small diff-new"
              >整条新条目</span
            >
            <span
              v-else-if="item.type === 'contest_delete'"
              class="ep-small diff-old"
            >
              整条移除
            </span>
            <span v-else class="preview-change">
              <span class="diff-old">{{
                previewValue(item, item.old_value)
              }}</span>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <span class="diff-new">{{
                previewValue(item, item.new_value)
              }}</span>
            </span>
            <span class="ep-small ep-faint">#{{ item.submission_id }}</span>

            <!-- 新增类把整条比赛摊开：下发前要核对的就是这些字段 -->
            <div v-if="item.type === 'contest_create'" class="new-entry">
              <div
                v-for="name in FIELD_ORDER"
                :key="name"
                class="new-entry-cell"
              >
                <span class="new-entry-label">{{
                  contestFieldLabel(name)
                }}</span>
                <span class="new-entry-value">{{
                  renderValueOf(name, item.entry?.[name])
                }}</span>
              </div>
            </div>

            <!-- 身份哈希（界面上的 ID）变了：下发后这个 ID 就不是原来那个了 -->
            <div v-if="item.identity_changed" class="id-change">
              <span class="ep-small ep-faint">ID 变化</span>
              <span class="ep-mono ep-small diff-old">{{
                item.id_before
              }}</span>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <span class="ep-mono ep-small diff-new">{{ item.id_after }}</span>
            </div>
          </div>
        </div>
      </template>

      <template #footer>
        <span class="ep-small ep-faint preview-foot-hint"
          >确认后将写入 OBot-ACM 的数据文件</span
        >
        <el-button @click="previewOpen = false">关闭</el-button>
        <el-button
          type="primary"
          :disabled="!preview?.submissions"
          :loading="applying"
          @click="doApply"
        >
          确认下发
        </el-button>
      </template>
    </el-drawer>

    <!-- 冲突裁定：三方对比 -->
    <el-drawer v-model="conflictOpen" title="冲突处理" size="min(720px, 92vw)">
      <template v-if="conflictRow">
        <el-alert
          type="error"
          :closable="false"
          show-icon
          class="ep-mb"
          :title="conflictRow.conflict_reason || '原值已被改动'"
        >
        </el-alert>

        <el-descriptions :column="2" size="small" border class="ep-mb">
          <el-descriptions-item label="提交 ID"
            >#{{ conflictRow.id }}</el-descriptions-item
          >
          <el-descriptions-item label="类型">{{
            conflictRow.type_label
          }}</el-descriptions-item>
          <el-descriptions-item label="目标">{{
            conflictRow.target
          }}</el-descriptions-item>
          <el-descriptions-item label="提交者">
            {{ conflictRow.author_name || "—" }}
          </el-descriptions-item>
          <el-descriptions-item
            v-if="conflictRow.note"
            label="提交备注"
            :span="2"
          >
            {{ conflictRow.note }}
          </el-descriptions-item>
        </el-descriptions>

        <!--
          冲突只可能落在**一个字段**上（同一处被两个人改；新增/删除不参与这个竞争），
          所以用审核弹窗那套「原值 → 新值」的改动框展示就够了，不必再画一张表。
          中间那一格是「现在的对手」，裁定就是在中间和右边之间选一个。
        -->
        <div v-if="conflictDiff" class="diff-block ep-mb">
          <div class="diff-row">
            <div class="diff-head">
              <span class="diff-field">冲突 · {{ conflictDiff.label }}</span>
            </div>
            <div class="diff-values">
              <div class="diff-side">
                <span class="ep-small ep-faint">提交时看到</span>
                <span class="diff-old">{{ conflictDiff.base }}</span>
              </div>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <div class="diff-side">
                <span class="ep-small ep-faint">{{
                  conflictDiff.currentLabel
                }}</span>
                <span class="diff-current">{{ conflictDiff.current }}</span>
                <span v-if="conflictDiff.currentNote" class="ep-small ep-faint">
                  {{ conflictDiff.currentNote }}
                </span>
              </div>
              <el-icon class="diff-arrow"><Right /></el-icon>
              <div class="diff-side">
                <span class="ep-small ep-faint">这条提交的新值</span>
                <span class="diff-new">{{ conflictDiff.submitted }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="choice-row">
          <el-card
            shadow="never"
            class="choice-card"
            @click="resolveConflict(true)"
          >
            <div class="choice-title">
              <el-icon><Select /></el-icon> 保留提交的新值
            </div>
            <div class="choice-desc ep-small ep-muted">
              本条提交进入待下发流程，另一条提交将被驳回。
              仅在你确认提交内容比其他人的改动更准确时选择。
            </div>
            <el-button
              type="primary"
              class="choice-btn"
              :loading="conflictSubmitting === 'keep'"
              @click.stop="resolveConflict(true)"
            >
              采用这条修改
            </el-button>
          </el-card>

          <el-card
            shadow="never"
            class="choice-card"
            @click="resolveConflict(false)"
          >
            <div class="choice-title">
              <el-icon><CloseBold /></el-icon> 丢弃这条提交
            </div>
            <div class="choice-desc ep-small ep-muted">
              本条提交将被驳回， 提交者可以在浏览页看到最新值后重新提交。
            </div>
            <el-button
              class="choice-btn"
              :loading="conflictSubmitting === 'discard'"
              @click.stop="resolveConflict(false)"
            >
              保留文件现值
            </el-button>
          </el-card>
        </div>
      </template>
      <template #footer>
        <el-button @click="conflictOpen = false">稍后处理</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.queue-tabs {
  padding: 6px 20px 0;
}

.queue-body {
  padding: 4px 20px 18px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.total-hint {
  margin-left: auto;
}

.alert-btn {
  margin-top: 6px;
}

.preview-foot-hint {
  float: left;
  line-height: 40px;
}

/*
 * 抽屉比页面窄，`ep-stats` 的 `flex: 1 1 160px` 会在这里折成两行（四项至少 640px）。
 * 汇总就四个数字，压成一行更容易一眼扫完。
 */
.preview-stats {
  flex-wrap: nowrap;
  margin-bottom: 24px;
}

.preview-stats .ep-stat {
  flex: 1 1 0;
  min-width: 0;
  padding: 24px 28px;
}

.preview-stats .ep-stat-label {
  font-size: 11px;
  letter-spacing: 0.04em;
}

.preview-stats .ep-stat-value {
  margin-top: 4px;
  font-size: 22px;
}

.preview-list {
  display: flex;
  flex-direction: column;
}

.preview-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 0;
  flex-wrap: wrap;
}

/* 预览里的那一格改动：原值 → 新值，跟审核弹窗同一套配色 */
.preview-change {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.preview-change .diff-arrow {
  margin-top: 0;
  font-size: 11px;
}

/* 新增类的详细信息：整条比赛摊开，两列就够（抽屉窄，排太多列反而挤） */
.new-entry {
  flex: 0 0 100%;
  width: 100%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 18px;
  margin: 4px 0 8px;
  padding: 8px 12px;
  border-radius: var(--ep-radius-sm);
  background: var(--ep-surface-sunken);
}

.new-entry-cell {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.new-entry-label {
  flex: none;
  width: 64px;
  font-size: 12px;
  color: var(--ep-ink-faint);
}

.new-entry-value {
  font-size: 12.5px;
  word-break: break-word;
}

/* 预览行里的 ID 变化：整行铺开，紧跟在字段详情下面 */
.preview-line .id-change {
  flex: 0 0 100%;
  width: 100%;
  margin: 2px 0 6px;
  padding: 6px 12px;
}

/* 冲突裁定的两个选项：并排两张卡片，点哪张就选哪边 */
.choice-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 14px;
}

.choice-card {
  cursor: pointer;
  border-radius: var(--ep-radius-sm);
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.choice-card:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 4px 14px rgba(23, 26, 38, 0.08);
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

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

/* ID（身份哈希）变化的那一行 */
.id-change {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border: 1px solid var(--ep-warn-border, var(--ep-border));
  border-radius: var(--ep-radius-sm);
  background: var(--ep-warn-soft, var(--ep-surface-sunken));
}

.id-change .diff-arrow {
  margin-top: 0;
}

/* 审核弹窗里的改动对比（与 PickOne 的审核弹窗同一套样式） */
.diff-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.diff-row {
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius-sm);
  padding: 12px 14px;
  background: var(--ep-surface-sunken);
}

.diff-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.diff-field {
  font-size: 13px;
  font-weight: 700;
}

.diff-values {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.diff-side {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.diff-old,
.diff-new,
.diff-current {
  font-size: 12.5px;
  line-height: 1.6;
  word-break: break-word;
}

.diff-old {
  color: var(--ep-ink-muted);
  text-decoration: line-through;
}

.diff-new {
  color: var(--ep-ink);
  font-weight: 600;
}

/* 冲突裁定里的「另一条待下发的修改 / 文件当前值」：将要生效的那一格，用告警色标出来 */
.diff-current {
  color: var(--el-color-warning);
  font-weight: 600;
}

.diff-arrow {
  flex: none;
  margin-top: 18px;
  font-size: 12px;
  color: var(--ep-ink-faint);
}

@media (max-width: 720px) {
  .choice-row {
    grid-template-columns: 1fr;
  }
}
</style>
