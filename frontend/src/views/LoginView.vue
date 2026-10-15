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
      <p class="eyebrow">YOUR NEXT ACCEPTED</p>
      <h1>从一声问候，<br />到下一次 <em>Accepted.</em></h1>
      <p>算法竞赛、训练记录，<br />以及更多日常的小乐趣。</p>
      <span class="auth-signature">O宝的AC梦</span>
    </div>
    <section class="ep-card auth-card">
      <span class="ep-chip ep-chip--accent">QQ 身份登录</span>
      <h2>{{ token ? "欢迎回来" : "从 QQ 开始" }}</h2>
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
