const assert = require("node:assert/strict");
const {randomUUID} = require("node:crypto");
const {test} = require("node:test");

test("result records paginate in isolation; hover shows state and cards show full stage data", {skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST}, async () => {
  const {chromium} = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    const stories = await (await page.request.get("/api/stories")).json();
    const source = stories.find(item => item.scene_count > 0);
    const current = await (await page.request.get("/api/stories/"+source.id)).json();
    const story = current.story, scene = story.scenes[0], character = story.characters[0];
    const entry = (name,value) => ({id:randomUUID(),name,value,description:"",reason:"依据正文",children:[]});
    const profile = {entries:[entry("背景","仅在卡片展示的背景")]}, state = {entries:[entry("心理","正在担心来信")]};
    const gen = randomUUID(), confirm = randomUUID(), unrelated = randomUUID();
    scene.source_command_id = confirm; scene.text = character.name + "站在窗边。";
    story.scenes = [scene]; story.chapters = story.chapters.filter(item => item.id === scene.chapter_id);
    current.characters_at_scenes = [{character_id:character.id,scene_id:scene.id,name:character.name,profile,state,aliases:[],custom_fields:[]}];
    const command = (id,kind,extra={}) => ({submission:{id,story_id:story.id,base_revision_id:current.revision_id,payload:{kind,instruction:"继续",...extra}},scene_id:scene.id,status:"completed",undone:false,output:{message:"已完成"}});
    const commands = [command(gen,"generate"),command(confirm,"review_draft",{generation_command_id:gen,decision:"confirm"}), {...command(unrelated,"generate"),scene_id:randomUUID()}];
    const requests=[], errors=[];
    page.on("pageerror",error => errors.push(error.message));
    const events = Array.from({length:101},(_,index) => ({id:index+1,event:{command_id:gen,type:"step.started",created_at:new Date(1700000000000+index).toISOString(),trace:{node:"步骤"+(index+1),summary:"当前场景的过程"}}}));
    const snapshots = [{character_id:character.id,scene_id:scene.id,profile,state,aliases:[{id:randomUUID(),name:"小林",description:"朋友使用的称呼",reason:"彼此熟悉"}],custom_fields:[entry("自定条目","完整自由字段")],experiences:[{id:randomUUID(),scene_id:scene.id,event:"收到来信",keywords:["来信"],entries:[entry("感受","期待")]}]}];
    await page.route("**/favicon.ico",route => route.fulfill({status:204}));
    await page.route("**/api/**",route => {
      assert.equal(route.request().method(),"GET");
      const url = new URL(route.request().url()), path=url.pathname;
      const json = value => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(value)});
      if (path === "/api/stories/"+story.id) return json(current);
      if (path === "/api/stories/"+story.id+"/commands") return json(commands);
      if (path.startsWith("/api/undo/")) return json({available:false,command_ids:[]});
      if (path.endsWith("/events")) {
        requests.push(path+url.search);
        if (path.includes(gen)) return json(events.filter(item => item.id > Number(url.searchParams.get("after") || 0)).slice(0,100));
        if (path.includes(confirm)) return json([{id:102,event:{command_id:confirm,type:"llm.completed",created_at:new Date(1700000000200).toISOString(),trace:{node:"提交维护",summary:"确认后的记录",usage:{input_tokens:10,output_tokens:5},duration_ms:20,model:"mock",structured_input:"测试输入",structured_output:"测试输出"}}}]);
        return json([{id:103,event:{command_id:unrelated,type:"step.started",trace:{node:"其他场景",summary:"不能混入"}}}]);
      }
      if (path === "/api/revisions/"+current.revision_id+"/characters") return json(url.searchParams.has("scene_id") ? [{...snapshots[0],state:{entries:[]}}] : snapshots);
      return route.continue();
    });
    await page.goto("/#story/"+story.id);
    await page.locator(".scene .process-records").waitFor();
    assert.equal(await page.locator("#tab-log").count(),0);
    assert.equal(await page.locator(".scene .process-records").getAttribute("open"),null);
    assert.equal(requests.length,0);
    const tooltip=await page.locator(".prose .character").first().getAttribute("title");
    assert.match(tooltip,/正在担心来信/); assert.doesNotMatch(tooltip,/仅在卡片展示/);
    await page.locator(".scene .process-records > summary").click();
    await page.waitForFunction(() => document.querySelector(".scene .process-body")?.textContent.includes("确认后的记录"));
    const logs=await page.locator(".scene .process-body").textContent();
    assert.match(logs,/步骤101/); assert.doesNotMatch(logs,/不能混入/);
    assert.ok(requests.some(path => path.includes("after=100")));
    await page.reload();
    await page.locator(".scene .process-records > summary").click();
    await page.waitForFunction(() => document.querySelector(".scene .process-body")?.textContent.includes("步骤101"));
    await page.goto("/characters?story="+story.id);
    await page.locator(".character-card").waitFor();
    const card=await page.locator(".character-card").first().textContent();
    for (const text of ["仅在卡片展示的背景","正在担心来信","朋友使用的称呼","彼此熟悉","完整自由字段","收到来信"]) assert.ok(card.includes(text),text);
    await page.selectOption("#card-stage",scene.id);
    await page.waitForFunction(() => !document.querySelector(".character-card")?.textContent.includes("正在担心来信"));
    assert.match(await page.locator(".character-card").first().textContent(),/仅在卡片展示的背景/);
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});
