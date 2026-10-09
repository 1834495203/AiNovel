/* Character cards and stage chats run entirely against a synthetic in-memory fixture. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue character pages keep stage snapshots, explain nested profile facts, and isolate chat sessions", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const storyId = randomUUID(), revisionId = randomUUID(), chapterId = randomUUID(), sceneId = randomUUID(), characterId = randomUUID();
    const character = { id: characterId, name: "沈遥", introduced_scene_id: sceneId, aliases: [], profile: { entries: [{ id: randomUUID(), name: "来历", value: null,
      description: "住在旧港", reason: "角色亲口说明", children: [{ id: randomUUID(), name: "旧物", value: "铜钥匙", description: "随身携带", reason: "现场观察", children: [] }] }] }, custom_fields: [] };
    const story = { id: storyId, title: "合成人物页", world: { premise: "测试世界", entries: [] }, preferences: { likes: [], dislikes: [], custom_fields: [] },
      writer: { mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: "", current_user_intent: "", custom_fields: [] },
      characters: [character], chapters: [{ id: chapterId, title: "第一章", summary: "合成" }], scenes: [{ id: sceneId, chapter_id: chapterId, title: "港口", summary: "合成", text: "她来到旧港。", source_command_id: null, character_patches: [] }], directions: [], initial_snapshots: [], custom_fields: [] };
    const view = { revision_id: revisionId, story, characters_at_scenes: [] };
    const snapshot = { character_id: characterId, scene_id: sceneId, name: character.name, aliases: [], profile: character.profile,
      state: { entries: [{ id: randomUUID(), name: "当时的心情", value: "警惕", description: "刚到港口", reason: "该幕描写", children: [] }] }, custom_fields: [], experiences: [] };
    const commands = [], submissions = [], errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const json = (route, value) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => json(route, { database: true, redis: true, worker: true, provider: "mock", model: "fixture" }));
    await page.route("**/api/**", async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      if (req.method() === "POST") {
        assert.equal(path, "/api/commands", "only the unified command endpoint is writable");
        const body = req.postDataJSON(); submissions.push(body);
        const payload = body.payload;
        if (payload.kind === "start_character_chat") {
          const result = { submission: body, status: "completed", output: { chat_session_id: payload.session_id }, undone: false };
          commands.push(result); return json(route, result);
        }
        assert.equal(payload.kind, "character_chat");
        const result = { submission: body, status: "completed", output: { chat: { reply: "合成阶段回复", canon: false } }, undone: false };
        commands.push(result); return json(route, result);
      }
      if (path === "/api/stories") return json(route, [{ id: storyId, title: story.title, revision_id: revisionId, scene_count: 1 }]);
      if (path === "/api/drafts") return json(route, []);
      if (path === `/api/stories/${storyId}`) return json(route, view);
      if (path === `/api/stories/${storyId}/commands`) return json(route, commands);
      if (path === `/api/revisions/${revisionId}`) return json(route, { id: revisionId, story_id: storyId, parent_id: null, command_id: randomUUID(), reason: "合成版本", story });
      if (path === `/api/revisions/${revisionId}/characters`) return json(route, [snapshot]);
      if (path === `/api/revisions/${revisionId}/characters/${characterId}/snapshots/${sceneId}`) return json(route, snapshot);
      if (path === `/api/undo/${storyId}`) return json(route, { available: false, command_ids: [], message: "" });
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: `未配置的合成 GET: ${path}` }) });
    });

    await page.goto(`/characters?story=${storyId}`);
    await page.getByRole("heading", { name: /合成人物页.*人物与阶段/ }).waitFor();
    await page.getByRole("button", { name: "查看完整档案" }).click();
    await page.getByText(/角色亲口说明/).waitFor();
    await page.getByText(/现场观察/).waitFor();
    await page.locator(".character-detail-dialog .n-base-close").click();
    await page.getByRole("link", { name: "聊聊此时的角色" }).click();
    await page.getByRole("heading", { name: "与 沈遥 聊聊" }).waitFor();
    await page.getByRole("button", { name: "新会话" }).click();
    await page.waitForFunction(() => document.querySelectorAll(".character-sessions button").length === 3, null, { timeout: 5000 })
      .catch(async error => { throw new Error(`${error.message}\nsubmissions=${JSON.stringify(submissions)}\nbody=${await page.locator("body").innerText()}\nerrors=${errors.join(" | ")}`); });
    const sessionButtons = page.locator(".character-sessions button");
    await sessionButtons.nth(1).click();
    const composer = page.getByPlaceholder("向角色提问……");
    await composer.fill("你还记得那把钥匙吗？");
    await page.getByRole("button", { name: "发送" }).click();
    await page.getByText("合成阶段回复").waitFor();
    assert.equal(submissions.filter(item => item.payload.kind === "start_character_chat").length, 2);
    const chat = submissions.find(item => item.payload.kind === "character_chat");
    assert.equal(chat.base_revision_id, revisionId, "stage chat remains bound to its selected revision");
    assert.ok(chat.payload.session_id);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
