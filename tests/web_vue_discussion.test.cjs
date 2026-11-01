/* Vue discussion smoke with entirely synthetic in-memory story data. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue discussion restores drafts and separates sessions with conversation-tail actions", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const storyId = randomUUID(), revisionId = randomUUID(), firstSessionId = randomUUID();
    const storyView = { revision_id: revisionId, story: { id: storyId, title: "合成讨论", scenes: [], chapters: [] } };
    const sessions = [{ id: firstSessionId, story_id: storyId, title: "初始讨论", message_count: 0 }];
    const bySession = new Map([[firstSessionId, []]]), all = new Map(), writes = [], pageErrors = [];
    let undoAvailable = true, attempts = 0;
    page.on("pageerror", error => pageErrors.push(error.message));
    const json = (route, value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => json(route, { database: true, redis: true, worker: true, provider: "mock", model: "fixture" }));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === "GET") {
        if (path === "/api/stories") return json(route, [{ id: storyId, title: "合成讨论", revision_id: revisionId, scene_count: 0 }]);
        if (path === "/api/drafts") return json(route, []);
        if (path === `/api/stories/${storyId}`) return json(route, storyView);
        if (path === `/api/stories/${storyId}/discussions`) return json(route, sessions);
        const sessionHistory = path.match(new RegExp(`/api/stories/${storyId}/discussions/([^/]+)/commands$`));
        if (sessionHistory) return json(route, bySession.get(sessionHistory[1]) || []);
        if (path.startsWith("/api/undo/")) return json(route, { available: undoAvailable, operation_id: 12, command_id: [...all.values()].filter(item => !item.undone && item.submission.payload.session_id === path.split("/").at(-1)).at(-1)?.submission.id, command_ids: [...all.keys()], message: "合成撤回" });
        if (path.includes("/events")) return json(route, [{ id: 3 }]);
        const commandMatch = path.match(/\/api\/commands\/([^/]+)$/);
        if (commandMatch && all.has(commandMatch[1])) return json(route, all.get(commandMatch[1]));
        if (path.startsWith("/api/runs/")) return json(route, { command: all.get(path.split("/").at(-1)), related_commands: [], steps: [],
          change_set: { text_changed: false, entries: [] }, waiting_for: null, draft_stale: false, current_revision_id: revisionId });
        return json(route, { detail: `未配置的合成 GET: ${path}` }, 404);
      }
      assert.equal(request.method(), "POST");
      assert.equal(path, "/api/commands", "所有写操作均由合成 command mock 截获");
      const body = request.postDataJSON(), payload = body.payload; writes.push(body);
      if (payload.kind === "start_discussion") {
        const session = { id: payload.session_id, story_id: storyId, title: payload.title, message_count: 0 };
        sessions.unshift(session); bySession.set(session.id, []);
        return json(route, { submission: body, status: "completed", output: { discussion_session: session }, undone: false });
      }
      if (payload.kind === "undo") {
        undoAvailable = false;
        const last = all.get(payload.target_command_id);
        if (last) last.undone = true;
        return json(route, { submission: body, status: "completed", output: { message: "合成撤回完成" }, undone: false });
      }
      if (payload.kind === "recover_command") {
        const target = all.get(payload.target_command_id);
        if (target) { target.status = "completed"; target.output = { discussion: { session_id: target.submission.payload.session_id, reply: "重试成功的合成回复", read_revision_id: revisionId, settings_changed: false } }; }
        return json(route, { submission: body, status: "completed", output: { message: "合成重试" }, undone: false });
      }
      assert.equal(payload.kind, "discussion");
      const command = { submission: body, status: "failed", error: "合成的临时失败", output: {}, undone: false };
      all.set(body.id, command); bySession.get(payload.session_id).push(command); attempts++;
      if (attempts > 1) { command.status = "completed"; command.error = null; command.output = { discussion: { session_id: payload.session_id, reply: "已保存的合成讨论回复", read_revision_id: revisionId, settings_changed: false } }; }
      return json(route, command);
    });

    await page.goto(`/#discussion/${storyId}?session=${firstSessionId}`);
    await page.locator(".discussion-workspace").waitFor();
    const composer = page.getByPlaceholder("聊聊后续剧情、人物或世界规则…");
    await composer.fill("失败后也要保留的输入");
    await page.getByRole("button", { name: "发送" }).click();
    await page.getByText("合成的临时失败").waitFor();
    assert.equal(await composer.inputValue(), "失败后也要保留的输入");
    await page.reload();
    await page.getByText("合成的临时失败").waitFor();
    assert.equal(await composer.inputValue(), "失败后也要保留的输入", "刷新后恢复未成功发送的输入");
    assert.equal(await page.getByRole("button", { name: "重试", exact: true }).count(), 1);

    await page.getByRole("button", { name: "新建讨论" }).click();
    await page.locator(".discussion-workspace").waitFor();
    assert.equal(sessions.length, 2, "点击新讨论会创建独立会话");
    await composer.fill("另一条合成消息");
    await page.getByRole("button", { name: "发送" }).click();
    await page.getByText("已保存的合成讨论回复").waitFor();
    assert.equal(await composer.inputValue(), "", "服务已接受命令后清除输入草稿");
    assert.equal(await page.getByRole("button", { name: "回退", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: /运行历史/ }).count(), 0);
    await page.getByRole("button", { name: "回退", exact: true }).click();
    await page.getByText("已保存的合成讨论回复", { exact: true }).waitFor({ state: "hidden" });
    assert.equal(writes.at(-1).payload.scope_id, sessions[0].id);
    assert.equal(bySession.get(firstSessionId)[0].undone, false, "其他会话消息保留");
    assert.ok(writes.every(item => item.story_id === storyId || item.story_id == null));
    assert.deepEqual(pageErrors, []);
  } finally { await browser.close(); }
});

