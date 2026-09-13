import { test, expect, type Page } from "@playwright/test";
import { createHash, randomBytes } from "node:crypto";

const longText =
  "本条为虚构的浏览器验收材料，用于核对完整内容与来源，不代表任何真实制度。".repeat(
    12,
  );
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("所有者密码").fill("Synthetic-browser-owner-123");
  await page.getByRole("button", { name: "登录资料库" }).click();
  await expect(page.getByRole("heading", { name: "资料库概览" })).toBeVisible();
}
async function importNorm(
  page: Page,
  id: string,
  text: string,
  replacing = false,
) {
  await page.goto(
    "/#import/" + encodeURIComponent(replacing ? "norm:" + id : "norm"),
  );
  await page.getByLabel("选择来源文件").setInputFiles({
    name: "虚构验收制度.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "第一章 核对测试\n第一条 " + text + "\n第二条 这是一条结尾核对条款。",
    ),
  });
  await page.getByLabel("资料名称", { exact: true }).fill("虚构浏览器验收制度");
  if (!replacing) await page.getByLabel("资料标识", { exact: true }).fill(id);
  await page.getByLabel("私域规范类型").selectOption("other");
  await page.getByLabel("制定主体").fill("虚构测试公司");
  await page.getByLabel("约束范围").fill("仅用于自动化测试");
  await page.getByRole("button", { name: "上传并生成预览" }).click();
  await expect(
    page.getByRole("heading", { name: "维护任务", exact: true }),
  ).toBeVisible();
  await page
    .locator(".job-card.selected")
    .getByRole("button", { name: "核对并确认" })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "虚构浏览器验收制度",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await expect(page.locator(".articles")).toContainText(text);
}
async function confirm(page: Page) {
  await page
    .getByRole("checkbox", { name: "我已核对全文与来源，确认按以上内容入库" })
    .check();
  await page.getByRole("button", { name: "确认入库", exact: true }).click();
  await expect(page.getByRole("tab", { name: "完整正文" })).toBeVisible();
}

test("browse public fixtures, full civil-code text, source and history", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await expect(page.locator(".stat-card").first()).toContainText("49");
  await page.screenshot({
    path: testInfo.outputPath("overview.png"),
    fullPage: true,
  });
  await page
    .getByRole("navigation", { name: "主要导航" })
    .getByRole("button", { name: "公开法规", exact: true })
    .click();
  await page.getByLabel("搜索资料名称").fill("民法典");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("button", { name: "中华人民共和国民法典", exact: true })
    .click();
  await expect(page.locator(".article")).toHaveCount(1260);
  await page.getByLabel("定位条号").fill("524");
  await page.getByRole("button", { name: "跳转条文" }).click();
  await expect(page.locator("#clause-523")).toBeInViewport();
  await page.getByRole("tab", { name: "来源与原件" }).click();
  await expect(page.locator(".source-panel")).toContainText("来源地址");
  await expect(page.locator(".source-panel a").first()).toHaveAttribute(
    "href",
    /^https?:/,
  );
  await page.getByRole("tab", { name: "版本与维护记录" }).click();
  await page.getByRole("button", { name: "查看全文" }).first().click();
  await expect(page.getByText("历史快照", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("preview complete private text, compare, commit, review and reimport", async ({
  page,
}, testInfo) => {
  await login(page);
  const id = "browser-norm";
  await importNorm(page, id, longText);
  await expect(
    page.getByRole("button", { name: "确认入库", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "来源与原件" }).click();
  await page.getByRole("button", { name: "查看原文文本" }).click();
  await expect(page.locator(".source-text")).toContainText(longText);
  await page.getByRole("tab", { name: "完整差异" }).click();
  await expect(page.locator(".diff-panel")).toContainText(longText);
  await confirm(page);
  await page.getByLabel("人工核对备注").fill("已比对完整原件。");
  await page.getByRole("button", { name: "标记已核对" }).click();
  await expect(page.getByText("已人工核对", { exact: true })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("document.png"),
    fullPage: true,
  });
  await importNorm(page, id, longText + "第二次导入的新内容。", true);
  await page.getByRole("tab", { name: "完整差异" }).click();
  await expect(page.locator(".diff-after")).toContainText(
    "第二次导入的新内容。",
  );
  await confirm(page);
  await expect(page.getByText("尚未人工核对", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "版本与维护记录" }).click();
  await expect(page.getByRole("button", { name: "查看全文" })).toHaveCount(2);
  await page
    .getByRole("button", { name: "预览恢复操作前内容" })
    .first()
    .click();
  await expect(page.getByRole("tab", { name: "待入库全文" })).toBeVisible();
  await expect(page.locator(".articles")).not.toContainText(
    "第二次导入的新内容。",
  );
  await page.getByRole("button", { name: "取消此预览" }).click();
});

test("issue and revoke a public token without exposing private records", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "连接与备份", exact: true }).click();
  await page.getByLabel("连接名称").fill("浏览器只读测试");
  await expect(
    page.getByRole("checkbox", { name: "同时允许读取私域规范" }),
  ).not.toBeChecked();
  await page.getByRole("button", { name: "生成只读令牌" }).click();
  const token = await page.getByLabel("新生成的只读令牌").inputValue();
  const headers = { Authorization: "Bearer " + token };
  expect(
    (await page.request.get("/api/v1/documents", { headers })).status(),
  ).toBe(200);
  expect(
    (
      await page.request.get("/api/v1/documents?kind=norm", { headers })
    ).status(),
  ).toBe(403);
  expect((await page.request.get("/api/v1/system", { headers })).status()).toBe(
    403,
  );
  await page
    .locator(".credentials .list-row")
    .filter({ hasText: "浏览器只读测试" })
    .getByRole("button", { name: "撤销", exact: true })
    .click();
  await expect(page.locator(".credentials")).toContainText("已撤销");
  expect(
    (await page.request.get("/api/v1/documents", { headers })).status(),
  ).toBe(401);
});

test("download, validate and restore portable backup in browser", async ({
  page,
}, testInfo) => {
  await login(page);
  await page.getByRole("button", { name: "连接与备份", exact: true }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载备份", exact: true }).click();
  const download = await downloadPromise;
  const path = testInfo.outputPath("library.zip");
  await download.saveAs(path);
  await page.getByLabel("选择资料库备份").setInputFiles(path);
  await page.getByRole("button", { name: "上传并校验备份" }).click();
  await expect(
    page.getByRole("heading", { name: "恢复范围预览" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认恢复资料库" }),
  ).toBeDisabled();
  await page
    .getByRole("checkbox", {
      name: "我已核对替换范围，并保存需要保留的当前资料",
    })
    .check();
  await page.getByRole("button", { name: "确认恢复资料库" }).click();
  await expect(page.getByRole("heading", { name: "资料库概览" })).toBeVisible();
  await expect(page.locator(".stat-card").first()).toContainText("49");
});

test("mobile navigation and import remain within viewport", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.screenshot({
    path: testInfo.outputPath("mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "导入与核对", exact: true }).click();
  await expect(page.getByRole("heading", { name: "导入与核对" })).toBeVisible();
  await expect(page.getByLabel("选择来源文件")).toBeAttached();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("owner consent completes OAuth PKCE without granting private scope", async ({
  page,
  baseURL,
}) => {
  await login(page);
  const callback = baseURL + "/synthetic-callback";
  const registered = await page.request.post("/register", {
    data: {
      client_name: "虚构浏览器 OAuth 客户端",
      redirect_uris: [callback],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      scope: "chinalaw:public:read chinalaw:private:read",
    },
  });
  expect(registered.status()).toBe(201);
  const client = await registered.json();
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const parameters = new URLSearchParams({
    client_id: client.client_id,
    redirect_uri: callback,
    response_type: "code",
    scope: "chinalaw:public:read chinalaw:private:read",
    state: "browser-state",
    code_challenge: challenge,
    code_challenge_method: "S256",
    resource: baseURL + "/mcp",
  });
  await page.goto("/authorize?" + parameters);
  await expect(
    page.getByRole("heading", { name: "授权应用读取资料" }),
  ).toBeVisible();
  await expect(
    page.getByRole("checkbox", { name: "同时允许此应用读取私域规范" }),
  ).not.toBeChecked();
  await page.getByRole("button", { name: "允许只读访问" }).click();
  await page.waitForURL((url) => url.pathname === "/synthetic-callback");
  const responseURL = new URL(page.url());
  expect(responseURL.searchParams.get("state")).toBe("browser-state");
  const exchanged = await page.request.post("/token", {
    form: {
      grant_type: "authorization_code",
      client_id: client.client_id,
      code: responseURL.searchParams.get("code")!,
      redirect_uri: callback,
      code_verifier: verifier,
      resource: baseURL + "/mcp",
    },
  });
  expect(exchanged.status()).toBe(200);
  const token = await exchanged.json();
  expect(token.scope).toBe("chinalaw:public:read");
  expect(
    (
      await page.request.get("/api/v1/documents?kind=norm", {
        headers: { Authorization: "Bearer " + token.access_token },
      })
    ).status(),
  ).toBe(403);
});

test("expired session returns to login and removes displayed library data", async ({
  page,
}) => {
  await login(page);
  await page.context().clearCookies();
  await page
    .getByRole("navigation", { name: "主要导航" })
    .getByRole("button", { name: "公开法规", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "打开你的资料库" }),
  ).toBeVisible();
  await expect(
    page.getByText("登录会话已失效，请重新登录。", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".inventory-table")).toHaveCount(0);
});
