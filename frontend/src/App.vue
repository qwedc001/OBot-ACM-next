<script setup>
import { computed } from "vue";
import { capabilityRevision } from "@/security/capability";
import { useRoute } from "vue-router";
import ToolShell from "@/layout/ToolShell.vue";
const route = useRoute();
const publicKey = computed(() => `${route.path}:${capabilityRevision.value}`);
const ready = computed(() => route.matched.length > 0);
</script>
<template>
  <ToolShell v-if="ready && !route.meta.public" /><router-view
    v-else-if="ready"
    :key="publicKey"
  />
</template>
