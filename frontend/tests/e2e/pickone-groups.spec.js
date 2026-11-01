import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

test("group library creation, approval and edits stay within the issuing group", async ({
  page,
  request,
}) => {
  const issued = await request.post("/__test__/issue", {
    data: { kind: "group-login" },
  });
  await page.goto((await issued.json()).url);
  await page.getByRole("button", { name: "以 QQ 身份继续" }).click();
  await expect(page.getByText("群管理工作台", { exact: true })).toBeVisible();
  await page.goto("/pickone");
  await expect(page).toHaveURL(/\/pickone\/groups$/);
  await expect(
    page.getByRole("link", { name: "公共图库", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "新建类别", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("类别标识").fill("browser-cats");
  await dialog.getByLabel("名称", { exact: true }).fill("浏览器测试猫");
  await dialog.getByLabel("别名，每行一个").fill("测试猫\n浏览器猫");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByText("待审核", { exact: true }).click();
  await expect(page.locator(".library-grid article")).toHaveCount(1);
  await expect(page.locator(".library-grid img")).toBeVisible();
  await page.getByRole("button", { name: "通过", exact: true }).click();
  await expect(
    page.getByText("没有待审核图片。", { exact: true }),
  ).toBeVisible();
  await page.getByText("已入库", { exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await dialog.getByLabel("图片描述").fill("这是本群专属图片");
  await dialog
    .getByLabel("评论，每行一条，最多 20 条、每条 32 字")
    .fill("本群评论");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByText("这是本群专属图片", { exact: true }),
  ).toBeVisible();
  const directory = fileURLToPath(
    new URL("../../../.codex/ep-screenshots/", import.meta.url),
  );
  await mkdir(directory, { recursive: true });
  await page.screenshot({
    path: directory + "pickone-groups-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    )
    .toBe(true);
  await page.screenshot({
    path: directory + "pickone-groups-mobile.png",
    fullPage: true,
  });
  expect(
    (await page.request.get("/api/plugins/pickone/images/cats")).status(),
  ).toBe(403);
  await request.post("/__test__/group-role", { data: { admin: false } });
  expect(
    (await page.request.get("/api/plugins/pickone/groups/categories")).status(),
  ).toBe(401);
  await request.post("/__test__/group-role", { data: { admin: true } });
});
