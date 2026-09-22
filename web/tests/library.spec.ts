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
/** Private norms are addressable by name, so each test id gets its own. */
const normName = (id: string) => `虚构浏览器验收制度 ${id}`;
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
  await page.getByLabel("资料名称", { exact: true }).fill(normName(id));
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
    page.getByRole("heading", { name: normName(id), exact: true, level: 1 }),
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
/** Owner API calls from the test share the page's cookie session. */
async function ownerHeaders(page: Page, baseURL: string) {
  const session = await (await page.request.get("/api/v1/auth/session")).json();
  return { Origin: baseURL, "X-CSRF-Token": session.csrf_token as string };
}
function draftIdFromUrl(page: Page) {
  const hash = new URL(page.url()).hash;
  expect(hash).toMatch(/^#draft\//);
  return decodeURIComponent(hash.slice("#draft/".length));
}
async function fetchFromSource(page: Page, query: string) {
  await page.goto("/#import");
  await page.getByRole("tab", { name: "从来源获取" }).click();
  await page.getByLabel("法规名称").fill(query);
  await page.getByRole("button", { name: "查找来源候选" }).click();
  await page.getByRole("radio", { name: /虚构来源抓取测试条例/ }).check();
  await page.getByRole("button", { name: "获取并生成预览" }).click();
  await expect(
    page.getByRole("heading", { name: "维护任务", exact: true }),
  ).toBeVisible();
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
  // Long statutes are mounted in chunks; jumping mounts up to the target.
  await expect(page.locator(".reader-toolbar")).toContainText("1260 / 1260 条");
  expect(await page.locator(".article").count()).toBeLessThan(1260);
  await page.getByLabel("定位条号").fill("524");
  await page.getByRole("button", { name: "跳转条文" }).click();
  await expect(page.locator("#clause-523")).toBeInViewport();
  const mounted = await page.locator(".article").count();
  await page.locator(".render-more").scrollIntoViewIfNeeded();
  await expect
    .poll(() => page.locator(".article").count())
    .toBeGreaterThan(mounted);
  await page.getByRole("button", { name: "显示全部条文" }).click();
  await expect(page.locator(".article")).toHaveCount(1260);
  await page
    .getByRole("navigation", { name: "章节目次" })
    .getByRole("button")
    .last()
    .click();
  await expect(page.locator(".article").last()).toBeInViewport();
  await page.getByLabel("筛选正文").fill("第五百二十四条");
  await expect(page.locator(".reader-toolbar")).toContainText("/ 1260 条");
  await expect(page.locator(".article").first()).toContainText("五百二十四");
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
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "取消此预览" }).click();
  await expect(page.getByRole("heading", { name: "导入与核对" })).toBeVisible();
});

test("re-import pre-fills existing metadata and errors keep a way back", async ({
  page,
}) => {
  await login(page);
  await page.goto("/#import/" + encodeURIComponent("norm:browser-norm"));
  await expect(page.getByLabel("资料名称", { exact: true })).toHaveValue(
    normName("browser-norm"),
  );
  await expect(page.getByLabel("私域规范类型")).toHaveValue("other");
  await expect(page.getByLabel("制定主体")).toHaveValue("虚构测试公司");
  await page.goto("/#law/does-not-exist");
  await expect(page.getByRole("alert")).toContainText("资料不存在");
  await page.getByRole("button", { name: "返回公开法规" }).click();
  await expect(page.getByRole("heading", { name: "公开法规" })).toBeVisible();
  await page.goto("/#draft/does-not-exist");
  await page.getByRole("button", { name: "返回导入与核对" }).click();
  await expect(page.getByRole("heading", { name: "导入与核对" })).toBeVisible();
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
  const revoke = page
    .locator(".credentials .list-row")
    .filter({ hasText: "浏览器只读测试" })
    .getByRole("button", { name: "撤销", exact: true });
  page.once("dialog", (dialog) => void dialog.dismiss());
  await revoke.click();
  await expect(page.locator(".credentials")).toContainText("有效");
  page.once("dialog", (dialog) => void dialog.accept());
  await revoke.click();
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

test("tabs follow the WAI-ARIA pattern with keyboard navigation", async ({
  page,
}) => {
  await login(page);
  await page.goto("/#law/" + encodeURIComponent("flk-civil-code-2020"));
  const tabs = page.getByRole("tablist", { name: "资料内容" });
  await expect(tabs.getByRole("tab")).toHaveCount(3);
  const text = tabs.getByRole("tab", { name: "完整正文" });
  await expect(text).toHaveAttribute("aria-selected", "true");
  const panelId = await page.getByRole("tabpanel").getAttribute("id");
  await expect(text).toHaveAttribute("aria-controls", panelId!);
  await expect(page.getByRole("tabpanel")).toHaveAttribute(
    "aria-labelledby",
    (await text.getAttribute("id"))!,
  );
  await expect(tabs.getByRole("tab", { name: "来源与原件" })).toHaveAttribute(
    "tabindex",
    "-1",
  );
  await text.focus();
  await page.keyboard.press("ArrowRight");
  const source = tabs.getByRole("tab", { name: "来源与原件" });
  await expect(source).toHaveAttribute("aria-selected", "true");
  await expect(source).toBeFocused();
  await expect(page.locator(".source-panel")).toBeVisible();
  await page.keyboard.press("End");
  await expect(
    tabs.getByRole("tab", { name: "版本与维护记录" }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".history-grid")).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(text).toHaveAttribute("aria-selected", "true");
  await expect(text).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(
    tabs.getByRole("tab", { name: "版本与维护记录" }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(text).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveCount(1);
  // The re-import action sits beside the list, not inside the tablist.
  await expect(tabs.getByRole("button", { name: "重新导入" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新导入" })).toBeVisible();
});

test("fetch from a source, review the preview and commit", async ({ page }) => {
  await login(page);
  await fetchFromSource(page, "虚构来源抓取测试条例");
  await page
    .locator(".job-card.selected")
    .getByRole("button", { name: "核对并确认" })
    .click();
  await expect(
    page.getByRole("heading", { name: "虚构来源抓取测试条例", level: 1 }),
  ).toBeVisible();
  await expect(page.locator(".article")).toHaveCount(3);
  await expect(page.locator(".articles")).toContainText(
    "来源抓取生成的预览必须经过人工核对后才能入库。",
  );
  await page.getByRole("tab", { name: "来源与原件" }).click();
  await expect(page.locator(".source-panel")).toContainText(
    "https://example.test/synthetic-fetched-law",
  );
  await page.getByRole("tab", { name: "待入库全文" }).click();
  await confirm(page);
  await expect(page.getByText("尚未人工核对", { exact: true })).toBeVisible();
  await page.goto("/#law");
  await page.getByLabel("搜索资料名称").fill("虚构来源抓取");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.locator(".inventory-table")).toContainText(
    "虚构来源抓取测试条例",
  );
});

test("failed jobs explain the cause and can be retried", async ({ page }) => {
  await login(page);
  // The synthetic source fails once for this query, then succeeds.
  await fetchFromSource(page, "重试后成功");
  const failed = page.locator(".job-card.selected");
  await expect(failed).toContainText("失败");
  await expect(failed).toContainText("来源暂时不可用，请稍后重试。");
  await failed.getByText("技术细节").click();
  await expect(failed).toContainText("FetchSourceError");
  await failed.getByRole("button", { name: "重试" }).click();
  const retried = page.locator(".job-card.selected");
  await expect(retried).toContainText("重试任务");
  await expect(retried).toContainText("待确认");
  await expect(
    retried.getByRole("button", { name: "核对并确认" }),
  ).toBeVisible();
  await expect(retried).not.toContainText("失败");
  // A file that cannot be decoded fails deterministically, retry included.
  await page.goto("/#import/norm");
  await page.getByLabel("选择来源文件").setInputFiles({
    name: "国标编码.txt",
    mimeType: "text/plain",
    buffer: Buffer.from([0xb5, 0xda, 0xd2, 0xbb, 0xcc, 0xf5, 0x20, 0xd5, 0xfd]),
  });
  await page.getByLabel("私域规范类型").selectOption("other");
  await page.getByRole("button", { name: "上传并生成预览" }).click();
  const encoding = page.locator(".job-card.selected");
  await expect(encoding).toContainText("原件不是 UTF-8 文本");
  await encoding.getByRole("button", { name: "重试" }).click();
  await expect(page.locator(".job-card.selected")).toContainText("重试任务");
  await expect(page.locator(".job-card.selected")).toContainText(
    "原件不是 UTF-8 文本",
  );
});

test("a preview conflicts once the stored text changes underneath it", async ({
  page,
  baseURL,
}) => {
  await login(page);
  const id = "conflict-norm";
  await importNorm(page, id, "冲突测试的初始正文。");
  await confirm(page);
  await importNorm(page, id, "冲突测试的第二版正文。", true);
  await expect(
    page.getByRole("checkbox", {
      name: "我已核对全文与来源，确认按以上内容入库",
    }),
  ).toBeEnabled();
  // Another writer replaces the document while this preview is open.
  const headers = await ownerHeaders(page, baseURL!);
  const created = await page.request.post("/api/v1/drafts", {
    headers,
    data: {
      kind: "norm",
      payload: {
        id,
        name: normName(id),
        source_type: "other",
        clauses: [{ number: "1", text: "并发写入的正文。" }],
      },
    },
  });
  expect(created.status()).toBe(201);
  const other = await created.json();
  expect(
    (
      await page.request.post(`/api/v1/drafts/${other.id}/commit`, {
        headers,
        data: { fingerprint: other.fingerprint },
      })
    ).status(),
  ).toBe(200);
  await page
    .getByRole("checkbox", { name: "我已核对全文与来源，确认按以上内容入库" })
    .check();
  await page.getByRole("button", { name: "确认入库", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "资料已被其他操作更新，请重新生成预览后确认。",
  );
  await expect(
    page.getByText("资料库中的内容在预览后发生了变化。请重新导入并核对差异。"),
  ).toBeVisible();
  await expect(
    page.getByRole("checkbox", {
      name: "我已核对全文与来源，确认按以上内容入库",
    }),
  ).toBeDisabled();
  await page.goto("/#norm/" + encodeURIComponent(id));
  await expect(page.locator(".articles")).toContainText("并发写入的正文。");
});

test("an expired preview cannot be confirmed but can still be discarded", async ({
  page,
  baseURL,
}) => {
  await login(page);
  await importNorm(page, "expiring-norm", "会过期的预览正文。");
  const id = draftIdFromUrl(page);
  const headers = await ownerHeaders(page, baseURL!);
  expect(
    (
      await page.request.post(`/__test__/drafts/${id}/expire`, { headers })
    ).status(),
  ).toBe(200);
  await page.goto("/#import");
  await expect(
    page.locator(".import-aside").getByText("预览已过期，请重新导入"),
  ).toBeVisible();
  await page.goto("/#draft/" + encodeURIComponent(id));
  await expect(
    page.getByText("预览已过期，请重新取得资料并核对。"),
  ).toBeVisible();
  await expect(
    page.getByRole("checkbox", {
      name: "我已核对全文与来源，确认按以上内容入库",
    }),
  ).toBeDisabled();
  expect(
    (
      await page.request.post(`/api/v1/drafts/${id}/commit`, {
        headers,
        data: { fingerprint: "a".repeat(64) },
      })
    ).status(),
  ).toBe(409);
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "取消此预览" }).click();
  await expect(page.getByRole("heading", { name: "导入与核对" })).toBeVisible();
  await expect(page.locator(".import-aside")).not.toContainText(
    normName("expiring-norm"),
  );
});

test("browser OAuth clients on another origin reach discovery and registration", async ({
  page,
  baseURL,
}) => {
  // Chromium refuses loopback requests from a non-secure opaque origin such
  // as data:, so the foreign page is the same server under another host name.
  // The server rejects that host (421) but the document still has that origin.
  const foreign = new URL(baseURL!);
  foreign.hostname = "localhost";
  await page.goto(foreign.toString(), { waitUntil: "commit" });
  const result = await page.evaluate(async (base) => {
    const metadata = await fetch(
      base + "/.well-known/oauth-authorization-server",
    );
    const registered = await fetch(base + "/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_name: "虚构跨域 OAuth 客户端",
        redirect_uris: ["http://127.0.0.1:43111/callback"],
        token_endpoint_auth_method: "none",
        grant_types: ["authorization_code"],
        response_types: ["code"],
      }),
    });
    let panel = "readable";
    try {
      await fetch(base + "/api/v1/options");
    } catch {
      panel = "blocked";
    }
    return {
      metadata: metadata.status,
      endpoint: ((await metadata.json()) as { token_endpoint: string })
        .token_endpoint,
      registered: registered.status,
      panel,
    };
  }, baseURL!);
  expect(result.metadata).toBe(200);
  expect(result.endpoint).toBe(baseURL + "/token");
  expect(result.registered).toBe(201);
  expect(result.panel).toBe("blocked");
});
