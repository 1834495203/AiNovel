const assert = require("node:assert/strict");
const {randomUUID} = require("node:crypto");
const {test} = require("node:test");

test("confirm retries the failed command; double click never submits a second confirmation", {skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST}, async () => {
  const {chromium} = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    const stories = await (await page.request.get("/api/stories")).json();
    const source = stories.find(item => item.scene_count > 0);
    let current = await (await page.request.get("/api/stories/"+source.id)).json();
    const generationId = randomUUID(), confirmationId = randomUUID();
    const draft = {generation_command_id:generationId,version:1,status:"pending",content:{title:"确认重试的草稿",text:"失败的确认从原来的检查点继续。",summary:"摘要"}};
    const parent = {submission:{id:generationId,story_id:source.id,base_revision_id:current.revision_id,payload:{kind:"generate",instruction:"继续"}},status:"waiting",undone:false,output:{story_id:source.id,writing_draft:draft}};
    const failed = {submission:{id:confirmationId,story_id:source.id,base_revision_id:current.revision_id,payload:{kind:"review_draft",generation_command_id:generationId,expected_draft_version:1,decision:"confirm"}},status:"failed",undone:false,error:"维护失败",output:{}};
    const submissions = [], errors=[];
    page.on("pageerror",error => errors.push(error.message));
    await page.route("**/favicon.ico",route => route.fulfill({status:204}));
    await page.route("**/api/**",async route => {
      const request=route.request(), path=new URL(request.url()).pathname;
      const json=value => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
      if (request.method() === "POST") {
        const body=request.postDataJSON(); submissions.push(body);
        assert.equal(body.payload.kind,"recover_command");
        assert.equal(body.payload.target_command_id,confirmationId);
        assert.equal(body.payload.decision,"retry");
        current=structuredClone(current); current.revision_id=randomUUID();
        current.story.scenes.push({id:randomUUID(),chapter_id:current.story.chapters.at(-1).id,title:draft.content.title,text:draft.content.text,summary:draft.content.summary,character_patches:[]});
        failed.status="completed"; failed.error=null;
        failed.output={story_id:source.id,revision_id:current.revision_id,writing_draft:{...draft,status:"confirmed"},message:"确认完成"};
        parent.status="completed"; parent.output=failed.output;
        return json({submission:body,status:"completed",output:{recovered_command_id:confirmationId,recovery_decision:"retry"}});
      }
      if (path === "/api/stories/"+source.id) return json(current);
      if (path === "/api/stories/"+source.id+"/commands") return json([parent,failed]);
      if (path.startsWith("/api/undo/")) return json({available:true,command_ids:[generationId,confirmationId]});
      if (path.endsWith("/events")) return json([{id:45,event:{command_id:confirmationId,type:"run.failed"}}]);
      if (path.endsWith("/stream")) return route.fulfill({status:200,contentType:"text/event-stream",body:"event: done\ndata: {}\n\n"});
      if (path === "/api/commands/"+confirmationId) return json(failed);
      return route.continue();
    });
    await page.goto("/#story/"+source.id);
    await page.locator("#writing-draft:not([hidden])").waitFor();
    await page.locator("#writing-draft button").filter({hasText:"确认并提交"}).evaluate(button => { button.click(); button.click(); });
    await page.locator("#writing-draft[hidden]").waitFor({state:"attached"});
    assert.equal(submissions.length,1);
    assert.match(await page.locator("#novel").textContent(),/失败的确认从原来的检查点继续/);
    assert.equal(await page.locator(`[data-failed-command="${confirmationId}"]`).count(),0);
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});
