/* Synthetic Workbench browser contract. All API traffic is served from fixtures. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue Workbench edits profile approvals and creates stale-draft retries from the latest head", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000",
      viewport: { width: Number(process.env.AINOVEL_TEST_WIDTH || 1440), height: Number(process.env.AINOVEL_TEST_HEIGHT || 1000) } });
    const storyId = randomUUID(), beforeRevisionId = randomUUID(), latestRevisionId = randomUUID();
    const generationId = randomUUID(), approvalCommandId = randomUUID(), staleGenerationId = randomUUID(), characterId = randomUUID();
    const pageErrors = [], submissions = [];
    page.on("pageerror", error => pageErrors.push(error.message));

    const profileEntry = { id: randomUUID(), name: "来历", value: null, description: "来自旧档案的初始描述", reason: "合成审批测试", children: [] };
    const proposedCharacter = { name: "沈遥", aliases: [], profile: { entries: [profileEntry] }, custom_fields: [] };
    const character = { id: characterId, name: proposedCharacter.name, introduced_scene_id: null, aliases: [],
      profile: proposedCharacter.profile, custom_fields: [] };
    const chapterId = randomUUID(), sceneId = randomUUID();
    const story = {
      id: storyId, title: "合成工作台测例", world: { premise: "合成世界设定", entries: [] },
      preferences: { likes: [], dislikes: [], custom_fields: [] },
      writer: { mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: "", current_user_intent: "", custom_fields: [] },
      characters: [], chapters: [{ id: chapterId, title: "第一章·雨夜来信" }],
      scenes: [{ id: sceneId, chapter_id: chapterId, title: "没有寄出的信", text: "雨水沿着窗沿滑落。林初把那封没有寄出的信放回抽屉，灯光照亮纸面上最后一行字。", source_command_id: generationId }],
      directions: [], initial_snapshots: [], custom_fields: [],
    };
    let currentView = { revision_id: beforeRevisionId, story, characters_at_scenes: [] };
    const rootCommand = { submission: { id: generationId, actor: "user", story_id: storyId, base_revision_id: beforeRevisionId,
      session_id: null, payload: { kind: "generate", instruction: "原始写作指令" } }, status: "waiting", undone: false, error: null,
      output: { story_id: storyId, message: "等待新角色档案审批", need: null, approval_phase: null, character: null, writing_draft: null } };
    const approvalCommand = { submission: { id: approvalCommandId, actor: "user", story_id: storyId, base_revision_id: beforeRevisionId,
      session_id: null, payload: { kind: "resolve_character", generation_command_id: generationId, decision: "consent" } },
      status: "waiting", undone: false, error: null,
      output: { story_id: storyId, message: "请确认角色档案", need: null, approval_phase: "profile", character: proposedCharacter, writing_draft: null } };
    let staleGeneration = null;
    let commands = [rootCommand, approvalCommand];

    const json = (route, value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const runFor = command => ({ command, related_commands: [], steps: [{ event_id: command.submission.id, command_id: command.submission.id,
      node: "writing", label: "生成正文", status: "completed", explanation: "根据当前故事资料生成候选正文。", attempts: [] }],
      change_set: { text_changed: false, entries: [] } });
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => json(route, { database: true, redis: true, worker: true, provider: "mock", model: "fixture" }));
    await page.route("**/api/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "GET") {
        if (path === "/api/stories") return json(route, [{ id: storyId, title: story.title, revision_id: currentView.revision_id, scene_count: 0 }]);
        if (path === "/api/drafts") return json(route, []);
        if (path === `/api/stories/${storyId}`) return json(route, currentView);
        if (path === `/api/stories/${storyId}/commands`) return json(route, commands);
        if (path === `/api/undo/${storyId}`) return json(route, { available: true, operation_id: 31, command_ids: [generationId, approvalCommandId], message: "可撤回本轮" });
        const runId = path.match(/^\/api\/runs\/([^/]+)$/)?.[1];
        if (runId) {
          const command = commands.find(item => item.submission.id === runId);
          return command ? json(route, runFor(command)) : json(route, { detail: "合成运行不存在" }, 404);
        }
        return json(route, { detail: `未配置的合成 GET: ${path}` }, 404);
      }

      if (path !== "/api/commands") return json(route, { detail: `未配置的合成写入: ${path}` }, 404);
      const body = request.postDataJSON();
      submissions.push(body);
      if (body.payload?.kind === "undo") {
        assert.equal(body.payload.target_command_id, generationId);
        return json(route, { submission: body, status: "completed", undone: false, output: { story_id: storyId, message: "本轮已撤回" } });
      } else if (body.payload?.kind === "resolve_character") {
        assert.equal(body.payload.decision, "accept");
        approvalCommand.status = "completed";
        approvalCommand.output = { ...approvalCommand.output, message: "角色档案已确认", approval_phase: null, character: character };
        rootCommand.status = "completed";
        rootCommand.output = { ...rootCommand.output, message: "角色审批完成" };
        currentView = { ...currentView, revision_id: latestRevisionId, story: { ...currentView.story, characters: [character] } };
        staleGeneration = { submission: { id: staleGenerationId, actor: "user", story_id: storyId, base_revision_id: beforeRevisionId,
          session_id: null, payload: { kind: "generate", instruction: "资料更新前的旧写作要求" } },
          status: "waiting", undone: false, error: null,
          output: { story_id: storyId, message: "旧资料版本上的草稿", need: null,
            writing_draft: { generation_command_id: staleGenerationId, version: 1, status: "pending",
              content: { title: "旧版本草稿", text: "这份旧草稿只作为新草稿参考。", summary: "旧草稿摘要" } } } };
        commands = [rootCommand, approvalCommand, staleGeneration];
      } else if (body.payload?.kind === "generate") {
        submissions.push({ fixture_note: "latest-generate", body });
        const resultCommand = { submission: body, status: "completed", undone: false, error: null,
          output: { story_id: storyId, message: "最新资料上的新草稿已提交", need: null, writing_draft: null } };
        commands = [...commands, resultCommand];
        return json(route, { submission: body, status: "completed", undone: false, output: resultCommand.output });
      } else {
        return json(route, { detail: `不允许的合成命令: ${body.payload?.kind}` }, 400);
      }
      return json(route, { submission: body, status: "completed", undone: false,
        output: { story_id: storyId, message: "合成审批完成", need: null, character, writing_draft: null } });
    });

    await page.addInitScript(theme => localStorage.setItem("ainovel.theme", theme), process.env.AINOVEL_TEST_THEME || "dark");
    await page.goto(`/#story/${storyId}`);
    await page.locator(".topbar").filter({ hasText: story.title }).waitFor();
    await page.getByRole("button", { name: "创作记录", exact: true }).click();
    await page.locator(".records-pane").waitFor();
    if (process.env.AINOVEL_TEST_SCREENSHOT_INITIAL) await page.screenshot({ path: process.env.AINOVEL_TEST_SCREENSHOT_INITIAL, fullPage: false });
    assert.equal(await page.locator(".creation-record").count(), 2, "the conversation panel must replay durable generate/approval commands");
    const composerInput = page.locator("#instruction");
    assert.equal(await composerInput.isVisible(), true);
    await page.getByRole("button", { name: "创作记录", exact: true }).click();
    assert.equal(await page.locator(".records-pane").count(), 0, "closing history leaves the composer mounted");
    assert.equal(await composerInput.isVisible(), true, "composer input remains available when history is closed");
    if (process.env.AINOVEL_TEST_SCREENSHOT_CLOSED) await page.screenshot({ path: process.env.AINOVEL_TEST_SCREENSHOT_CLOSED, fullPage: false });
    await page.getByRole("button", { name: "创作记录", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: /撤回/ }).count(), 0, "撤回集中在操作历史");
    await page.locator(".decision-card").waitFor();
    assert.match(await page.locator(".decision-card").textContent(), /确认新角色档案/);
    const nameInput = page.locator(".profile-approval-form input");
    await nameInput.fill("沈遥·修改后");
    const description = page.locator(".profile-approval-form textarea");
    await description.fill("已根据审批人的判断修订档案描述");
    await page.locator(".decision-card").getByRole("button", { name: "确认角色档案" }).click();
    await page.getByText("草稿依据的是旧资料版本。", { exact: false }).waitFor({ timeout: 5000 }).catch(async error => {
      throw new Error(`${error.message}\nWorkbench text after profile approval:\n${await page.locator("body").innerText()}\nPage errors: ${pageErrors.join(" | ")}`);
    });

    const approval = submissions.find(item => item.payload?.kind === "resolve_character");
    assert.ok(approval, "profile approval should submit one resolve_character command");
    assert.equal(approval.story_id, storyId);
    assert.equal(approval.base_revision_id, beforeRevisionId);
    assert.equal(approval.payload.generation_command_id, generationId, "approval command must retain the root generation command id, not the child approval id");
    assert.equal(approval.payload.decision, "accept");
    assert.equal(approval.payload.character.name, "沈遥·修改后");
    assert.equal(approval.payload.character.profile.entries[0].description, "已根据审批人的判断修订档案描述");
    assert.deepEqual(pageErrors, [], "reactive character profile must be cloned without DataCloneError");

    const staleCard = page.locator(".decision-card");
    assert.match(await staleCard.textContent(), /旧资料版本/);
    assert.match(await staleCard.textContent(), /不能直接确认/);
    if (process.env.AINOVEL_TEST_SCREENSHOT) await page.screenshot({ path: process.env.AINOVEL_TEST_SCREENSHOT, fullPage: false });
    assert.equal(await staleCard.getByRole("button", { name: "按最新资料另写一份" }).isDisabled(), false);
    await staleCard.getByRole("button", { name: "按最新资料另写一份" }).click();
    await page.getByText("新任务已提交。旧草稿仍保留为历史候选。", { exact: false }).waitFor();

    const latestGenerate = submissions.find(item => item.fixture_note === "latest-generate")?.body;
    assert.ok(latestGenerate, "stale-draft action should submit a new generate command");
    assert.equal(latestGenerate.story_id, storyId);
    assert.equal(latestGenerate.base_revision_id, latestRevisionId, "new generation must use the current head revision");
    assert.equal(latestGenerate.payload.kind, "generate");
    assert.equal(latestGenerate.payload.new_chapter, false);
    assert.match(latestGenerate.payload.instruction, /原始写作要求：资料更新前的旧写作要求/);
    assert.match(latestGenerate.payload.instruction, /旧草稿摘要：旧草稿摘要/);
    assert.ok(commands.includes(staleGeneration), "the stale source draft remains in history after a separate latest-head generation");
    assert.equal(staleGeneration.status, "waiting");
    assert.deepEqual(pageErrors, []);
  } finally { await browser.close(); }
});
