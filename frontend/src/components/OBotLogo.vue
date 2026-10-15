<script setup>
/**
 * OBot 的 logo：机器人头像，纯色渐变、无底。
 *
 * 刻意不画外框/背景块 —— 用「实心渐变的头部 + 镂空的眼睛和嘴」出形象，
 * 放在浅底上简洁有力。
 *
 * 眼睛和嘴都是**真的镂空**（眼睛靠 evenodd，嘴走 mask），不是画一块背景色的补丁：
 * 深色主题下头顶的底色是深的，补丁式的嘴会留一条白杠。
 * 渐变与 mask 的 id 带随机后缀，避免同页多个实例互相覆盖。
 */
import { computed } from "vue";

defineProps({
  size: { type: [Number, String], default: 40 },
});

const uid = computed(() => `obotlogo${Math.random().toString(36).slice(2, 8)}`);
const maskId = computed(
  () => `obotmouth${Math.random().toString(36).slice(2, 8)}`,
);
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    class="obot-logo"
    role="img"
    aria-label="OBot"
  >
    <defs>
      <linearGradient
        :id="uid"
        x1="7"
        y1="9"
        x2="41"
        y2="44"
        gradientUnits="userSpaceOnUse"
      >
        <stop offset="0%" stop-color="var(--ep-brand-a)" />
        <stop offset="100%" stop-color="var(--ep-brand-b)" />
      </linearGradient>
      <!-- 白色 = 保留，黑色 = 挖掉；嘴在这里被挖穿 -->
      <mask
        :id="maskId"
        maskUnits="userSpaceOnUse"
        x="0"
        y="0"
        width="48"
        height="48"
      >
        <g fill="#fff">
          <circle cx="24" cy="5.4" r="2.9" />
          <rect x="22.7" y="8.2" width="2.6" height="4.4" rx="1.3" />
          <path
            fill-rule="evenodd"
            clip-rule="evenodd"
            d="M13 13h22a6 6 0 0 1 6 6v14a6 6 0 0 1-6 6H13a6 6 0 0 1-6-6V19a6 6 0 0 1 6-6Zm4.6 8.4a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm12.8 0a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z"
          />
          <rect
            x="18.6"
            y="33.2"
            width="10.8"
            height="2.7"
            rx="1.35"
            fill="#000"
          />
        </g>
      </mask>
    </defs>

    <g :mask="`url(#${maskId})`" :fill="`url(#${uid})`">
      <!-- 天线 -->
      <circle cx="24" cy="5.4" r="2.9" />
      <rect x="22.7" y="8.2" width="2.6" height="4.4" rx="1.3" />

      <!-- 实心头部；眼睛靠 evenodd 镂空 -->
      <path
        fill-rule="evenodd"
        clip-rule="evenodd"
        d="M13 13h22a6 6 0 0 1 6 6v14a6 6 0 0 1-6 6H13a6 6 0 0 1-6-6V19a6 6 0 0 1 6-6Zm4.6 8.4a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm12.8 0a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z"
      />
    </g>
  </svg>
</template>

<style scoped>
.obot-logo {
  display: block;
  flex: none;
}
</style>
