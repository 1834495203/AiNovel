/* Synthetic-only browser regression for setup drafts, interview recovery, and undo. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue creation preserves interview context and delegates recovery to central history", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const draftId = randomUUID(), firstId = randomUUID(), pendingId = randomUUID(), failedId = randomUUID(), retryId = randomUUID();
    const completedOutput = (reply, proposal = null) => ({ message: reply, story_deleted: false, story_restored: false, need: null,
      writing_draft: null, rewrite: null, interview: { reply, checklist: [
        { key: "world", label: "世界观", status: "defined", summary: "记忆可以交换", required: true },
        { key: "characters", label: "人物", status: "missing", summary: "还可以继续讨论", required: false },
      ], ...(proposal ? { proposal } : {}) } });
    const proposal = { title: "记忆渡口", world: { premise: "交换记忆需要付出代价。", entries: [] }, characters: [],
      preferences: { likes: [], dislikes: [], custom_fields: [] }, custom_fields: [] };
    const user1 = { role: "user", content: "我想写记忆交换的悬疑故事。" };
    const assistant1 = { role: "assistant", content: "交换记忆需要付出什么代价？" };
    const user2 = { role: "user", content: "每次交换都会遗忘一个重要的人。" };
    const turns1 = [user1];
    const turns2 = [user1, assistant1, user2];
    const submission = (id, payload) => ({ id, actor: "user", story_id: null, base_revision_id: null, session_id: draftId, payload });
    const command = (id, payload, status, output, error = null) => ({ submission: submission(id, payload), status, output,
      error, undone: false, scene_id: null });
    const first = command(firstId, { kind: "interview", turns: turns1 }, "completed", completedOutput(assistant1.content, proposal));
    const pending = command(pendingId, { kind: "interview", turns: turns2 }, "running",
      { message: "访谈处理中", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null });
    const failed = command(failedId, { kind: "interview", turns: [...turns2, { role: "user", content: "再补充一个细节" }] }, "failed",
      { message: "访谈失败", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null }, "模拟的首次失败");
    const retryFailure = command(retryId, failed.submission.payload, "failed",
      { message: "重试任务失败", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null }, "模拟重试任务");
    const retried = command(retryId, failed.submission.payload, "completed", completedOutput("重试后的合成答复"));
    const initialDraft = { id: draftId, title: proposal.title, input_text: "", pending_input: user2.content, proposal,
      editing_setup: false, edits: [], updated_at: new Date().toISOString(), save_event_id: 0 };
    let currentDraft = initialDraft;
    let currentCommands = [first, pending, failed];
    let latestUndo = { available: true, operation_id: 3, command_id: firstId, kind: "interview", message: "撤回最近一次构思更新", command_ids: [firstId] };
    let pendingCompleted = command(pendingId, pending.submission.payload, "completed", completedOutput("第二轮合成答复"));
    const interviewSubmissions = [], recoverySubmissions = [], postPayloads = [];
    let pageErrors = [];
    const pageLogs = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    page.on("console", message => { if (message.type() === "error") pageLogs.push(message.text()); });
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ database: true, redis: true, worker: true, provider: "mock", model: "fixture" }) }));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        if (path !== "/api/commands") return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "本合成测试不允许其他 POST" }) });
        const payload = body.payload;
        postPayloads.push(payload);
        if (payload.kind === "save_setup_draft") {
          currentDraft = payload.draft;
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(command(body.id, payload, "completed",
            { message: "草稿已保存", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null })) });
        }
        if (payload.kind === "interview") {
          interviewSubmissions.push(body);
          const result = command(body.id, payload, "completed", completedOutput("第三轮合成答复"));
          currentCommands = [...currentCommands, result];
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) });
        }
        if (payload.kind === "recover_command") {
          recoverySubmissions.push(body);
          if (payload.target_command_id === failedId && payload.decision === "withdraw") {
            currentCommands = [...currentCommands.filter(item => item.submission.id !== failedId), retryFailure];
          }
          if (payload.target_command_id === retryId && payload.decision === "retry") {
            currentCommands = [...currentCommands.filter(item => item.submission.id !== retryId), retried];
          }
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(command(body.id, payload, "completed",
            { message: "恢复决策已记录", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null })) });
        }
        if (payload.kind === "undo") {
          const latestInterview = currentCommands.slice().reverse().find(item => item.submission.payload.kind === "interview" && !item.undone);
          if (latestInterview) currentCommands = currentCommands.filter(item => item.submission.id !== latestInterview.submission.id);
          latestUndo = { ...latestUndo, available: false, operation_id: 4, command_ids: [] };
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(command(body.id, payload, "completed",
            { message: "已撤回", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null,
              undone_command_id: latestInterview?.submission.id ?? firstId })) });
        }
        return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "不允许的合成测试命令" }) });
      }
      if (path === `/api/drafts/${draftId}`) return json(route, currentDraft);
      if (path === `/api/sessions/${draftId}/commands`) return json(route, currentCommands);
      if (path === `/api/undo/${draftId}`) return json(route, latestUndo);
      if (path === `/api/commands/${pendingId}/stream` || path === `/api/commands/${retryId}/stream`)
        return route.fulfill({ status: 200, contentType: "text/event-stream", headers: { "Cache-Control": "no-cache" }, body: "event: done\ndata: {}\n\n" });
      if (path === `/api/commands/${pendingId}`) {
        currentCommands = [...currentCommands.filter(item => item.submission.id !== pendingId), pendingCompleted];
        return json(route, pendingCompleted);
      }
      if (path === `/api/commands/${retryId}`) return json(route, retried);
      if (path === `/api/commands/${failedId}/events`) return json(route, [{ id: 7, event: { command_id: failedId, actor: "system", type: "failed" } }]);
      if (path === `/api/commands/${retryId}/events`) return json(route, [{ id: 9, event: { command_id: retryId, actor: "system", type: "failed" } }]);
      if (path === "/api/stories" || path === "/api/drafts") return json(route, []);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: `未配置合成 GET：${path}` }) });
    });
    function json(route, body) { return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }); }

    await page.goto(`/#draft/${draftId}`);
    await page.getByText("第二轮合成答复", { exact: false }).waitFor({ timeout: 6000 }).catch(async error => {
      throw new Error(`${error.message}\nVisible page: ${await page.locator("body").innerText()}\nErrors: ${pageErrors.join(" | ")} ${pageLogs.join(" | ")}`);
    });
    assert.match(await page.locator(".chat-history").textContent(), /交换记忆需要付出什么代价/);
    assert.match(await page.locator(".chat-history").textContent(), /每次交换都会遗忘一个重要的人/);
    await page.getByRole("heading", { name: "构思进度" }).waitFor();
    assert.match(await page.locator(".creation-checklist").textContent(), /记忆可以交换/);

    await page.getByPlaceholder(/例如：我想写一部/).fill("主角试图找回那段记忆。");
    await page.waitForFunction(() => { const button = document.querySelector(".discussion-composer button"); return button && !button.disabled; });
    await page.getByRole("button", { name: "继续聊" }).click();
    await page.getByText("第三轮合成答复", { exact: false }).waitFor({ timeout: 6000 }).catch(async error => {
      const formState = await page.locator(".discussion-composer").evaluate(form => ({ value: form.querySelector("textarea").value, disabled: form.querySelector("button").disabled }));
      throw new Error(`${error.message}\nVisible page: ${await page.locator("body").innerText()}\nForm: ${JSON.stringify(formState)}\nPOST payloads: ${JSON.stringify(postPayloads)}\nErrors: ${pageErrors.join(" | ")} ${pageLogs.join(" | ")}`);
    });
    assert.deepEqual(interviewSubmissions[0].payload.turns.map(turn => turn.role), ["user", "assistant", "user", "assistant", "user"]);
    assert.equal(interviewSubmissions[0].payload.turns[1].content, assistant1.content, "下一轮必须保留上一条assistant上下文");

    assert.equal(await page.getByRole("button", { name: /撤回|重试/ }).count(), 0, "构思页不再提供分散恢复入口");
    assert.ok(await page.getByRole("button", { name: /运行历史/ }).count());
    assert.deepEqual(pageErrors, []);
  } finally { await browser.close(); }
});

test("a new setup draft autosaves and reloads at its stable draft URL", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const drafts = new Map(), errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ database: true, redis: true, worker: true, provider: "mock", model: "fixture" }) }));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        if (path === "/api/commands" && body.payload?.kind === "save_setup_draft") {
          drafts.set(body.payload.draft.id, body.payload.draft);
          return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ submission: body, status: "completed",
            output: { message: "草稿已保存", story_deleted: false, story_restored: false, need: null, writing_draft: null, rewrite: null }, undone: false }) });
        }
        return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "本合成测试只允许保存内存草稿" }) });
      }
      if (path === "/api/stories" || path === "/api/drafts") return json(route, []);
      const draftMatch = path.match(/^\/api\/drafts\/([^/]+)$/);
      if (draftMatch) return drafts.has(draftMatch[1]) ? json(route, drafts.get(draftMatch[1])) : route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      const sessionMatch = path.match(/^\/api\/sessions\/([^/]+)\/commands$/);
      if (sessionMatch) return json(route, []);
      const undoMatch = path.match(/^\/api\/undo\/([^/]+)$/);
      if (undoMatch) return json(route, { available: false, operation_id: null, command_id: null, kind: "", message: "", command_ids: [] });
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: `未配置合成 GET：${path}` }) });
    });
    function json(route, body) { return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }); }

    await page.goto("/#new");
    await page.getByPlaceholder(/例如：我想写一部/).waitFor();
    await page.waitForFunction(() => location.hash.startsWith("#draft/")).catch(async error => {
      throw new Error(`${error.message}\nURL: ${page.url()}\nPage: ${await page.locator("body").innerText()}\nErrors: ${errors.join(" | ")}\nDraft saves: ${JSON.stringify([...drafts.entries()])}`);
    });
    const savedDraftId = page.url().split("#draft/")[1];
    assert.ok(savedDraftId);
    await page.getByPlaceholder(/例如：我想写一部/).fill("构思自动保存后可以继续");
    await page.getByText("正在保存草稿…", { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector(".creation-save-state")?.textContent === "草稿已保存");
    assert.equal(drafts.get(savedDraftId)?.input_text, "构思自动保存后可以继续", `autosaved draft did not preserve input: ${JSON.stringify(drafts.get(savedDraftId))}`);
    await page.reload();
    await page.getByPlaceholder(/例如：我想写一部/).waitFor();
    assert.equal(page.url().split("#draft/")[1], savedDraftId, "刷新仍停留在同一构思草稿");
    assert.equal(await page.getByPlaceholder(/例如：我想写一部/).inputValue(), "构思自动保存后可以继续");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
