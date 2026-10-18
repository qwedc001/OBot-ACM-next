<script setup>
/**
 * 比赛编辑抽屉：新增与修改共用。
 *
 * 和 PickOne 的图片编辑抽屉一套做法（`el-drawer` 从右侧滑出 + 底部说明栏）：
 *
 *   - 表单起点优先「我自己的在途提交」，否则磁盘原值。别人的在途改动**绝不进表单**
 *     —— 那样等于替别人「打补丁」，最后整条改动都算在我头上；
 *   - 所有在途改动都列在顶部那个只读块里，连作者和状态一起，我的那条标绿；
 *   - 底部那行说明写清「点提交会发生什么」（新增 / 更新我上次的提交 / 撤回去）；
 *   - 没有实际改动时提交按钮是禁用的，不会提交一条「改了等于没改」的提交。
 */
import { computed, reactive, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "@/api";
import { formatTime, renderValue, statusMeta } from "@/utils/format";
import {
  FIELD_ORDER,
  contestFieldLabel,
  formatDuration,
  pendingFieldValue,
} from "../format";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 'create' | 'update' */
  mode: { type: String, default: "create" },
  /**
   * 修改时：磁盘原值；新增时：null；
   * **改自己待新增的草稿时**：草稿那六个字段（当起点用，不参与逐字段提交那套）。
   */
  item: { type: Object, default: null },
  /** 列表里已经出现过的平台，作为输入建议（不是受控取值，随便填别的也行） */
  platformOptions: { type: Array, default: () => [] },
});

const emit = defineEmits(["update:modelValue", "submitted"]);

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const isUpdate = computed(() => props.mode === "update");

/** 正在改自己「待新增」的草稿：也是新增，但起点是草稿的值而不是空表单 */
const isDraft = computed(() => !isUpdate.value && props.item !== null);

/** 这场比赛的名称里带 "（滚木）" 之类，标题里放全称太长，用简称 */
const title = computed(() => {
  if (isUpdate.value) return "修改比赛信息";
  return isDraft.value ? "修改待新增的比赛" : "新增比赛";
});

/**
 * 表单起点：磁盘原值 + 我自己的在途改动。
 *
 * 逐字段拆分提交之后，我可能在这场比赛上有好几条在途提交（不同字段），
 * 所以这里按字段逐条叠加；别人的在途改动不进表单。
 */
const baseline = computed(() => {
  const disk = props.item ?? {};
  const base = {
    platform: disk.platform ?? "",
    abbr: disk.abbr ?? "",
    name: disk.name ?? "",
    start_time: Number(disk.start_time ?? 0),
    duration: Number(disk.duration ?? 18000),
    supplement: disk.supplement ?? "",
  };
  for (const [name, row] of myPendingByField.value) {
    const value = pendingFieldValue(row, name);
    if (value !== undefined)
      base[name] =
        name === "start_time" || name === "duration" ? Number(value) : value;
  }
  return base;
});

const form = reactive({
  platform: "ICPC",
  abbr: "",
  name: "",
  // el-date-picker 的 value-format="x" 给的是毫秒时间戳字符串，nil 时是 null
  start_ms: null,
  duration_minutes: 300,
  supplement: "",
  note: "",
});

function seed() {
  // 新增时没有原值可参照，开始时间默认给「现在」
  const startSeconds =
    baseline.value.start_time || Math.floor(Date.now() / 1000);
  form.platform = baseline.value.platform;
  form.abbr = baseline.value.abbr;
  form.name = baseline.value.name;
  form.start_ms = startSeconds * 1000;
  form.duration_minutes = Math.round(baseline.value.duration / 60);
  form.supplement = baseline.value.supplement;
  form.note = "";
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) seed();
  },
);

/** 一个字段值在「在途改动」块里的可读形式 */
function fieldText(name, value) {
  if (name === "start_time") return value ? formatTime(value) : "（空）";
  if (name === "duration") return formatDuration(value);
  return renderValue(value);
}

/**
 * 审核中的改动展开成逐字段的行，和 PickOne 的编辑抽屉一套提示方式：
 * 字段 chip（绿色是我的）· 作者 + 状态 · 磁盘原值 → 这条改成的值。
 *
 * 改动是**逐字段拆开提交**的，所以这里一条在途提交就是一行；同时兼容
 * 早期「一条提交带全部字段」的旧数据（那时 `value` 是整条对象）。
 */
const pendingRows = computed(() => {
  const disk = props.item ?? {};
  const rows = [];
  for (const item of props.item?.pending_changes ?? []) {
    for (const name of FIELD_ORDER) {
      const after = pendingFieldValue(item, name);
      if (after === undefined) continue;
      const before = disk[name] ?? "";
      if (String(before) === String(after)) continue;
      rows.push({
        key: `${item.submission_id}-${name}`,
        label: contestFieldLabel(name),
        who: item.mine ? "我" : item.author_name,
        status: statusMeta(item.status).short,
        mine: item.mine,
        before: fieldText(name, before),
        after: fieldText(name, after),
      });
    }
  }
  return rows;
});

/**
 * 我自己在这场比赛上的在途提交，按字段索引。
 *
 * 逐字段拆分之后，「我有一条在途提交」这个说法不再够用 —— 得知道是哪几个字段，
 * 才能把表单起点补成「磁盘值 + 我的在途改动」，也才能在某个字段改回原值时
 * 精确撤掉那一条（而不是把别的字段的提交一起撤了）。
 */
const myPendingByField = computed(() => {
  const map = new Map();
  for (const item of props.item?.pending_changes ?? []) {
    if (!item.mine) continue;
    for (const name of FIELD_ORDER) {
      if (pendingFieldValue(item, name) !== undefined) map.set(name, item);
    }
  }
  return map;
});

/** 提交给后端的值（时间从毫秒换算回 Unix 秒） */
const payload = computed(() => ({
  platform: form.platform,
  abbr: form.abbr.trim(),
  name: form.name.trim(),
  start_time: form.start_ms ? Math.floor(Number(form.start_ms) / 1000) : 0,
  duration: Math.round(Number(form.duration_minutes) * 60),
  supplement: form.supplement.trim(),
}));

/** 相对表单起点动过的字段（用来写底部说明，也用来判断有没有东西可提交） */
const changes = computed(() => {
  const base = baseline.value;
  const value = payload.value;
  const list = [];
  for (const field of FIELD_ORDER) {
    if (value[field] !== base[field]) list.push(contestFieldLabel(field));
  }
  return list;
});

/**
 * 改回磁盘原值的字段：我在这些字段上有在途提交，但表单又和磁盘值一致了。
 *
 * 逐字段拆分提交之后这件事也是逐字段的：把「时长」改回去只该撤掉时长那一条，
 * 不能连「简称」那条一起撤。所以这里返回**字段名列表**，提交时逐个撤回。
 */
const revertedFields = computed(() => {
  if (!isUpdate.value || !myPendingByField.value.size) return [];
  const disk = props.item ?? {};
  const value = payload.value;
  return FIELD_ORDER.filter(
    (field) =>
      myPendingByField.value.has(field) &&
      String(value[field]) === String(disk[field] ?? ""),
  );
});

const revertedLabels = computed(() =>
  revertedFields.value.map(contestFieldLabel),
);

/** 有没有「改了等于没改」的字段要撤回 */
const hasReverted = computed(() => revertedFields.value.length > 0);

/** 「平台 / 名称 / 开始时间」是唯一性判据，改了就等于变成另一场比赛，值得提醒一次 */
const identityChanged = computed(() => {
  if (!isUpdate.value) return false;
  const disk = props.item ?? {};
  const value = payload.value;
  return (
    value.platform !== disk.platform ||
    value.name !== disk.name ||
    value.start_time !== Number(disk.start_time)
  );
});

/**
 * 表单现在就是磁盘原值（「还原」没什么可还原的）。
 *
 * 新增时没有原值可还原，所以永远禁用。
 */
const formAtOriginal = computed(() => {
  if (!isUpdate.value) return true;
  const disk = props.item ?? {};
  const value = payload.value;
  return (
    value.platform === disk.platform &&
    value.abbr === disk.abbr &&
    value.name === disk.name &&
    value.start_time === Number(disk.start_time) &&
    value.duration === Number(disk.duration) &&
    value.supplement === (disk.supplement ?? "")
  );
});

/**
 * 还原：只把这个抽屉里的输入复位到**磁盘原值**，不动任何提交。
 *
 * 有在途提交时，复位后表单就和原值一致了 —— 这时提交会把那条在途提交撤掉
 * （见 submit），而不是提交一条没有变化的改动。和 PickOne 的图片编辑抽屉同一个行为。
 */
function reset() {
  const disk = props.item ?? {};
  form.platform = disk.platform ?? "";
  form.abbr = disk.abbr ?? "";
  form.name = disk.name ?? "";
  form.start_ms = Number(disk.start_time ?? 0) * 1000;
  form.duration_minutes = Math.round(Number(disk.duration ?? 18000) / 60);
  form.supplement = disk.supplement ?? "";
  form.note = "";
}

/** 平台输入建议：列表里已经出现过的写法，过滤出匹配当前输入的几个 */
function suggestPlatforms(query, callback) {
  const needle = String(query ?? "")
    .trim()
    .toLowerCase();
  const options = props.platformOptions
    .filter((name) => !needle || name.toLowerCase().includes(needle))
    .slice(0, 8)
    .map((name) => ({ value: name }));
  callback(options);
}

/** 起止时刻预览：时间最容易填错，直接算给用户看 */
const preview = computed(() => {
  const start = payload.value.start_time;
  if (!start) return "";
  return (
    `${formatTime(start)} 开始 · 持续 ${formatDuration(payload.value.duration)}` +
    ` · ${formatTime(start + payload.value.duration)} 结束`
  );
});

/** 底部说明：点提交会发生什么，一眼能看出来（说法与 PickOne 的编辑抽屉一致） */
const footHint = computed(() => {
  const mine = myPendingByField.value.size;
  if (changes.value.length) {
    const text = `待提交：${changes.value.join("、")}`;
    return mine ? `${text}（更新对应的在途提交）` : text;
  }
  if (hasReverted.value) {
    return `${revertedLabels.value.join("、")} 已改回原值，提交后会撤销对应的在途提交`;
  }
  if (mine) return "还没有改动（你的在途提交仍在审核中）";
  return "还没有改动";
});

function validate() {
  const value = payload.value;
  if (!value.platform) return "请选择平台";
  if (!value.abbr) return "简称不能为空";
  if (!value.name) return "比赛全称不能为空";
  if (!value.start_time) return "请选择开始时间";
  if (!value.duration) return "时长必须是正整数分钟";
  if (value.duration < 60 || value.duration > 7 * 24 * 3600)
    return "时长必须在 1 分钟到 7 天之间";
  return "";
}

async function submit() {
  if (!changes.value.length && !hasReverted.value) return;

  const problem = validate();
  if (problem) {
    ElMessage.warning(problem);
    return;
  }

  // 改回原值的字段：逐条撤掉对应的在途提交（只撤这些字段，别人的/别的字段不动）
  if (hasReverted.value) {
    try {
      await ElMessageBox.confirm(
        `${revertedLabels.value.join("、")} 已改回原值，提交后会撤销这些字段的在途提交`,
        "改回了原值",
        { type: "warning", confirmButtonText: "确定" },
      );
    } catch {
      return;
    }
    try {
      for (const field of revertedFields.value) {
        const row = myPendingByField.value.get(field);
        if (row) await api.contestlist.withdraw(row.submission_id);
      }
      ElMessage.success("已撤销改回原值的提交");
    } catch (error) {
      ElMessage.error(error.message);
      return;
    }
  }

  // 真正要改的字段交给父组件发出去（后端按字段拆成多条提交单）
  if (changes.value.length) {
    emit("submitted", { contest: payload.value, note: form.note.trim() });
  } else {
    emit("submitted");
  }
  visible.value = false;
}
</script>

<template>
  <el-drawer
    v-model="visible"
    size="min(600px, 92vw)"
    :title="title"
    destroy-on-close
  >
    <template v-if="isUpdate">
      <!--
        审核中的改动：磁盘原值 → 每条在途改动，连作者一起列出来（可能不止一个人）。
        绿色的是我自己的那条 —— 它已经回填进下面的表单，继续改就是给它打补丁。
        别人的内容只读：能看见，但不能作为修改起点。
      -->
      <div v-if="pendingRows.length" class="pending-box">
        <div class="pending-title ep-small">
          审核中的改动（{{ item?.pending_changes?.length ?? 0 }} 条）
        </div>
        <div v-for="row in pendingRows" :key="row.key" class="pending-row">
          <span class="ep-chip" :class="row.mine ? 'ep-chip--ok' : ''">{{
            row.label
          }}</span>
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
    </template>

    <el-form label-position="top" @submit.prevent="submit">
      <el-row :gutter="12">
        <el-col :span="8">
          <el-form-item label="平台">
            <!--
              自由输入，不是下拉框：上游 ManualContest.platform 就是个字符串，
              除了 ICPC / CCPC 还有邀请赛、校赛、AtCoder、Codeforces……
              做成下拉框等于替上游发明一套它并不认识的枚举。
              el-autocomplete 只把「列表里已经有过的写法」当建议，随便填别的也行。
            -->
            <el-autocomplete
              v-model="form.platform"
              :fetch-suggestions="suggestPlatforms"
              placeholder="如 ICPC / CCPC"
              maxlength="32"
              size="large"
              style="width: 100%"
            />
          </el-form-item>
        </el-col>
        <el-col :span="16">
          <el-form-item label="简称">
            <el-input
              v-model="form.abbr"
              maxlength="64"
              placeholder="如 济南区域赛"
              size="large"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item label="比赛全称">
        <el-input
          v-model="form.name"
          maxlength="128"
          placeholder="如 第50届ICPC国际大学生程序设计竞赛亚洲区域赛（济南）"
          size="large"
        />
      </el-form-item>

      <el-row :gutter="12">
        <el-col :span="14">
          <el-form-item label="开始时间">
            <el-date-picker
              v-model="form.start_ms"
              type="datetime"
              format="YYYY-MM-DD HH:mm"
              value-format="x"
              placeholder="选择开始时间"
              style="width: 100%"
              size="large"
            />
          </el-form-item>
        </el-col>
        <el-col :span="10">
          <el-form-item label="时长（分钟）">
            <el-input-number
              v-model="form.duration_minutes"
              :min="1"
              :max="10080"
              :step="30"
              controls-position="right"
              style="width: 100%"
              size="large"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item label="比赛地点（承办院校）">
        <el-input
          v-model="form.supplement"
          maxlength="128"
          placeholder="如 山东大学"
          size="large"
        />
      </el-form-item>

      <el-form-item label="备注给审核者（可选）">
        <el-input
          v-model="form.note"
          maxlength="200"
          placeholder="如 比赛邀请函链接"
          size="large"
        />
      </el-form-item>
    </el-form>

    <el-alert
      v-if="preview"
      type="info"
      :closable="false"
      class="preview"
      :title="preview"
    />

    <!--
      底部操作栏放在 drawer 的 footer 插槽里，而不是 body 内的 sticky 元素：
      sticky 版本会在滚动内容与按钮之间留出一条缝，底下的表单内容会透出来。
    -->
    <template #footer>
      <div class="drawer-foot">
        <span
          class="ep-small"
          :class="changes.length || hasReverted ? 'ep-muted' : 'ep-faint'"
        >
          {{ footHint }}
        </span>
        <div class="drawer-foot-actions">
          <!-- 还原到磁盘原值：不动任何提交，只是把输入复位（与 PickOne 的编辑抽屉一致） -->
          <el-button :disabled="formAtOriginal" @click="reset">还原</el-button>
          <el-button
            type="primary"
            :disabled="!changes.length && !hasReverted"
            @click="submit"
          >
            {{ myPendingByField.size ? "更新我的提交" : "提交审核" }}
          </el-button>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
/* 在途改动：只读信息块，跟可编辑的表单在视觉上分开 */
.pending-box {
  border: 1px solid var(--ep-border);
  border-radius: var(--ep-radius-sm);
  background: var(--ep-surface-sunken);
  padding: 10px 12px;
  margin-bottom: 18px;
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

.preview {
  margin-bottom: 12px;
}

/* 底部：左侧说明 + 右侧按钮（与 PickOne 的编辑抽屉同一套排版） */
.drawer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.drawer-foot-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
