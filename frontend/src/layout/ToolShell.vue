<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Moon, Sunny, Monitor, SwitchButton } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api } from "@/api";
import { session } from "@/stores/session";
import { theme } from "@/stores/theme";
import { plugins, currentPlugin } from "@/plugins/registry";
import { permitted } from "@/plugins/registry-core";
import OBotLogo from "@/components/OBotLogo.vue";
const route = useRoute(),
  router = useRouter(),
  version = ref(""),
  serverSlugs = ref(null);
const available = computed(() =>
  permitted(plugins, session.isAdmin.value, session.state.user?.tools).filter(
    (p) =>
      serverSlugs.value === null || serverSlugs.value.includes(p.manifest.slug),
  ),
);
const active = computed(() => currentPlugin(route.path));
const localNavigation = computed(() =>
  [
    ...(active.value?.manifest.nav ?? []),
    ...(session.isAdmin.value ? (active.value?.manifest.adminNav ?? []) : []),
  ].flatMap((item) => item.children ?? [item]),
);
onMounted(async () => {
  try {
    const [meta, registry] = await Promise.all([api.meta(), api.plugins()]);
    version.value = meta.version;
    serverSlugs.value = registry.plugins.map((p) => p.slug);
  } catch (error) {
    ElMessage.error(error.message);
  }
});
function cycleTheme() {
  const modes = ["system", "light", "dark"];
  theme.set(modes[(modes.indexOf(theme.preference.value) + 1) % 3]);
}
async function logout() {
  try {
    await session.logout();
  } catch {}
  await router.push("/login");
}
</script>
<template>
  <header class="shell-top">
    <router-link to="/" class="shell-brand" aria-label="OBot 工具首页"
      ><OBotLogo :size="38" /><span
        ><strong>OBot's Endpoint</strong><small>O宝的AC梦 · WEB</small></span
      ></router-link
    >
    <div class="shell-account">
      <span class="shell-version" v-if="version">v{{ version }}</span
      ><span class="ep-chip">{{ session.state.user?.label ?? "QQ 用户" }}</span
      ><el-button
        circle
        :aria-label="'颜色模式：' + theme.preference.value"
        @click="cycleTheme"
        ><el-icon
          ><Monitor v-if="theme.preference.value === 'system'" /><Moon
            v-else-if="theme.isDark.value" /><Sunny
            v-else /></el-icon></el-button
      ><el-button circle aria-label="退出登录" @click="logout"
        ><el-icon><SwitchButton /></el-icon
      ></el-button>
    </div>
  </header>
  <nav class="shell-nav" aria-label="工具导航">
    <router-link to="/" :class="{ active: route.path === '/' }"
      >工具首页</router-link
    ><router-link
      v-for="plugin in available"
      :key="plugin.manifest.slug"
      :to="plugin.manifest.home"
      :class="{ active: active?.manifest.slug === plugin.manifest.slug }"
      >{{ plugin.manifest.name }}</router-link
    >
  </nav>
  <nav
    v-if="localNavigation.length"
    class="shell-nav shell-subnav"
    aria-label="当前工具导航"
  >
    <router-link
      v-for="item in localNavigation"
      :key="item.path"
      :to="item.path"
      :class="{ active: route.path === item.path }"
      >{{ item.label }}</router-link
    >
  </nav>
  <main><router-view /></main>
  <footer class="shell-footer">
    <span>OBot's ACM</span><span>O宝的AC梦</span>
  </footer>
</template>
