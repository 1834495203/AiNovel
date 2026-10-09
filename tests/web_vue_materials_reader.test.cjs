/* Synthetic browser smoke for the Vue reader and materials components.
 * Every API request is intercepted; the fixture is never loaded from a user's library. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { test } = require("node:test");

test("Vue reader and materials use the synthetic story fixture and preserve Unicode selections", {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined,
  });
  try {
    const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000" });
    const storyId = randomUUID(), revisionId = randomUUID(), chapterId = randomUUID(), sceneId = randomUUID(), secondSceneId = randomUUID();
    const characterId = randomUUID(), commandId = randomUUID(), entryId = randomUUID();
    const story = {
      id: storyId,
      title: "合成组件测例",
      world: { premise: "只有本次浏览器测试会读取的合成世界。", entries: [{ id: entryId, name: "潮汐规则", value: "演示", description: "合成资料", reason: "测试夹具", children: [] }] },
      preferences: { likes: [], dislikes: [], custom_fields: [] },
      writer: { mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: "", current_user_intent: "", custom_fields: [] },
      characters: [{ id: characterId, name: "林初", introduced_scene_id: null, aliases: [], profile: { entries: [] }, custom_fields: [] }],
      chapters: [{ id: chapterId, title: "第一章 · 测试", summary: "合成章节" }],
      scenes: [{ id: sceneId, chapter_id: chapterId, title: "窗边", summary: "合成场景", text: "😀林初看见了信。<img src=x onerror=alert(1)>", source_command_id: commandId, character_patches: [] },
        { id: secondSceneId, chapter_id: chapterId, title: "回声", summary: "第二幕合成场景", text: "林初听见远处传来一声回响。", source_command_id: null, character_patches: [] }],
      directions: [], initial_snapshots: [], custom_fields: [],
    };
    const view = { revision_id: revisionId, story, characters_at_scenes: [{ character_id: characterId, scene_id: sceneId,
      name: "林初", aliases: [], profile: { entries: [] }, state: { entries: [{ id: randomUUID(), name: "当时的心情", value: "谨慎", description: "刚读到信", reason: "合成夹具", children: [] }] }, custom_fields: [] }] };
    const sourceCommand = { submission: { id: commandId, actor: "user", story_id: storyId, base_revision_id: revisionId, session_id: null,
      payload: { kind: "generate", instruction: "合成测试的创作指令" } }, status: "completed", output: { message: "本幕合成来源运行", story_deleted: false,
      story_restored: false, need: null, writing_draft: null, rewrite: null }, undone: false, scene_id: sceneId };
    const prompt = JSON.stringify({ model: "mock", messages: [{ role: "system", content: "合成系统提示" }, { role: "user", content: "合成资料上下文" }],
      tools: [{ function: { name: "submit_Draft" } }], tool_choice: { function: { name: "submit_Draft" } } });
    const attemptOutput = JSON.stringify({ choices: [{ finish_reason: "tool_calls", message: { tool_calls: [{ id: "tool-fixture", function: { name: "submit_Draft" } }] } }] });
    const run = { command: sourceCommand, related_commands: [], steps: [{ event_id: 1, command_id: commandId, node: "write", label: "生成正文",
      status: "completed", explanation: "写出合成场景", model: "mock", duration_ms: 12, input_tokens: 3, output_tokens: 4,
      cached_tokens: 0, prompt_version: "Draft:v3-fixture", structured_input: prompt,
      structured_output: JSON.stringify({ summary: "测试结果" }), raw_output: attemptOutput, tool_name: "submit_Draft", tool_call_id: "tool-fixture",
      finish_reason: "tool_calls", estimated_usage: false, attempts: [{ model: "mock", prompt_version: "Draft:v3-fixture", duration_ms: 12,
        input_tokens: 3, output_tokens: 4, cached_tokens: 0, error: "合成的首次校验错误", structured_input: prompt,
        structured_output: "", raw_output: attemptOutput, tool_name: "submit_Draft", tool_call_id: "tool-fixture", finish_reason: "tool_calls", estimated_usage: false }] }],
      change_set: { text_changed: false, entries: [] } };
    const snapshot = { character_id: characterId, scene_id: sceneId, state: view.characters_at_scenes[0].state,
      profile: { entries: [] }, aliases: [], custom_fields: [], experiences: [] };
    let currentView = view;
    let currentCommands = [sourceCommand];
    const postRequests = [], pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.route("**/favicon.ico", route => route.fulfill({ status: 204 }));
    await page.route("**/health", route => route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ database: true, redis: true, worker: true, provider: "mock", model: "mock" }) }));
    await page.route("**/api/**", async route => {
      const request = route.request();
      if (request.method() !== "GET") {
        const path = new URL(request.url()).pathname;
        const body = request.postDataJSON();
        if (path !== "/api/commands" || body.payload?.kind !== "update_story") {
          postRequests.push({ method: request.method(), url: request.url() });
          return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "仅允许更新本测试内存中的合成资料" }) });
        }
        postRequests.push(body);
        const patch = body.payload.patch.world?.entries;
        const nextEntries = [...currentView.story.world.entries.filter(item => !patch?.remove?.includes(item.id)), ...(patch?.upsert ?? [])];
        currentView = { ...currentView, revision_id: randomUUID(), story: { ...currentView.story,
          world: { ...currentView.story.world, ...(body.payload.patch.world?.premise === undefined ? {} : { premise: body.payload.patch.world.premise }),
            entries: nextEntries } } };
        const result = { submission: body, status: "completed", output: { message: "合成资料已保存", story_deleted: false,
          story_restored: false, need: null, writing_draft: null, rewrite: null, revision_id: currentView.revision_id }, undone: false };
        currentCommands = [...currentCommands, result];
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) });
      }
      const path = new URL(request.url()).pathname;
      const json = value => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
      if (path === "/api/stories") return json([{ id: storyId, title: story.title, revision_id: revisionId, scene_count: 2 }]);
      if (path === "/api/drafts") return json([]);
      if (path === "/health") return json({ database: true, redis: true, worker: true, provider: "mock", model: "mock" });
      if (path === `/api/stories/${storyId}`) return json(currentView);
      if (path === `/api/stories/${storyId}/commands`) return json(currentCommands);
      if (path === `/api/undo/${storyId}`) return json({ available: false, command_ids: [], message: "" });
      if (path === `/api/runs/${commandId}`) return json(run);
      if (path === `/api/revisions/${revisionId}/characters/${characterId}/snapshots/${sceneId}`) return json(snapshot);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "未配置的合成 API 请求" }) });
    });

    await page.addInitScript(({ storyId: persistedStoryId, sceneId: persistedSceneId }) => {
      localStorage.setItem(`ainovel.readerScene.${persistedStoryId}`, persistedSceneId);
    }, { storyId, sceneId: secondSceneId });
    await page.goto(`/#story/${storyId}`);
    await page.locator(".novel-reader").waitFor({ timeout: 6000 }).catch(async error => {
      throw new Error(`${error.message}\nVisible page: ${await page.locator("body").innerText()}\nPage errors: ${pageErrors.join(" | ")}`);
    });
    assert.equal(await page.locator(".records-pane").count(), 0);
    await page.getByRole("button", { name: "目录", exact: true }).click();
    assert.equal((await page.locator(".directory-pane .active").textContent()).trim(), "回声");
    await page.getByRole("button", { name: "回声", exact: true }).click();
    await page.getByRole("button", { name: "目录", exact: true }).click();
    assert.equal(await page.locator(".reader-prose img, .reader-prose script").count(), 0, "正文必须作为纯文本节点渲染");
    assert.match(await page.locator(".reader-prose").first().textContent(), /<img src=x onerror=alert\(1\)>/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: require("node:path").join(__dirname, "..", ".tmp", "reader-mobile-light.png") });
    await page.getByRole("button", { name: /切换.*色模式/ }).click();
    assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
    assert.notEqual(await page.locator(".reader-prose").first().evaluate(element => getComputedStyle(element).color), "rgb(57, 58, 50)", "暗黑主题正文应使用主题文字色");
    await page.waitForFunction(() => {
      const input = document.querySelector(".studio-composer .n-input");
      if (!input) return false;
      const background = getComputedStyle(input).backgroundColor;
      return background !== "rgb(255, 255, 255)" && background !== "rgba(0, 0, 0, 0)";
    }, { timeout: 3000 });
    await page.waitForTimeout(350);
    const darkButtonText = await page.locator(".pane-toolbar .n-button").first().evaluate(element => getComputedStyle(element).color);
    assert.notEqual(darkButtonText, "rgb(0, 0, 0)", "暗色主题目录按钮应使用可读的前景色");
    const composerButtonDiagnostics = await page.locator(".studio-composer .n-button--primary-type").evaluate(element => {
      const parse = value => value.match(/[\d.]+/g).slice(0, 3).map(Number).map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      const luminance = value => { const [r, g, b] = parse(value); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const style = getComputedStyle(element);
      const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => b - a);
      return { contrast: (values[0] + 0.05) / (values[1] + 0.05), outerHTML: element.outerHTML,
        color: style.color, backgroundColor: style.backgroundColor, textColorVariable: style.getPropertyValue("--n-text-color") };
    });
    assert.ok(composerButtonDiagnostics.contrast >= 4.5, `暗色主题固定创作按钮文字对比度不足：${composerButtonDiagnostics.contrast.toFixed(2)}:1 ${JSON.stringify(composerButtonDiagnostics)}`);
    await page.screenshot({ path: require("node:path").join(__dirname, "..", ".tmp", "reader-mobile-dark.png") });
    await page.getByRole("button", { name: /切换.*色模式/ }).click();

    await page.locator(".scene-run-summary").first().waitFor();
    assert.match(await page.locator(".scene-run-summary").first().textContent(), /生成正文/);
    assert.equal(await page.locator(".reader-process").count(), 0);
    assert.doesNotMatch(await page.locator(".novel-reader").textContent(), /合成系统提示/);
    const role = page.locator(".reader-character-mention").first();
    await role.hover();
    await page.getByText("林初 · 本幕状态", { exact: true }).waitFor();
    assert.match(await page.locator(".reader-stage-card").textContent(), /谨慎/);
    await page.screenshot({ path: require("node:path").join(__dirname, "..", ".tmp", "reader-mobile-stage-hover.png") });
    assert.match(await role.getAttribute("href"), new RegExp(`#character\\?story=${storyId}&revision=${revisionId}.*scene=${sceneId}.*character=${characterId}`));

    const selectedText = await page.locator(".reader-prose").first().evaluate(element => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const textNodes = [];
      while (walker.nextNode()) textNodes.push(walker.currentNode);
      const first = textNodes.find(node => node.textContent.includes("😀"));
      const last = textNodes.find(node => node.textContent.includes("看见"));
      const range = document.createRange();
      range.setStart(first, first.textContent.indexOf("😀")); range.setEnd(last, last.textContent.indexOf("看见") + "看见".length);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      window.dispatchEvent(new Event("selectionchange"));
      return selection.toString();
    });
    assert.equal(selectedText, "😀林初看见");
    await page.getByText("已选 5 个字符", { exact: true }).waitFor();
    await page.mouse.move(2, 2);
    await page.waitForTimeout(250);
    await page.getByRole("button", { name: "讨论修改", exact: true }).click();
    await page.getByRole("heading", { name: "你希望怎样修改？" }).waitFor();
    await page.getByLabel("修改方向").fill("保持原意并减少解释");
    assert.equal(postRequests.length, 0, "打开编辑界面不应提交任何写操作");
    await page.getByRole("button", { name: "取消", exact: true }).click();

    await page.mouse.move(2, 2);
    await page.waitForTimeout(250);
    await page.getByRole("button", { name: /故事资料/ }).click();
    await page.locator(".story-materials").waitFor();
    await page.getByRole("heading", { name: "世界设定" }).waitFor();
    assert.match(await page.locator(".materials-explainer").textContent(), /已有正文不会自动改写/);
    await page.getByRole("button", { name: "添加条目", exact: true }).click();
    await page.getByLabel("条目名称").fill("临时合成条目");
    assert.equal(postRequests.length, 0, "未点击保存时不得提交资料修改");
    await page.getByRole("button", { name: "保存条目", exact: true }).click();
    await page.getByText("临时合成条目", { exact: true }).waitFor();
    assert.equal(postRequests.length, 1);
    assert.equal(postRequests[0].payload.patch.world.entries.upsert.length, 1);
    assert.equal(postRequests[0].payload.patch.world.entries.remove.length, 0);
    assert.equal(postRequests[0].payload.patch.preferences, undefined, "世界观单条修改不应覆盖其他资料");
    assert.equal(postRequests[0].payload.patch.writer, undefined, "世界观单条修改不应覆盖计划" );
    const storyRoute = `story/${storyId}`;
    await page.getByRole("button", { name: "小说工作区", exact: true }).click();
    await page.locator(".novel-reader").waitFor();
    assert.equal(page.url().split("#")[1], storyRoute, "目录滚动不能覆盖工作台路由 hash");
    await page.reload();
    await page.getByRole("button", { name: "目录", exact: true }).click();
    await page.locator(".directory-pane .active").waitFor();
    assert.equal((await page.locator(".directory-pane .active").textContent()).trim(), "回声");
    await page.getByRole("button", { name: "回声", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      offenders: [...document.querySelectorAll("body *")].filter(element => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && (rect.left < -1 || rect.right > window.innerWidth + 1);
      }).slice(0, 8).map(element => `${element.tagName}.${String(element.className).replace(/\\s+/g, ".")}:${Math.round(element.getBoundingClientRect().left)}-${Math.round(element.getBoundingClientRect().right)}`),
    }));
    assert.ok(overflow.width <= 391, `窄屏正文不能产生横向溢出：${JSON.stringify(overflow)}`);
    assert.ok(await page.locator(".directory-pane").isVisible(), "目录保持为独立面板");
    assert.deepEqual(pageErrors, []);
    assert.ok(postRequests.every(item => item.payload?.patch?.world?.entries?.upsert?.[0]?.name === "临时合成条目"));
  } finally { await browser.close(); }
});
