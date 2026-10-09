/* Synthetic browser contract for complete Jinja templates. API reads/writes stay in-memory. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue Jinja editor previews rendered source and binds task versions independently", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const storyId = randomUUID(), revisionId = randomUUID(), globalScopeId = randomUUID();
    const taskNames = ["planning_decision", "planning", "revision_plan", "character_consultation", "rewrite_selection", "writing", "judge", "extraction", "maintenance", "creation_interview", "rewrite_snippet", "scene_summary", "character_profile", "character_chat", "discussion"];
    const ids = Object.fromEntries(taskNames.flatMap(key => [[`${key}Global`, randomUUID()], [`${key}Story`, randomUUID()]]));
    const writes = [], pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    const sourceFor = task => `System role for ${task}\n{% if instruction %}Instruction: {{ instruction }}{% endif %}`;
    const version = (id, scope, scopeId, task, name, versionNumber, source, builtin = false) => ({
      id, scope, scope_id: scopeId, task, name, version: versionNumber, guidance: "", source_format: "jinja2", source,
      editor_source: source, template_path: `templates/${task}.j2`, source_hash: `hash-${task}-${versionNumber}`,
      context_variables: ["instruction"], context_schema: [{ name: "instruction", type: "string", description: "本次用户要求" }], builtin,
    });
    const makeLibrary = () => ({ tasks: taskNames.map(task => {
      const global = version(ids[`${task}Global`], "global", globalScopeId, task, `${task} 默认`, 1, sourceFor(task), true);
      const story = version(ids[`${task}Story`], "story", storyId, task, `${task} 作品`, 2, sourceFor(task));
      return { task, label: `任务 ${task}`, description: `完整的${task}系统角色和任务说明。`, template_path: global.template_path,
        context_variables: ["instruction"], context_schema: global.context_schema, editor_source: global.editor_source,
        global_active: global, story_active: story, active: story, versions: [global, story] };
    }) });
    let library = makeLibrary(), undoAvailable = false, undoOperationId = 50;
    const story = { id: storyId, title: "合成模板测例", world: { premise: "合成世界", entries: [] }, preferences: { likes: [], dislikes: [], custom_fields: [] },
      writer: { mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: "", current_user_intent: "", custom_fields: [] },
      characters: [], chapters: [], scenes: [], directions: [], initial_snapshots: [], custom_fields: [] };
    const view = { revision_id: revisionId, story, characters_at_scenes: [] };
    const json = (route, value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => json(route, { database: true, redis: true, worker: true, provider: "mock", model: "fixture" }));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === "GET") {
        if (path === "/api/stories") return json(route, [{ id: storyId, title: story.title, revision_id: revisionId, scene_count: 0 }]);
        if (path === "/api/drafts") return json(route, []);
        if (path === `/api/stories/${storyId}`) return json(route, view);
        if (path === `/api/stories/${storyId}/commands`) return json(route, []);
        if (path === `/api/undo/${storyId}`) return json(route, { available: false, command_ids: [], message: "" });
        if (path === `/api/undo/${globalScopeId}`) return json(route, { available: undoAvailable, operation_id: undoOperationId, message: "" });
        if (path === "/api/prompt-templates") return json(route, library);
        if (path === "/api/prompt-templates/preview") return json(route, library);
        writes.push({ method: request.method(), path, unexpected: true });
        return json(route, { detail: `未配置的合成 GET: ${path}` }, 404);
      }
      const body = request.postDataJSON();
      writes.push({ method: request.method(), path, body });
      if (path === "/api/prompt-templates/preview") {
        const rendered = body.source.replaceAll("{{ instruction }}", body.context.instruction || "").replaceAll("{% if instruction %}", body.context.instruction ? "" : "{% if instruction %}").replaceAll("{% endif %}", body.context.instruction ? "" : "{% endif %}");
        return json(route, { strategy_preview: "", note: "仅使用填写的上下文；运行时资料见运行步骤。", rendered_prompt: rendered,
          rendered_messages: [{ role: "system", content: rendered }], template_path: `templates/${body.task}.j2`, source_hash: "preview-hash", context_schema: [{ name: "instruction", type: "string", description: "本次用户要求" }], context_variables: ["instruction"] });
      }
      if (path !== "/api/commands") return json(route, { detail: `未配置的合成写入: ${path}` }, 404);
      const payload = body.payload || {};
      if (payload.kind === "save_prompt_template") {
        const row = library.tasks.find(item => item.task === payload.task), scopeId = payload.scope === "global" ? globalScopeId : storyId;
        const previous = payload.scope === "global" ? row.global_active : row.story_active;
        const next = version(randomUUID(), payload.scope, scopeId, payload.task, payload.name, previous.version + 1,
          payload.source || sourceFor(payload.task));
        row.versions.push(next);
        if (payload.scope === "global") row.global_active = next; else row.story_active = next;
        row.active = row.story_active;
        if (payload.scope === "global") { undoAvailable = true; undoOperationId += 1; }
      } else if (payload.kind === "undo") undoAvailable = false;
      return json(route, { submission: body, status: "completed", output: { message: "合成命令完成", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null }, undone: false });
    });
    await page.addInitScript(({ persistedStoryId, judgeId }) => localStorage.setItem(`ainovel.promptSelection.story.${persistedStoryId}`, JSON.stringify([{ task: "judge", version_id: judgeId }])), { persistedStoryId: storyId, judgeId: ids.judgeStory });
    await page.goto(`/#story/${storyId}`);
    await page.locator(".novel-pane").waitFor();
    await page.getByRole("button", { name: "提示词设置", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "提示词模板" });
    await dialog.waitFor();
    assert.match(await dialog.textContent(), /完整的 Jinja/);
    assert.equal(await dialog.locator(".template-version-row").count(), 2);
    const sourceInput = dialog.getByLabel("Jinja 源码");
    await sourceInput.fill("System: {{ instruction }}");
    await dialog.getByText("预览输入（默认留空）").click();
    await dialog.getByLabel("instruction", { exact: true }).fill("合成预览上下文");
    await dialog.getByRole("button", { name: "渲染预览" }).click();
    const preview = dialog.locator(".rendered-preview");
    await preview.waitFor();
    assert.match(await preview.textContent(), /合成预览上下文/);
    const previewRequest = writes.find(item => item.path === "/api/prompt-templates/preview");
    assert.equal(previewRequest.body.source, "System: {{ instruction }}");
    assert.equal(previewRequest.body.context.instruction, "合成预览上下文");

    await dialog.getByLabel("任务").click();
    await page.getByText("任务 planning", { exact: true }).click();
    await dialog.getByRole("button", { name: /planning 作品/ }).click();
    await dialog.getByRole("button", { name: "本轮采用此版本" }).click();
    await dialog.getByLabel("任务").click();
    await page.getByText("任务 writing", { exact: true }).click();
    await dialog.getByRole("button", { name: /writing 作品/ }).click();
    await dialog.getByRole("button", { name: "本轮采用此版本" }).click();
    const selected = JSON.parse(await page.evaluate(key => localStorage.getItem(key), `ainovel.promptSelection.story.${storyId}`));
    assert.deepEqual(Object.fromEntries(selected.map(item => [item.task, item.version_id])), {
      planning: ids.planningStory, writing: ids.writingStory, judge: ids.judgeStory,
    });

    await dialog.getByLabel("任务").click();
    await page.getByText("任务 planning", { exact: true }).click();
    await dialog.getByLabel("模板范围").click();
    await page.getByText("所有作品默认", { exact: true }).click();
    await dialog.getByLabel("版本名称").fill("全局 planning Jinja v2");
    await sourceInput.fill("ROLE: {{ instruction }}\nDo the planning task.");
    await dialog.getByRole("button", { name: "保存新版本" }).click();
    await page.waitForTimeout(250);
    const currentAfterSave = await dialog.locator(".template-current").textContent();
    const saveError = await dialog.locator(".template-alert").count() ? await dialog.locator(".template-alert").textContent() : "无错误提示";
    assert.match(currentAfterSave || "", /全局 planning Jinja v2/, `保存界面未刷新；当前=${currentAfterSave}; 错误=${saveError}; 命令=${JSON.stringify(writes.filter(item => item.path === "/api/commands"))}`);
    const save = writes.find(item => item.path === "/api/commands" && item.body.payload?.kind === "save_prompt_template");
    assert.equal(save.body.session_id, globalScopeId);
    assert.equal(save.body.payload.scope, "global");
    assert.equal(save.body.payload.source, "ROLE: {{ instruction }}\nDo the planning task.");
    assert.equal(save.body.payload.guidance, "");
    assert.equal(await dialog.getByRole("button", { name: /撤回/ }).count(), 0);
    assert.ok(await dialog.getByRole("button", { name: /运行历史/ }).count());
    assert.deepEqual(pageErrors, []);
    assert.ok(writes.every(item => !item.unexpected), JSON.stringify(writes.filter(item => item.unexpected)));
  } finally { await browser.close(); }
});
