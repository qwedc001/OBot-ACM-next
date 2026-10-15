<script setup>
import { onMounted, onUnmounted, reactive, ref } from "vue";
import { Lock, Check, ArrowLeft } from "@element-plus/icons-vue";
import OBotLogo from "@/components/OBotLogo.vue";
import { consumeCapability } from "@/security/capability";
import { pluginApi } from "../api";
import { bindingOptions } from "../form";
const token = ref(consumeCapability("/board/bind")),
  info = ref(null),
  error = ref(""),
  success = ref(""),
  busy = ref(false),
  loading = ref(true);
const form = reactive({
  host: "",
  username: "",
  password: "",
  excludeUid: "",
  excludeBefore: "",
  showUnrated: true,
});
onMounted(async () => {
  if (!token.value) {
    error.value =
      "请从 QQ 打开完整绑定链接。链接已使用或过期时，请重新发送 /board bind 配置名。";
    loading.value = false;
    return;
  }
  try {
    info.value = await pluginApi.inspect(token.value);
  } catch (failure) {
    error.value = failure.message;
    token.value = null;
  } finally {
    loading.value = false;
  }
});
onUnmounted(() => {
  form.password = "";
  token.value = null;
});
async function submit() {
  if (
    !token.value ||
    busy.value ||
    !form.host ||
    !form.username ||
    !form.password
  )
    return;
  busy.value = true;
  error.value = "";
  try {
    const options = bindingOptions(
      form.excludeUid,
      form.excludeBefore,
      form.showUnrated,
    );
    const result = await pluginApi.submit({
      token: token.value,
      host: form.host,
      username: form.username,
      password: form.password,
      options,
    });
    success.value = result.message;
    token.value = null;
  } catch (failure) {
    error.value = failure.message;
  } finally {
    form.password = "";
    busy.value = false;
  }
}
</script>
<template>
  <main class="bind-page">
    <header class="bind-brand">
      <OBotLogo :size="38" /><span>OBot's Endpoint</span
      ><span class="ep-chip">训练榜单</span>
    </header>
    <section class="bind-layout">
      <aside class="bind-story">
        <p class="eyebrow">CONNECT YOUR TRAINING</p>
        <h1>让每一次提交，<br />都有迹可循。</h1>
        <p>连接你的 HydroOJ，<br />将日常训练汇成一份看得见的进步。</p>
        <div class="bind-assurance">
          <el-icon><Lock /></el-icon
          ><span
            >账号密码加密保存<br /><small
              >链接仅用于本次绑定，不会显示已有密码。</small
            ></span
          >
        </div>
      </aside>
      <section class="ep-card bind-card" v-loading="loading">
        <template v-if="success"
          ><span class="success-icon"
            ><el-icon><Check /></el-icon
          ></span>
          <h2>配置已保存</h2>
          <p role="status" class="bind-result">{{ success }}</p>
          <p class="ep-muted">现在可以回到 QQ，开始查看榜单。</p></template
        ><template v-else
          ><div class="form-heading">
            <span class="eyebrow">HYDRO OJ</span>
            <h2>{{ info ? "绑定 " + info.name : "绑定榜单配置" }}</h2>
            <p class="ep-muted" v-if="info">
              {{
                info.group
                  ? "这是群榜单配置，提交后还需发起人在原群确认。"
                  : "这份配置只与你的个人 QQ 身份关联。"
              }}
            </p>
          </div>
          <el-alert
            v-if="error"
            :title="error"
            type="error"
            :closable="false"
            show-icon
          /><el-form
            v-if="info && token"
            label-position="top"
            @submit.prevent="submit"
            ><el-form-item label="HydroOJ 地址" for="hydro-host" required
              ><el-input
                id="hydro-host"
                v-model="form.host"
                placeholder="https://oj.example.com 或 /d/team 域路径"
                type="url"
                autocomplete="url"
                required
            /></el-form-item>
            <div class="form-two">
              <el-form-item label="管理员名称" for="hydro-user" required
                ><el-input
                  id="hydro-user"
                  v-model="form.username"
                  autocomplete="username"
                  maxlength="128"
                  required /></el-form-item
              ><el-form-item label="管理员密码" for="hydro-password" required
                ><el-input
                  id="hydro-password"
                  v-model="form.password"
                  type="password"
                  autocomplete="current-password"
                  maxlength="1024"
                  required
              /></el-form-item>
            </div>
            <div class="form-divider">
              <span>榜单显示</span><small>可选设置</small>
            </div>
            <el-form-item label="排除 UID" for="exclude-uid"
              ><el-input
                id="exclude-uid"
                v-model="form.excludeUid"
                placeholder="用逗号或空格分隔，例如 2, 3, 7" /></el-form-item
            ><el-form-item label="排除此日期前注册的用户" for="exclude-before"
              ><el-input
                id="exclude-before"
                v-model="form.excludeBefore"
                type="date"
            /></el-form-item>
            <div class="switch-row">
              <div>
                <strong>显示打星用户</strong>
                <p>保留其记录，同时使用星号标识。</p>
              </div>
              <el-switch v-model="form.showUnrated" aria-label="显示打星用户" />
            </div>
            <el-button
              type="primary"
              native-type="submit"
              size="large"
              :loading="busy"
              :disabled="!form.host || !form.username || !form.password"
              class="full-width"
              >{{ busy ? "正在验证账号…" : "验证并绑定" }}</el-button
            >
            <p class="auth-footnote">
              请使用有读取训练记录和运行管理脚本权限的账号。
            </p></el-form
          ></template
        >
      </section>
    </section>
  </main>
</template>
