import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

async function login(page, request, role = "user") {
  const response = await request.post("/__test__/issue", { data: { role } });
  await page.goto((await response.json()).url);
  await page.getByRole("button", { name: "以 QQ 身份继续" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("content pages retain EP navigation, media and submit/review/apply flow", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.text().includes("Failed to resolve component"))
      errors.push(message.text());
  });
  await login(page, request);
  await page.goto("/pickone");
  await page.getByRole("button", { name: /猫猫.*cats/ }).click();
  await expect(page.getByRole("img", { name: /\.gif$/ })).toBeVisible();
  await expect
    .poll(() =>
      page
        .getByRole("img", { name: /\.gif$/ })
        .evaluate((img) => img.naturalWidth),
    )
    .toBeGreaterThan(0);
  await expect(
    page.getByRole("link", { name: "提交审核", exact: true }),
  ).toHaveCount(0);
  // Direct navigation must enforce route-level admin metadata as well as API permissions.
  await page.goto("/pickone/review/submissions");
  await expect(page).toHaveURL(/\/$/);
  await page.goto("/contestlist");
  await page.getByRole("button", { name: "新增比赛", exact: true }).click();
  await page.getByPlaceholder(/如 ICPC/).fill("ICPC");
  await page.getByPlaceholder("如 济南区域赛").fill("Browser regional");
  await page.getByPlaceholder(/如 第50届/).fill("Browser regional contest");
  await page.getByPlaceholder("选择开始时间").fill("2027-01-01 12:00");
  await page.getByPlaceholder("选择开始时间").press("Enter");
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/contestlist/submissions") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "提交审核", exact: true }).click();
  expect((await created).status()).toBe(201);
  await page.getByRole("link", { name: "我的提交", exact: true }).click();
  await expect(page.getByText(/Browser regional/).first()).toBeVisible();

  await login(page, request, "admin");
  await page.goto("/contestlist/review");
  await page.getByRole("button", { name: "通过", exact: true }).first().click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /确认通过/ })
    .click();
  await page.getByRole("button", { name: /一键下发/ }).click();
  await expect(page.getByRole("dialog", { name: "应用预览" })).toBeVisible();
  await page.getByRole("button", { name: "确认下发", exact: true }).click();
  await page
    .getByRole("dialog", { name: "一键下发" })
    .getByRole("button", { name: /确定|确认/ })
    .click();
  await expect(page.getByRole("dialog", { name: "应用预览" })).toHaveCount(0);

  const folder = fileURLToPath(
    new URL("../../../.codex/ep-screenshots/", import.meta.url),
  );
  await mkdir(folder, { recursive: true });
  for (const [path, name] of [
    ["/contestlist", "contestlist"],
    ["/pickone", "pickone"],
    ["/pickone/review/images", "audit"],
    ["/curation", "content-logs"],
  ]) {
    await page.goto(path);
    await expect(page.locator("h1")).toBeVisible();
    await page.screenshot({ path: `${folder}${name}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/pickone");
  await expect(page.getByRole("button", { name: /猫猫.*cats/ })).toBeVisible();
  await page.screenshot({
    path: `${folder}pickone-mobile.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: /颜色模式/ }).click();
  await page.getByRole("button", { name: /颜色模式/ }).click();
  await page.screenshot({
    path: `${folder}pickone-dark-mobile.png`,
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
