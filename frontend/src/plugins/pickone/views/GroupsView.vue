<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import api from "../groups-api";
import { pluginApi as boardApi } from "@/plugins/board/api";
import { session } from "@/stores/session";

const scopes = ref([]),
  scope = ref(""),
  categories = ref([]),
  category = ref("");
const items = ref([]),
  page = ref(1),
  total = ref(0),
  pending = ref(false);
const loading = ref(false),
  busy = ref(false),
  error = ref(""),
  verification = ref("");
const editor = ref(null),
  form = reactive({
    key: "",
    title: "",
    aliases: "",
    description: "",
    comments: "",
  });
let revision = 0;

async function loadImages() {
  const current = ++revision;
  loading.value = true;
  items.value = [];
  total.value = 0;
  error.value = "";
  try {
    if (!scope.value || (!pending.value && !category.value)) return;
    const data = pending.value
      ? await api.audit(scope.value, page.value)
      : await api.images(scope.value, category.value, page.value);
    if (current === revision) {
      items.value = data.items;
      total.value = data.total;
    }
  } catch (failure) {
    if (current === revision) error.value = failure.message;
  } finally {
    if (current === revision) loading.value = false;
  }
}
async function loadCategories() {
  const current = ++revision;
  categories.value = [];
  items.value = [];
  editor.value = null;
  error.value = "";
  try {
    if (!scope.value) return;
    const data = await api.categories(scope.value);
    if (current !== revision) return;
    categories.value = data.categories;
    if (!categories.value.some((item) => item.img_key === category.value))
      category.value = categories.value[0]?.img_key ?? "";
    page.value = 1;
    await loadImages();
  } catch (failure) {
    if (current === revision) error.value = failure.message;
  }
}
async function refresh() {
  try {
    scopes.value = (await api.scopes()).scopes;
    if (!scopes.value.some((item) => item.id === scope.value))
      scope.value = scopes.value[0]?.id ?? "";
    await loadCategories();
  } catch (failure) {
    error.value = failure.message;
    items.value = [];
    categories.value = [];
  }
}
async function verify() {
  try {
    verification.value = (await boardApi.groupCheck()).command;
  } catch (failure) {
    ElMessage.error(failure.message);
  }
}
function editCategory(item = null) {
  Object.assign(form, {
    key: item?.img_key ?? "",
    title: item?.id ?? "",
    aliases: item?.keys.join("\n") ?? "",
  });
  editor.value = {
    type: item ? "category" : "create",
    scope: scope.value,
    key: item?.img_key,
  };
}
function editImage(item) {
  Object.assign(form, {
    description: item.ocr_text,
    comments: item.comments.join("\n"),
  });
  editor.value = {
    type: "image",
    scope: scope.value,
    key: category.value,
    name: item.name,
  };
}
async function save() {
  const target = editor.value;
  busy.value = true;
  try {
    if (target.type === "image")
      await api.image(target.scope, target.key, {
        name: target.name,
        ocr_text: form.description,
        comments: form.comments.split("\n").filter(Boolean),
      });
    else {
      const values = {
        category_id: form.title,
        keys: form.aliases.split("\n").filter(Boolean),
      };
      if (!values.keys.length) values.keys = [form.title];
      if (target.type === "create")
        await api.create(target.scope, form.key, values);
      else await api.category(target.scope, target.key, values);
    }
    editor.value = null;
    ElMessage.success("已保存");
    await loadCategories();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    busy.value = false;
  }
}
async function review(item, approve) {
  busy.value = true;
  try {
    await api.review(scope.value, item.img_key, item.name, approve);
    await loadImages();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    busy.value = false;
  }
}
async function remove(item) {
  const selected = scope.value,
    key = category.value;
  try {
    await ElMessageBox.confirm(
      "删除后，本群将无法再取到这张图片。",
      "删除图片",
      { type: "warning" },
    );
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.remove(selected, key, item.name);
    await loadCategories();
  } catch (failure) {
    ElMessage.error(failure.message);
  } finally {
    busy.value = false;
  }
}
function resetPage() {
  page.value = 1;
  loadImages();
}
onMounted(refresh);
</script>

<template>
  <div class="ep-page group-library">
    <header>
      <h1>群图库</h1>
      <p>图片仅在所属群内可用；公共图库继续由机器人管理员统一维护。</p>
    </header>
    <div class="library-toolbar">
      <el-select
        v-model="scope"
        aria-label="选择群图库"
        placeholder="选择群聊"
        :disabled="busy"
        @change="loadCategories"
      >
        <el-option
          v-for="item in scopes"
          :key="item.id"
          :label="item.label"
          :value="item.id"
        />
      </el-select>
      <el-button :disabled="busy" @click="refresh">刷新</el-button>
      <el-button v-if="!session.state.user?.tools" @click="verify"
        >验证群管理身份</el-button
      >
      <el-button
        type="primary"
        :disabled="!scope || busy"
        @click="editCategory()"
        >新建类别</el-button
      >
    </div>
    <p v-if="verification">
      在目标群中发送 <code>{{ verification }}</code
      >，再刷新此页。
    </p>
    <p v-if="!scope && !error">
      暂无可管理的群。请在群内执行 /建图库 标识 名称，或验证群管理身份。
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="scope">
      <el-radio-group
        v-model="pending"
        aria-label="图片状态"
        :disabled="busy"
        @change="resetPage"
      >
        <el-radio-button :value="false">已入库</el-radio-button
        ><el-radio-button :value="true">待审核</el-radio-button>
      </el-radio-group>
      <div v-if="!pending" class="library-toolbar">
        <el-select
          v-model="category"
          aria-label="选择类别"
          placeholder="选择类别"
          :disabled="busy"
          @change="resetPage"
        >
          <el-option
            v-for="item in categories"
            :key="item.img_key"
            :label="`${item.id} · ${item.image_count} 张`"
            :value="item.img_key"
          />
        </el-select>
        <el-button
          :disabled="!category || busy"
          @click="
            editCategory(categories.find((item) => item.img_key === category))
          "
          >编辑类别</el-button
        >
        <span v-if="category"
          >上传：<code>/添加来只 group:{{ category }}</code
          >，同条消息附带图片。</span
        >
      </div>
      <p v-if="loading">正在读取图片…</p>
      <p v-else-if="!items.length && !error">
        {{ pending ? "没有待审核图片。" : "这个类别还没有图片。" }}
      </p>
      <div class="library-grid">
        <article
          v-for="item in items"
          :key="`${scope}:${item.img_key ?? category}:${item.name}`"
        >
          <img
            :src="
              api.media(scope, item.img_key ?? category, item.name, pending)
            "
            :alt="item.ocr_text || item.hash_id || item.name"
            loading="lazy"
          />
          <div class="image-details">
            <code>{{ item.hash_id }}</code>
            <template v-if="!pending"
              ><p>{{ item.ocr_text || "暂无描述" }}</p>
              <small
                >点赞 {{ item.likes }} · 评论 {{ item.comments.length }}</small
              ></template
            >
            <p v-else>{{ item.category_id }}</p>
            <div class="image-actions">
              <template v-if="pending"
                ><el-button :disabled="busy" @click="review(item, true)"
                  >通过</el-button
                ><el-button :disabled="busy" @click="review(item, false)"
                  >拒绝</el-button
                ></template
              >
              <template v-else
                ><el-button :disabled="busy" @click="editImage(item)"
                  >编辑</el-button
                ><el-button :disabled="busy" @click="remove(item)"
                  >删除</el-button
                ></template
              >
            </div>
          </div>
        </article>
      </div>
      <el-pagination
        v-if="total > 24"
        v-model:current-page="page"
        :page-size="24"
        :total="total"
        layout="prev, pager, next"
        @current-change="loadImages"
      />
    </template>
    <el-dialog
      :model-value="Boolean(editor)"
      :title="
        editor?.type === 'image'
          ? '编辑图片'
          : editor?.type === 'create'
            ? '新建类别'
            : '编辑类别'
      "
      width="min(540px, 92vw)"
      :before-close="
        (done) => {
          if (!busy) {
            editor = null;
            done();
          }
        }
      "
    >
      <el-form label-position="top" @submit.prevent="save">
        <template v-if="editor?.type === 'image'">
          <el-form-item label="图片描述"
            ><el-input
              v-model="form.description"
              type="textarea"
              :maxlength="512"
              :rows="3"
          /></el-form-item>
          <el-form-item label="评论，每行一条，最多 20 条、每条 32 字"
            ><el-input v-model="form.comments" type="textarea" :rows="4"
          /></el-form-item>
        </template>
        <template v-else>
          <el-form-item v-if="editor?.type === 'create'" label="类别标识"
            ><el-input
              v-model="form.key"
              maxlength="64"
              placeholder="例如 cats"
          /></el-form-item>
          <el-form-item label="名称"
            ><el-input v-model="form.title" maxlength="64"
          /></el-form-item>
          <el-form-item label="别名，每行一个"
            ><el-input v-model="form.aliases" type="textarea" :rows="3"
          /></el-form-item>
        </template>
        <el-button native-type="submit" type="primary" :loading="busy"
          >保存</el-button
        >
      </el-form>
    </el-dialog>
  </div>
</template>

<style scoped>
.group-library {
  display: grid;
  gap: 24px;
}
.group-library h1 {
  margin: 0 0 12px;
}
.library-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.library-toolbar .el-select {
  width: 230px;
}
.library-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 20px;
}
.library-grid article {
  min-width: 0;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  overflow: hidden;
}
.library-grid img {
  display: block;
  width: 100%;
  height: 220px;
  object-fit: contain;
  background: var(--el-fill-color-light);
}
.image-details {
  padding: 16px;
  overflow-wrap: anywhere;
}
.image-details p {
  white-space: pre-wrap;
}
.image-actions {
  display: flex;
  margin-top: 16px;
}
</style>
