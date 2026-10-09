/* All writes are mocked: the real novels are used only for read-only page context. */
const assert=require("node:assert/strict");
const {randomUUID}=require("node:crypto");
const {test}=require("node:test");

test("discussion sessions persist, read fresh heads, edit settings and undo without editing prose",{skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST},async () => {
  const {chromium}=require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser=await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page=await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    const books=await (await page.request.get("/api/stories")).json(),book=books.find(item => item.scene_count>0);
    let current=await (await page.request.get("/api/stories/"+book.id)).json();
    const prose=JSON.stringify(current.story.scenes), first=randomUUID(), second=randomUUID();
    const sessions=[{id:first,story_id:book.id,title:"第一讨论",message_count:1},{id:second,story_id:book.id,title:"第二讨论",message_count:0}];
    const initial={submission:{id:randomUUID(),story_id:book.id,base_revision_id:current.revision_id,payload:{kind:"discussion",session_id:first,message:"讨论主线"}},status:"completed",output:{discussion:{session_id:first,reply:"这是第一会话的历史",read_revision_id:current.revision_id,settings_changed:false}},undone:false};
    const commands=new Map([[first,[initial]],[second,[]]]), all=new Map([[initial.submission.id,initial]]), before=new Map();
    const writes=[],errors=[];page.on("pageerror",error => errors.push(error.message));
    await page.route("**/favicon.ico",route => route.fulfill({status:204}));
    await page.route("**/api/**",async route => {
      const req=route.request(),path=new URL(req.url()).pathname;
      const json=value => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
      if (req.method() === "POST") {
        assert.equal(path,"/api/commands");const body=req.postDataJSON();writes.push(body);
        if (body.payload.kind === "start_discussion") {
          const session={id:body.payload.session_id,story_id:book.id,title:body.payload.title,message_count:0};sessions.unshift(session);commands.set(session.id,[]);
          return json({submission:body,status:"completed",output:{discussion_session:session}});
        }
        if (body.payload.kind === "undo") {
          const target=[...all.values()].filter(item => !item.undone).at(-1);target.undone=true;
          if (before.has(target.submission.id)) current=before.get(target.submission.id);
          return json({submission:body,status:"completed",output:{message:"已撤回"}});
        }
        assert.equal(body.payload.kind,"discussion");assert.equal(body.base_revision_id,current.revision_id);
        const command={submission:body,status:"queued",output:{},undone:false};all.set(body.id,command);commands.get(body.payload.session_id).push(command);
        return json(command);
      }
      if (path === "/api/stories/"+book.id) return json(current);
      if (path === "/api/stories/"+book.id+"/discussions") return json(sessions);
      const history=path.match(/\/discussions\/([^/]+)\/commands$/);if (history) return json((commands.get(history[1]) || []).filter(item => !item.undone));
      if (path.startsWith("/api/undo/")) return json({available:true,operation_id:3,command_ids:[...all.keys()]});
      if (path.endsWith("/events")) return json([]);
      const commandPath=path.match(/\/api\/commands\/([^/]+)(\/stream)?$/);
      if (commandPath) {
        const command=all.get(commandPath[1]);
        if (!commandPath[2]) return json(command);
        await new Promise(resolve => setTimeout(resolve,100));
        const changed=command.submission.payload.message.includes("保存设定"), read=current.revision_id;
        if (changed) {before.set(command.submission.id,structuredClone(current));current=structuredClone(current);current.revision_id=randomUUID();current.story.world.premise="讨论后保存的新世界";}
        command.status="completed";command.output={story_id:book.id,revision_id:changed ? current.revision_id : null,discussion:{session_id:command.submission.payload.session_id,reply:changed ? "**设定已保存**，正文保持原样。" : "读取最新世界："+current.story.world.premise,read_revision_id:read,settings_changed:changed,updated_revision_id:changed ? current.revision_id : null}};
        sessions.find(item => item.id === command.submission.payload.session_id).message_count++;
        return route.fulfill({status:200,contentType:"text/event-stream",body:"event: done\ndata: {}\n\n"});
      }
      return route.continue();
    });
    await page.goto("/discussion?story="+book.id+"&session="+first);
    await page.getByText("这是第一会话的历史",{exact:true}).waitFor();
    assert.equal(await page.locator("#discussion-sessions button").count(),2);
    await page.locator(`[data-session="${second}"]`).click();
    await page.locator(".discussion-empty").waitFor();
    assert.doesNotMatch(await page.locator("#discussion-messages").textContent(),/第一会话的历史/);
    await page.locator("#discussion-input").fill("未发送的讨论内容");
    await page.reload();await page.waitForFunction(() => document.querySelector("#discussion-input").value === "未发送的讨论内容");
    current=structuredClone(current);current.revision_id=randomUUID();current.story.world.premise="外部刚更新的世界";
    await page.locator("#discussion-input").fill("聊聊新设定");
    await page.locator("#discussion-form button").evaluate(button => {button.click();button.click();});
    await page.getByText("读取最新世界：外部刚更新的世界",{exact:true}).waitFor();
    await page.waitForFunction(() => !document.querySelector("#discussion-input").disabled);
    assert.equal(writes.filter(item => item.payload.kind === "discussion").length,1);
    assert.equal(JSON.stringify(current.story.scenes),prose);
    await page.locator("#new-discussion").click();
    await page.waitForFunction(() => !document.querySelector("#discussion-input").disabled);
    assert.equal(await page.locator("#discussion-sessions button").count(),3);
    await page.locator("#discussion-input").fill("请保存设定");await page.locator("#discussion-form button").click();
    await page.locator(".settings-changed").waitFor();await page.waitForFunction(() => !document.querySelector("#discussion-input").disabled);
    assert.equal(await page.locator(".chat-text strong").first().textContent(),"设定已保存");
    assert.equal(JSON.stringify(current.story.scenes),prose);
    assert.equal(await page.locator("#discussion-undo").isEnabled(),true);
    await page.locator("#discussion-undo").click();
    await page.locator(".settings-changed").waitFor({state:"detached"});
    assert.equal(current.story.world.premise,"外部刚更新的世界");
    assert.equal(JSON.stringify(current.story.scenes),prose);
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
