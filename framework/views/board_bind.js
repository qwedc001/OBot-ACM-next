"use strict";
const token = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const fields = document.querySelector("#fields"), status = document.querySelector("#status"), password = document.querySelector("#password");
function message(text, kind = "") { status.textContent = text; status.className = kind; }
async function api(action, data = {}) {
  const response = await fetch(`/board/api/bind/${action}`, {method: "POST", headers: {"Content-Type": "application/json"}, credentials: "omit", body: JSON.stringify({token, ...data})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "暂时无法完成操作，请稍后重试。");
  return result;
}
(async () => {
  try {
    const result = await api("inspect");
    document.querySelector("#name").textContent = result.name;
    document.querySelector("#scope").textContent = result.group ? "当前群聊" : "个人配置";
    document.querySelector("#description").textContent = result.group ? "填写信息后，请返回原群，由发起人确认绑定。" : "为你的个人训练榜单配置 HydroOJ。";
    fields.disabled = false;
  } catch (error) { document.querySelector("#description").textContent = "请从 QQ 中的绑定链接进入。"; message(error.message, "error"); }
})();
document.querySelector("#visibility").addEventListener("click", (event) => {
  password.type = password.type === "password" ? "text" : "password";
  event.currentTarget.textContent = password.type === "password" ? "显示" : "隐藏";
  event.currentTarget.setAttribute("aria-label", `${event.currentTarget.textContent}密码`);
});
document.querySelector("#form").addEventListener("submit", async (event) => {
  event.preventDefault(); fields.disabled = true; message("正在验证 HydroOJ 管理员权限，请稍候…");
  try {
    const excluded = document.querySelector("#excluded").value.trim();
    if (excluded && !/^\d+(?:\s*,\s*\d+)*$/.test(excluded)) throw new Error("UID 请用数字填写，多项用英文逗号分隔。");
    const options = {exclude_uid: excluded ? excluded.split(",").map(Number) : [], exclude_before: document.querySelector("#before").value, show_unrated: document.querySelector("#unrated").checked};
    const result = await api("submit", {host: document.querySelector("#host").value, username: document.querySelector("#username").value, password: password.value, options});
    password.value = ""; message(result.message, "success"); document.querySelector("#submit").textContent = "已保存";
  } catch (error) { password.value = ""; message(error.message, "error"); fields.disabled = false; }
});
