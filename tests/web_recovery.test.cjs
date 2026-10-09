/* Every write is intercepted; existing novels are used only for read-only UI context. */
const assert = require("node:assert/strict");
const {randomUUID} = require("node:crypto");
const {test} = require("node:test");

test("failed tasks survive reload and offer checkpoint retry and withdrawal", {skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST}, async () => {
  const {chromium} = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    const stories = await (await page.request.get("/api/stories")).json();
    const story = stories.find(item => item.scene_count > 0);
    assert.ok(story);
    const current = await (await page.request.get("/api/stories/"+story.id)).json();
    const baseline = JSON.stringify(current.story);
    const command = {submission:{id:randomUUID(),story_id:story.id,base_revision_id:current.revision_id,payload:{kind:"generate",instruction:"继续写故事"}},status:"failed",error:"模拟连接失败",undone:false,output:{}};
    const submissions = [], errors = [];
    page.on("pageerror",error => errors.push(error.message));
    await page.route("**/favicon.ico",route => route.fulfill({status:204}));
    await page.route("**/api/**",route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = value => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
      if (request.method() === "POST") {
        assert.equal(path,"/api/commands");
        const body = request.postDataJSON(); submissions.push(body);
        assert.equal(body.payload.kind,"recover_command");
        assert.equal(body.payload.target_command_id,command.submission.id);
        assert.equal(body.payload.expected_event_id,33);
        if (body.payload.decision === "retry") {
          command.status="waiting"; command.error=null;
          command.output={writing_draft:{generation_command_id:command.submission.id,version:1,status:"pending",content:{title:"恢复的草稿",text:"这里是重试后的草稿。",summary:"摘要"}}};
        } else command.undone=true;
        return json({submission:body,status:"completed",undone:false,output:{recovered_command_id:command.submission.id,recovery_decision:body.payload.decision}});
      }
      if (path === "/api/stories/"+story.id) return json(current);
      if (path === "/api/stories/"+story.id+"/commands") return json([command]);
      if (path.startsWith("/api/undo/")) return json({available:true,operation_id:1,command_ids:[command.submission.id]});
      if (path.endsWith("/events")) return json([{id:33,event:{command_id:command.submission.id,type:"run.failed"}}]);
      if (path.endsWith("/stream")) return route.fulfill({status:200,contentType:"text/event-stream",body:"event: done\ndata: {}\n\n"});
      if (path === "/api/commands/"+command.submission.id) return json(command);
      return route.continue();
    });
    await page.goto("/#story/"+story.id);
    await page.locator("[data-failed-command]").waitFor({state:"attached"});
    await page.reload();
    await page.locator("[data-failed-command]").waitFor({state:"attached"});
    if (!await page.locator("#chat-panel").isVisible()) await page.locator("#toggle-chat").click();
    await page.locator("[data-failed-command]").getByRole("button",{name:"重试",exact:true}).click();
    await page.locator("#writing-draft:not([hidden])").waitFor();
    assert.equal(await page.locator("#writing-draft .prose").textContent(),"这里是重试后的草稿。");
    command.status="failed"; command.error="再次失败"; command.output={};
    await page.reload();
    await page.locator("[data-failed-command]").waitFor({state:"attached"});
    if (!await page.locator("#chat-panel").isVisible()) await page.locator("#toggle-chat").click();
    await page.locator("[data-failed-command]").getByRole("button",{name:"撤回这次失败",exact:true}).click();
    await page.locator("[data-failed-command]").waitFor({state:"detached"});
    assert.deepEqual(submissions.map(item => item.payload.decision),["retry","withdraw"]);
    assert.equal(JSON.stringify(current.story),baseline);
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});
