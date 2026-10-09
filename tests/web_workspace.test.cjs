/* Current SPA contracts. Every write stays inside this synthetic in-memory API. */
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const enabled = !!process.env.AINOVEL_BROWSER_TEST;

async function fixture() {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || 'playwright');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE });
  const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || 'http://127.0.0.1:8000', viewport: { width: 1440, height: 1000 } });
  const id = Object.fromEntries(['story','revision','prior','c1','c2','s1','s2','g1','g2','r1','r2','session'].map(key => [key, randomUUID()]));
  const view = { revision_id: id.revision, characters_at_scenes: [], story: { id: id.story, title: '合成工作区', world: { premise: '合成世界', entries: [] }, preferences: { likes: [], dislikes: [], custom_fields: [] }, writer: { mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: '', current_user_intent: '', custom_fields: [] }, characters: [], directions: [], custom_fields: [], initial_snapshots: [], chapters: [{id:id.c1,title:'第一章 · 信件'},{id:id.c2,title:'第二章 · 追踪'}], scenes: [{ id:id.s1, chapter_id:id.c1, title:'第一幕 · 收信', source_command_id:id.r1, text:'雨水落在信封上。\n\n'.repeat(80) },{id:id.s2,chapter_id:id.c2,title:'第二幕 · 出发',source_command_id:id.r2,text:'清晨她离开旧港。\n\n'.repeat(80)}] } };
  const draft = (n,status='confirmed') => ({ generation_command_id: id[`g${n}`], version:1,status,content:{ title:`第${n}幕草稿`,text:`正文${n}`,summary:`结果${n}` } });
  const command = (cid,payload,output={}) => ({ submission:{id:cid,story_id:id.story,base_revision_id:id.revision,session_id:null,actor:'user',payload},status:'completed',output:{message:'操作已完成',...output},undone:false,error:null });
  const commands = [command(id.g1,{kind:'generate',instruction:'第一章写一封信'}, {writing_draft:draft(1)}), command(id.r1,{kind:'review_draft',generation_command_id:id.g1,expected_draft_version:1,decision:'confirm',feedback:''}), command(id.g2,{kind:'generate',instruction:'第二章追踪线索'}, {writing_draft:draft(2)}), command(id.r2,{kind:'review_draft',generation_command_id:id.g2,expected_draft_version:1,decision:'confirm',feedback:''})];
  // Historical scenes may predate source_command_id, but retain checkpoint scene IDs.
  view.story.scenes[1].source_command_id = null;
  commands[2].scene_id = view.story.scenes[1].id;
  commands[3].scene_id = view.story.scenes[1].id;
  const stack = [...commands.map(c => c.submission.id)];
  const writes=[], errors=[];
  let rejects = 0;
  const json = (route,value,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  const overview = cmd => ({ command:cmd,related_commands:[],steps:[{event_id:1,command_id:cmd.submission.id,node:'write',label:'生成正文草稿',status:'completed',explanation:'根据本幕安排写作',structured_input:JSON.stringify({messages:[{role:'system',content:'禁止重复展示的系统提示词'}]}),structured_output:JSON.stringify({summary:'本幕收到了信',plan:{goal:'推进线索'}}),raw_output:'{}',attempts:[{input_tokens:100,output_tokens:20,duration_ms:1000,estimated_usage:false},{input_tokens:10,output_tokens:5,duration_ms:500,estimated_usage:false}],input_tokens:110,output_tokens:25,duration_ms:1500}],change_set:{text_changed:true,entries:[{category:'剧情计划',path:'笔记',before:'',after:JSON.stringify({name:'信件',value:'信已收到'})}],base_revision_id:id.prior,result_revision_id:view.revision_id} });
  const journal = () => { const cid=stack.at(-1);return {total:commands.length,latest_command_id:cid,latest:cid?{command_id:cid,operation_id:stack.length,scope_id:id.story,story_id:id.story,revision_id:view.revision_id}:null,items:[...commands].reverse().map(c=>({command:c,scope_id:id.story,story_title:view.story.title,created_at:'2026-10-08T10:00:00Z',operation_id:c.submission.id===cid?stack.length:null}))} };
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/health',r=>json(r,{database:true,redis:true,worker:true,provider:'mock',model:'fixture'}));
  await page.route('**/favicon.ico',r=>r.fulfill({status:204}));
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(request.method()==='GET') {
      if(path==='/api/stories')return json(route,[{id:id.story,title:view.story.title,revision_id:view.revision_id,scene_count:2}]);
      if(path==='/api/drafts')return json(route,[]);
      if(path===`/api/stories/${id.story}`)return json(route,view);
      if(path===`/api/stories/${id.story}/commands`)return json(route,commands);
      if(path==='/api/history')return json(route,journal());
      if(path.startsWith('/api/runs/'))return json(route,overview(commands.find(c=>c.submission.id===path.split('/').at(-1))||commands[0]));
      if(path.startsWith('/api/commands/'))return json(route,commands.find(c=>c.submission.id===path.split('/').at(-1)));
      if(path===`/api/stories/${id.story}/discussions`)return json(route,Array.from({length:30},(_,i)=>({id:i===0?id.session:randomUUID(),story_id:id.story,title:`讨论会话 ${i}`,message_count:30})));
      if(path.includes('/discussions/')&&path.endsWith('/commands'))return json(route,Array.from({length:25},(_,i)=>command(randomUUID(),{kind:'discussion',session_id:id.session,message:`讨论问题 ${i}`},{discussion:{reply:'推敲故事走向。'.repeat(25),settings_changed:false}})));
      return json(route,{detail:`Unexpected GET ${path}`},404);
    }
    const body=request.postDataJSON();writes.push(body);
    if(path!=='/api/commands')return json(route,{detail:'Unexpected write'},400);
    const p=body.payload;
    if(p.kind==='undo') {
      assert.equal(p.target_command_id,stack.at(-1));assert.equal(p.expected_operation_id,stack.length);
      const removed=stack.pop();const old=commands.find(c=>c.submission.id===removed);old.undone=true;
      view.revision_id=id.prior;
      return json(route,{...command(body.id,p),submission:body,output:{message:'已撤回最新操作'}});
    }
    if(rejects>0){rejects--;return json(route,{detail:'模拟提交冲突，请重试'},409)}
    const next={...command(body.id,p,{writing_draft:{...draft(2,'pending'),version:2,content:{title:'新草稿',text:'新正文',summary:'新的结果'}}}),submission:body,status:'waiting'};
    commands.push(next);stack.push(body.id);return json(route,next);
  });
  return {browser,page,id,view,commands,writes,errors,rejectNext:()=>{rejects=1}};
}

test('independent directory, novel, and creation record panes preserve associations and SPA navigation', {skip:!enabled}, async()=>{
  const f=await fixture();try{
    const {page,id}=f;
    await page.goto(`/#story/${id.story}`);await page.locator('.novel-pane').waitFor();
    assert.equal(await page.locator('.workspace-pane').count(),1);
    await page.getByRole('button',{name:'目录',exact:true}).click();await page.getByRole('button',{name:'创作记录',exact:true}).click();
    const boxes=await Promise.all(['.directory-pane','.novel-pane','.records-pane'].map(s=>page.locator(s).boundingBox()));
    assert.ok(boxes[0].x<boxes[1].x&&boxes[1].x<boxes[2].x);
    await page.getByRole('button',{name:'第二幕 · 出发',exact:true}).click();
    await page.locator('.records-pane').getByText('第二章 · 追踪 / 第二幕 · 出发',{exact:false}).first().waitFor();
    assert.equal(await page.locator('.records-pane').getByText('第一章写一封信',{exact:true}).count(),0);
    assert.equal(await page.locator('.records-pane').getByText('第二章追踪线索',{exact:true}).count(),1);
    await page.getByRole('button',{name:'关闭小说'}).click();assert.equal(await page.locator('.novel-pane').count(),0);
    await page.getByRole('button',{name:'关闭目录'}).click();assert.equal(await page.locator('.workspace-pane').count(),1);
    await page.getByRole('button',{name:'小说',exact:true}).click();
    await page.locator('.scene-run-summary').first().getByText('135 tokens',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:/撤回/}).count(),0);
    assert.equal(await page.locator('.reader-process').count(),0);
    assert.equal(await page.getByText('禁止重复展示的系统提示词').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight-window.innerHeight),0);
    const marker=randomUUID();await page.evaluate(v=>window.workspaceMarker=v,marker);
    await page.locator('.project-rail').getByRole('button',{name:'故事讨论',exact:true}).click();await page.locator('.discussion-workspace').waitFor();
    assert.equal(await page.evaluate(()=>window.workspaceMarker),marker);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight-window.innerHeight),0);
    assert.ok(await page.locator('.discussion-scroll .n-scrollbar-container').evaluate(e=>e.scrollHeight>e.clientHeight));
    assert.ok(await page.locator('.session-scroll .n-scrollbar-container').evaluate(e=>e.scrollHeight>e.clientHeight));
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(f.errors,[]);assert.equal(f.writes.length,0);
  }finally{await f.browser.close()}
});

test('central history offers only latest undo and regenerates edited requests after compensation', {skip:!enabled}, async()=>{
  const f=await fixture();try{
    const {page,id}=f;
    await page.goto(`/#history?scope=${id.story}`);await page.getByRole('button',{name:'撤回最新操作',exact:true}).waitFor();
    await page.locator(`[data-command-id="${id.g1}"]`).click();assert.equal(await page.getByRole('button',{name:'撤回最新操作',exact:true}).count(),0);
    await page.locator(`[data-command-id="${id.r2}"]`).click();
    await page.getByRole('button',{name:'修改后重新生成',exact:true}).click();await page.getByLabel('重新生成要求').fill('改成克制的心理描写');
    await page.getByRole('button',{name:'撤回并重新生成',exact:true}).click();
    await page.getByText('已保存新的生成结果',{exact:false}).waitFor();
    assert.equal(f.writes.length,2);assert.equal(f.writes[0].payload.kind,'undo');assert.equal(f.writes[0].payload.target_command_id,id.r2);
    assert.equal(f.writes[1].payload.kind,'review_draft');assert.equal(f.writes[1].payload.decision,'revise');assert.equal(f.writes[1].payload.feedback,'改成克制的心理描写');assert.equal(f.writes[1].base_revision_id,id.prior);
    assert.equal(await page.getByText('禁止重复展示的系统提示词').count(),0);
    await page.getByText('结果与决策',{exact:true}).click();await page.getByText('推进线索',{exact:true}).waitFor();
    assert.equal(await page.locator('.journal-detail pre').count(),0);
    assert.deepEqual(f.errors,[]);
  }finally{await f.browser.close()}
});

test('a rejected regeneration submission retains editable input on refresh and never undoes twice', {skip:!enabled}, async()=>{
  const f=await fixture();try{
    const {page,id}=f;f.rejectNext();
    await page.goto(`/#history?scope=${id.story}`);await page.getByRole('button',{name:'修改后重新生成',exact:true}).click();await page.getByLabel('重新生成要求').fill('保留这条修改要求');
    await page.getByRole('button',{name:'撤回并重新生成',exact:true}).click();await page.getByRole('button',{name:'继续提交重新生成',exact:true}).waitFor();
    await page.reload();await page.getByRole('button',{name:'继续提交重新生成',exact:true}).waitFor();assert.equal(await page.getByLabel('重新生成要求').inputValue(),'保留这条修改要求');
    await page.getByRole('button',{name:'继续提交重新生成',exact:true}).click();await page.getByText('已保存新的生成结果',{exact:false}).waitFor();
    assert.equal(f.writes.filter(w=>w.payload.kind==='undo').length,1);
    assert.equal(f.writes.at(-1).payload.feedback,'保留这条修改要求');assert.deepEqual(f.errors,[]);
  }finally{await f.browser.close()}
});


test('novel history identifies scenes and discussion sessions, and materials preserves its own navigation', {skip:!enabled}, async()=>{
  const f=await fixture();try {
    const {page,id,commands}=f;
    const discussionId=randomUUID();
    commands.push({...commands[0],submission:{...commands[0].submission,id:discussionId,payload:{kind:'discussion',session_id:id.session,message:'讨论下一幕'}},output:{discussion:{reply:'讨论摘要',settings_changed:false}}});
    const historyRequests=[];
    page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/history')historyRequests.push(url.searchParams.get('scope_id'))});
    await page.goto(`/#history?scope=${id.story}`);
    await page.locator('.history-breadcrumb').getByText('第二章 · 追踪 / 第二幕 · 出发',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'运行历史',exact:true}).getAttribute('aria-current'),'page');
    await page.getByLabel('选择小说').click();
    assert.equal(await page.getByText('整个项目',{exact:true}).count(),0);
    await page.keyboard.press('Escape');
    await page.locator(`[data-command-id="${discussionId}"]`).click();
    await page.locator('.history-breadcrumb').getByText('讨论会话 0',{exact:true}).waitFor();
    assert.equal(await page.locator('.history-breadcrumb').getByText('讨论会话',{exact:true}).count(),1);
    await page.locator(`[data-command-id="${id.r2}"]`).click();
    await page.locator('.run-changes').waitFor();
    assert.equal(await page.locator('.run-changes').getAttribute('open'),null,'full change contents start collapsed');
    await page.evaluate(()=>window.__spaMarker='keep');
    await page.getByRole('button',{name:'故事资料',exact:true}).click();
    await page.locator('.materials-page .story-materials').waitFor();
    assert.ok(page.url().includes(`#materials/${id.story}`));
    assert.equal(await page.getByRole('button',{name:'故事资料',exact:true}).getAttribute('aria-current'),'page');
    assert.equal(await page.getByRole('button',{name:'小说工作区',exact:true}).getAttribute('aria-current'),null);
    assert.equal(await page.evaluate(()=>window.__spaMarker),'keep');
    assert.ok(historyRequests.length && historyRequests.every(scope=>scope===id.story));
    assert.deepEqual(f.errors,[]);
  }finally{await f.browser.close()}
});
