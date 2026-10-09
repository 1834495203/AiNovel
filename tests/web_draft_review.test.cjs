/* Browser contract test intercepts every write: it never generates or edits user content. */
const assert = require("node:assert/strict");
const {randomUUID} = require("node:crypto");
const {test} = require("node:test");

test("pending draft survives reload, supports selected feedback, and requires confirmation", {skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST}, async () => {
  const {chromium} = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const baseURL = process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000";
  const browser = await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({baseURL});
    const stories = await (await page.request.get("/api/stories")).json();
    const story = stories.find(item => item.scene_count > 0);
    assert.ok(story,"Requires an existing novel for read-only context");
    let current = await (await page.request.get("/api/stories/"+story.id)).json();
    const baseline = JSON.stringify(current.story);
    const generationId = randomUUID();
    const initialText = "先保留这一句。\n\n🐱林初把信放在桌上。\n\n尾句不会改。";
    let pending = {
      submission:{id:generationId,story_id:story.id,base_revision_id:current.revision_id,payload:{kind:"generate",instruction:"写下一幕"}},
      status:"waiting",undone:false,error:null,
      output:{story_id:story.id,writing_draft:{generation_command_id:generationId,version:1,content:{title:"测试草稿",text:initialText,summary:"草稿摘要"},status:"pending"},message:"草稿已保存，请确认"},
    };
    const submissions = []; const errors = [];
    page.on("pageerror",error => errors.push(error.message));
    await page.route("**/favicon.ico",route => route.fulfill({status:204}));
    await page.route("**/api/**", async route => {
      const request = route.request(); const path = new URL(request.url()).pathname;
      const json = value => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
      if (request.method() === "POST") {
        assert.equal(path,"/api/commands");
        const body = request.postDataJSON(); submissions.push(body);
        assert.equal(body.payload.kind,"review_draft");
        assert.equal(body.payload.expected_draft_version,pending.output.writing_draft.version);
        const result = {submission:body,status:"completed",undone:false,output:structuredClone(pending.output)};
        if (body.payload.decision === "revise") {
          const old = Array.from(pending.output.writing_draft.content.text);
          const range = body.payload.selection;
          if (range) {
            assert.equal(range.selected_text,"🐱林初");
            assert.equal(range.start,Array.from(initialText.slice(0,initialText.indexOf("🐱"))).length);
            pending.output.writing_draft.content.text=old.slice(0,range.start).join("")+"林初犹疑了一瞬"+old.slice(range.end).join("");
          } else pending.output.writing_draft.content.text += "\n\n她保持沉默。";
          pending.output.writing_draft.version += 1;
          result.output = structuredClone(pending.output);
        } else {
          assert.equal(body.payload.decision,"confirm");
          assert.equal(JSON.stringify(current.story),baseline,"Draft feedback must not update canonical UI data");
          current = structuredClone(current); current.revision_id = randomUUID();
          const scene = {id:randomUUID(),chapter_id:current.story.chapters.at(-1).id,title:"已确认草稿",text:pending.output.writing_draft.content.text,summary:"已确认",character_patches:[]};
          current.story.scenes.push(scene);
          result.output.revision_id = current.revision_id;
          result.output.writing_draft.status="confirmed"; pending.status="completed";
        }
        return json(result);
      }
      if (path === "/api/stories/"+story.id) return json(current);
      if (path === "/api/stories/"+story.id+"/commands") return json([pending]);
      if (path.startsWith("/api/undo/")) return json({available:true,operation_id:1,command_ids:[generationId]});
      if (path.endsWith("/events")) return json([]);
      return route.continue();
    });
    await page.goto(baseURL+"/#story/"+story.id);
    await page.locator("#writing-draft:not([hidden])").waitFor();
    assert.equal(await page.locator("#writing-draft .prose").textContent(),initialText);
    await page.reload();
    await page.locator("#writing-draft:not([hidden])").waitFor();
    const prose = page.locator("#writing-draft .prose");
    await prose.evaluate(node => {
      const range = document.createRange(); const start=node.textContent.indexOf("🐱");
      range.setStart(node.firstChild,start); range.setEnd(node.firstChild,start+"🐱林初".length);
      const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      node.dispatchEvent(new MouseEvent("mouseup",{bubbles:true}));
    });
    await page.locator("#rewrite-selection").click();
    await page.locator("#modal-body textarea").fill("让动作更犹疑");
    await page.locator("#modal-body").getByRole("button",{name:"修改并重新预览"}).click();
    await page.locator("#writing-draft h2").filter({hasText:"第 2 版"}).waitFor();
    assert.equal(await page.locator("#writing-draft .prose").textContent(),initialText.replace("🐱林初","林初犹疑了一瞬"));
    await page.locator("#chat-input").fill("再克制一点");
    await page.locator("#chat-form").getByRole("button",{name:"发送"}).click();
    await page.locator("#writing-draft h2").filter({hasText:"第 3 版"}).waitFor();
    await page.locator("#writing-draft").getByRole("button",{name:"确认并提交"}).click();
    await page.locator("#writing-draft[hidden]").waitFor({state:"attached"});
    assert.ok((await page.locator("#novel").textContent()).includes("她保持沉默。"));
    assert.deepEqual(submissions.map(item=>item.payload.decision),["revise","revise","confirm"]);
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});
