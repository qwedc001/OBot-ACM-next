import { test, expect } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
const artifacts = fileURLToPath(
  new URL("../../../.codex/ep-screenshots/", import.meta.url),
);
async function issue(request, payload = {}) {
  const result = await request.post("/__test__/issue", { data: payload });
  expect(result.ok()).toBeTruthy();
  return (await result.json()).url;
}
async function signIn(page, request, role = "user") {
  const url = await issue(request, { role });
  await page.goto(url);
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("button", { name: "以 QQ 身份继续" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:8765/");
  await expect(page.getByRole("heading", { name: /每一段进步/ })).toBeVisible();
}
async function screenshot(page, name) {
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({ path: artifacts + name + ".png", fullPage: true });
}

test("QQ link clears fragment, establishes HttpOnly session and preserves role after reload", async ({
  page,
  request,
  context,
}) => {
  const url = await issue(request);
  const raw = url.split("#")[1],
    requested = [];
  page.on("request", (request) => requested.push(request.url()));
  await page.goto(url);
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("button", { name: "以 QQ 身份继续" }),
  ).toBeVisible();
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
  await screenshot(page, "login-desktop");
  await page.getByRole("button", { name: "以 QQ 身份继续" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:8765/");
  await expect(page.getByText("个人工作台", { exact: true })).toBeVisible();
  const cookie = (await context.cookies()).find(
    (item) => item.name === "obot_session",
  );
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe("Strict");
  expect(requested.every((url) => !url.includes(raw))).toBe(true);
  expect(
    await page.evaluate(
      () => JSON.stringify(localStorage) + JSON.stringify(sessionStorage),
    ),
  ).not.toContain(raw);
  await screenshot(page, "hub-desktop");
  await page.reload();
  await expect(page.getByText("个人工作台", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});

test("user and admin see the same permissions enforced by the real backend", async ({
  page,
  request,
}) => {
  await signIn(page, request);
  await expect(
    page.getByRole("link", { name: "运行状态", exact: true }),
  ).toHaveCount(0);
  expect((await page.request.get("/api/plugins/runtime")).status()).toBe(403);
  await page.goto("/runtime");
  await expect(page).toHaveURL("http://127.0.0.1:8765/");
  await page.goto("/commands");
  await page
    .getByRole("textbox", { name: "搜索指令" })
    .fill("maintenance.status");
  await expect(page.getByText("没有找到匹配的可用指令。")).toBeVisible();
  await signIn(page, request, "admin");
  await expect(
    page.getByRole("link", { name: "运行状态", exact: true }),
  ).toBeVisible();
  await page.goto("/runtime");
  await expect(page.getByText("尚未连接", { exact: true })).toBeVisible();
  await expect(page.getByText("还没有指令执行记录。")).toBeVisible();
  await screenshot(page, "runtime-desktop");
  await page.goto("/commands");
  await page
    .getByRole("textbox", { name: "搜索指令" })
    .fill("maintenance.status");
  await expect(
    page.getByRole("heading", { name: "/maintenance.status" }),
  ).toBeVisible();
  await screenshot(page, "commands-admin");
});

test("public board binding works on mobile and consumed links cannot be reused", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const url = await issue(request, { kind: "bind" });
  await page.goto(url);
  await expect(page).toHaveURL(/\/board\/bind$/);
  await expect(
    page.getByRole("heading", { name: "绑定 browser-oj" }),
  ).toBeVisible();
  await screenshot(page, "board-bind-mobile");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page
    .getByLabel("HydroOJ 地址", { exact: true })
    .fill("https://oj.example.com");
  await page.getByLabel("管理员名称", { exact: true }).fill("fixture-admin");
  await page.getByLabel("管理员密码", { exact: true }).fill("synthetic");
  await page.getByRole("button", { name: "验证并绑定" }).click();
  await expect(page.getByRole("heading", { name: "配置已保存" })).toBeVisible();
  await expect(page.locator("input[type=password]")).toHaveCount(0);
  await page.goto(url);
  await expect(page.getByRole("alert")).toContainText("已使用");
});

test("personal board settings and real image rendering share the existing service", async ({
  page,
  request,
}) => {
  await signIn(page, request);
  await page.goto("/board");
  await expect(page.getByRole("heading", { name: "demo-oj" })).toBeVisible();
  await page
    .getByRole("switch", { name: "demo-oj 显示打星用户" })
    .locator("..")
    .click();
  await expect
    .poll(
      async () =>
        (
          await (await page.request.get("/api/plugins/board/configs")).json()
        ).configs.find((item) => item.name === "demo-oj").show_unrated,
    )
    .toBe(false);
  await page
    .locator("article")
    .filter({
      has: page.getByRole("heading", { name: "demo-oj", exact: true }),
    })
    .getByRole("button", { name: "今日榜单" })
    .click();
  const image = page.getByRole("img", { name: "训练榜单图片" });
  await expect(image).toBeVisible({ timeout: 50000 });
  expect(
    await image.evaluate((element) => element.naturalWidth),
  ).toBeGreaterThan(1000);
  await screenshot(page, "board-render-desktop");
});

test("dark theme persists and mobile shell has no horizontal overflow", async ({
  page,
  request,
}) => {
  await signIn(page, request, "admin");
  await page.getByRole("button", { name: "颜色模式：system" }).click();
  await page.getByRole("button", { name: "颜色模式：light" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await screenshot(page, "hub-dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, "hub-mobile-dark");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("group admin verification unlocks only the configured group and demotion revokes it", async ({
  page,
  request,
}) => {
  await signIn(page, request);
  await page.goto("/board");
  await page.getByRole("button", { name: "验证管理群" }).click();
  const command = await page.locator(".group-check code").innerText();
  expect(command).toMatch(/^\/dashboard-verify [A-Za-z0-9_-]{24}$/);
  expect(
    (
      await request.post("/__test__/group-role", {
        data: { admin: true, command },
      })
    ).ok(),
  ).toBeTruthy();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("combobox", { name: "配置范围" }).press("ArrowDown");
  await page.getByRole("option", { name: /群聊.*team-oj/ }).click();
  await expect(
    page.getByRole("heading", { name: "team-oj", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "demo-oj", exact: true }),
  ).toHaveCount(0);
  const scopes = (
    await (await page.request.get("/api/plugins/board/scopes")).json()
  ).scopes;
  const selector = scopes.find((item) => item.id !== "personal").id;
  const configs = await (
    await page.request.get("/api/plugins/board/configs?scope=" + selector)
  ).json();
  expect(JSON.stringify(configs)).not.toMatch(
    /credentials|password|fixture-admin|push_target/,
  );
  await page.getByRole("button", { name: "显示设置", exact: true }).click();
  await page.getByLabel("排除 UID", { exact: true }).fill("7, 9");
  await page.getByRole("button", { name: "保存显示设置" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(/排除 2 个 UID/)).toBeVisible();
  await expect(page.locator(".group-check")).toHaveCount(0);
  await page.getByRole("button", { name: "今日榜单", exact: true }).click();
  await expect(page.getByRole("img", { name: "训练榜单图片" })).toBeVisible({
    timeout: 50000,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, "board-group-mobile");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    (
      await request.post("/__test__/group-role", { data: { admin: false } })
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await page.request.get("/api/plugins/board/configs?scope=" + selector)
    ).status(),
  ).toBe(403);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "demo-oj", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "team-oj", exact: true }),
  ).toHaveCount(0);
});
