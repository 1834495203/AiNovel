/* All writes are intercepted. No user library or model is accessed. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("custom field templates save definitions, retain typed values and apply to multiple chapters", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000", viewport: { width: 1280, height: 900 } });
    const storyId = randomUUID();
    const story = { id: storyId, title: "字段合成测例", world: { premise: "海港", entries: [] }, preferences: { entries: [], likes: [], dislikes: [], custom_fields: [] },
      writer: { entries: [], mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: "", current_user_intent: "", custom_fields: [] },
      characters: [], chapters: [{ id: randomUUID(), title: "第一章", summary: "", custom_fields: [] }, { id: randomUUID(), title: "第二章", summary: "", custom_fields: [] }], scenes: [], directions: [], initial_snapshots: [], custom_fields: [] };
    let revision = randomUUID();
    const library = { session_id: randomUUID(), templates: [] };
    const writes = [], errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    page.on("requestfailed", request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    const fields = target => target.scope === "world" ? story.world.entries : story.chapters.find(item => item.id === target.entity_id).custom_fields;
    const instantiate = definition => ({ id: randomUUID(), name: definition.name, definition, value: null, description: "", reason: "", children: [] });
    const json = (route, value) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
    await page.route("**/health", route => json(route, { database: true, redis: true, worker: true, provider: "mock", model: "mock" }));
    await page.route("**/api/**", async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      if (request.method() === "GET") {
        if (path === "/api/stories") return json(route, [{ id: storyId, title: story.title, revision_id: revision, scene_count: 0 }]);
        if (path === "/api/drafts") return json(route, []);
        if (path === "/api/field-templates") return json(route, library);
        if (path === `/api/stories/${storyId}`) return json(route, { revision_id: revision, story, characters_at_scenes: [] });
        if (path === `/api/stories/${storyId}/fields`) {
          const target = { scope: url.searchParams.get("scope"), entity_id: url.searchParams.get("entity_id") };
          return json(route, { revision_id: revision, target, entries: fields(target) });
        }
        if (path.endsWith("/field-history")) return json(route, []);
        return json(route, []);
      }
      assert.equal(path, "/api/commands");
      const submission = request.postDataJSON(), payload = submission.payload;
      writes.push(payload);
      if (payload.kind === "save_field_template") library.templates.push(payload.template);
      else if (payload.kind === "apply_field_template") {
        const template = library.templates.find(item => item.id === payload.template_id);
        for (const target of payload.targets) fields(target).push(...template.fields.map(instantiate));
        revision = randomUUID();
      } else if (payload.kind === "edit_fields") {
        const values = fields(payload.target);
        for (const entry of payload.patch.upsert || []) {
          const index = values.findIndex(item => item.id === entry.id);
          if (index < 0) values.push(entry); else values[index] = entry;
        }
        revision = randomUUID();
      } else throw new Error(`Unexpected write: ${payload.kind}`);
      return json(route, { submission, status: "completed", output: { message: "已保存", revision_id: revision }, undone: false });
    });
    const prefix = process.env.AINOVEL_TEST_UI_PREFIX || "/";
    await page.goto(`${prefix}#story/${storyId}`);
    await page.getByRole("button", { name: "故事资料", exact: true }).click();
    try { await page.locator(".custom-fields").waitFor({ timeout: 5000 }); }
    catch (error) { throw new Error(`${error.message}\nPage: ${await page.locator('body').innerText()}\nErrors: ${errors.join('; ')}`); }
    await page.getByRole("button", { name: "新建字段模板", exact: true }).click();
    await page.getByRole("button", { name: "添加字段", exact: true }).click();
    await page.getByPlaceholder("字段名称").fill("潮汐周期");
    await page.getByLabel("字段类型", { exact: true }).click();
    await page.locator(".n-base-select-option").filter({ hasText: "数字" }).click();
    await page.getByPlaceholder("字段说明（可选）：说明含义或填写要求").fill("以小时为单位");
    await page.getByRole("button", { name: "保存模板", exact: true }).click();
    await page.getByRole("button", { name: "应用模板", exact: true }).click();
    const entry = page.locator(".entry-node").filter({ has: page.locator("strong", { hasText: "潮汐周期" }) });
    await entry.getByRole("button", { name: "编辑", exact: true }).click();
    await page.getByPlaceholder("数字", { exact: true }).fill("7");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText("正在保存…", { exact: true }).waitFor({ state: "hidden" });
    assert.equal(story.world.entries[0].value, 7);
    assert.equal(library.templates[0].fields[0].type, "number");
    assert.equal(library.templates[0].fields[0].description, "以小时为单位");
    assert.equal(library.templates[0].fields[0].value, undefined);
    await page.locator(".custom-fields > .toolbar .n-select").first().click();
    await page.locator(".n-base-select-option").filter({ hasText: /^章节$/ }).click();
    await page.getByRole("button", { name: "新建字段模板", exact: true }).click();
    await page.getByRole("button", { name: "添加字段", exact: true }).click();
    await page.getByPlaceholder("字段名称").fill("章节冲突");
    await page.getByRole("button", { name: "保存模板", exact: true }).click();
    await page.locator(".template-bar > .n-select").click();
    await page.locator(".n-base-select-option").filter({ hasText: /^第一章$/ }).click();
    await page.locator(".n-base-select-option").filter({ hasText: /^第二章$/ }).click();
    await page.getByRole("heading", { name: "字段模板", exact: true }).click();
    await page.getByRole("button", { name: "应用模板", exact: true }).click();
    await page.getByText("正在保存…", { exact: true }).waitFor({ state: "hidden" });
    assert.deepEqual(writes.at(-1).targets.map(item => item.entity_id), story.chapters.map(item => item.id));
    assert.ok(story.chapters.every(item => item.custom_fields.length === 1));
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
