import { computed, ref, watchEffect } from "vue";

/**
 * 明暗主题。
 *
 * `preference` 是**用户的选择**：`'system'`（跟随系统）/ `'light'` / `'dark'`。
 * 真正挂到 `<html>` 上的 `dark` 类由 `isDark` 决定 —— 跟随系统时它还会随系统实时变化。
 *
 * 初始值不在这里读 localStorage：`web/index.html` 里那段内联脚本已经在首帧之前
 * 把偏好写进 `<html data-theme>` 并挂好了 `dark` 类（否则深色用户会先闪一帧浅色）。
 */
const STORAGE_KEY = "obot-ep.theme";
const MODES = ["system", "light", "dark"];

const root = document.documentElement;
const media = window.matchMedia
  ? window.matchMedia("(prefers-color-scheme: dark)")
  : null;

const preference = ref(
  MODES.includes(root.dataset.theme) ? root.dataset.theme : "system",
);
const systemDark = ref(media?.matches ?? false);

media?.addEventListener("change", (event) => {
  systemDark.value = event.matches;
});

const isDark = computed(() =>
  preference.value === "system"
    ? systemDark.value
    : preference.value === "dark",
);

watchEffect(() => {
  root.classList.toggle("dark", isDark.value);
});

export const theme = {
  preference,
  isDark,
  /** 当前系统是不是深色（UI 里用来解释「跟随系统」跟到了什么） */
  systemDark,

  set(value) {
    if (!MODES.includes(value)) return;
    preference.value = value;
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      /* 存不下就只在本次会话里生效 */
    }
  },
};
