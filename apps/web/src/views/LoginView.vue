<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { ArrowRight, CopyDocument } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import OBotLogo from "@/components/OBotLogo.vue";
import { consumeCapability } from "@/security/capability";
import { session } from "@/stores/session";
const token = ref(consumeCapability("/login")),
  busy = ref(false),
  error = ref(""),
  router = useRouter();
async function login() {
  if (!token.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await session.login(token.value);
    token.value = null;
    await router.replace("/");
  } catch (failure) {
    error.value = failure.message;
    token.value = null;
  } finally {
    busy.value = false;
  }
}
async function copy() {
  try {
    await navigator.clipboard.writeText("/dashboard");
    ElMessage.success("已复制 /dashboard");
  } catch {
    ElMessage.info("请在私聊中发送 /dashboard");
  }
}
</script>
<template>
  <main class="auth-page">
    <div class="auth-story">
      <router-link to="/" class="auth-brand"
        ><OBotLogo :size="44" /><span>OBot's Endpoint</span></router-link
      >
      <h1>OBot 工作台</h1>
      <p>查看训练榜单，管理 OJ 配置。<br />使用 QQ 机器人发来的链接登录。</p>
    </div>
    <section class="ep-card auth-card">
      <h2>{{ token ? "确认登录" : "获取登录链接" }}</h2>
      <p class="ep-muted">
        {{
          token
            ? "使用机器人发给你的一次性链接，继续访问个人工作台。"
            : "私聊机器人发送下面的指令，打开收到的登录链接即可。"
        }}
      </p>
      <div v-if="!token" class="command-copy">
        <code>/dashboard</code
        ><el-button text aria-label="复制登录指令" @click="copy"
          ><el-icon><CopyDocument /></el-icon
        ></el-button>
      </div>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      /><el-button
        v-if="token"
        type="primary"
        size="large"
        :loading="busy"
        class="full-width"
        @click="login"
        >以 QQ 身份继续 <el-icon><ArrowRight /></el-icon
      ></el-button>
      <p class="auth-footnote">登录链接仅可使用一次，请勿转发给他人。</p>
    </section>
  </main>
</template>
