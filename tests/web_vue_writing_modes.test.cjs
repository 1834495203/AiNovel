const {test}=require('node:test');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
test('planning has independent scene board, multiple sessions and a separate writing page',{skip:!process.env.AINOVEL_BROWSER_TEST},async()=>{
 const {chromium}=require(process.env.AINOVEL_PLAYWRIGHT_PATH||'playwright');
 const browser=await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE||undefined});
 try {
 const page=await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL||'http://127.0.0.1:8000'});
 await page.addInitScript(()=>localStorage.setItem('ainovel.storageEpoch','empty-fields-2026-11-02'));
 const id=randomUUID(), revision=randomUUID(), planned=randomUUID(); const errors=[],sessions=[],commands=[];
 page.on('pageerror',e=>errors.push(e.message));
 const story={id,title:'雾港来信',world:{entries:[]},preferences:{entries:[]},writer:{entries:[]},characters:[],chapters:[],scenes:[],directions:[],initial_snapshots:[],custom_fields:[],planned_scenes:[],active_scene_leaf_id:null};
 await page.route('**/health',r=>r.fulfill({json:{database:true,redis:true,worker:true,provider:'mock',model:'mock'}}));
 await page.route('**/api/**',async r=>{
 const path=new URL(r.request().url()).pathname; const json=value=>r.fulfill({json:value});
 if(r.request().method()==='POST') {
  const body=r.request().postDataJSON(),p=body.payload;
  assert.equal(p.mode,'planning');
  const output={};
  if(p.kind==='start_discussion') {const session={id:p.session_id,story_id:id,title:p.title,mode:'planning',message_count:0};sessions.push(session);output.discussion_session=session;}
  else {assert.equal(p.kind,'discussion'); story.planned_scenes=[{id:planned,title:'雨夜来信',parent_id:null,plan:{scene_plan:'沈砚收到未来的自己寄来的信。',chapter_plan:'雨夜',character_names:[]}}];story.active_scene_leaf_id=planned;output.discussion={session_id:p.session_id,reply:'已保存第一幕。',settings_changed:true};}
  const command={submission:body,status:'completed',output,undone:false};commands.push(command);return json(command);
 }
 if(path==='/api/stories')return json([{id,title:story.title,revision_id:revision,scene_count:0}]);
 if(path===`/api/stories/${id}`)return json({revision_id:revision,story,characters_at_scenes:[]});
 if(path.endsWith('/discussions'))return json(sessions);
 if(path.includes('/discussions/')&&path.endsWith('/commands'))return json(commands.filter(c=>c.submission.payload.kind==='discussion'&&path.includes(c.submission.payload.session_id)));
 if(path.endsWith('/commands'))return json(commands);
 if(path.includes('/runs/'))return json({steps:[],related_commands:[],change_set:{entries:[]}});
 if(path.includes('/undo'))return json({available:false});
 if(path.includes('prompt-templates'))return json({templates:[],bindings:[]});
 return json([]);
 });
 await page.goto(`/#planning/${id}`);
 await page.getByRole('heading',{name:'幕设定',exact:true}).waitFor();
 await page.getByRole('region',{name:'幕列表'}).waitFor();
 await page.getByRole('region',{name:'设定会话'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'写出下一幕'}).count(),0);
 await page.getByRole('button',{name:'新会话',exact:true}).click();
 await page.getByRole('button',{name:'新会话',exact:true}).click();
 assert.equal(sessions.length,2);
 await page.getByRole('textbox',{name:'设定要求'}).fill('先规划第一幕');
 await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.getByText('已保存第一幕。',{exact:true}).waitFor();
 await page.getByRole('button',{name:/雨夜来信/}).click();
 await page.getByRole('button',{name:'在会话中修改'}).waitFor();
 await page.screenshot({path:'.tmp/planning-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'去写正文 →'}).click();
 await page.getByRole('button',{name:'写出下一幕'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'写出下一幕'}).isEnabled(),true);
 assert.equal(await page.getByRole('region',{name:'设定会话'}).count(),0);
 await page.goto(`/#planning/${id}`);await page.setViewportSize({width:390,height:844});
 await page.getByRole('heading',{name:'幕设定',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'.tmp/planning-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 }finally{await browser.close()}
});
