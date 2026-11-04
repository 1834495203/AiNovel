const {test}=require('node:test');const assert=require('node:assert/strict');const {readFileSync}=require('node:fs');const {randomUUID}=require('node:crypto');
test('frontend edits, previews and saves behavior and context separately from schema',{skip:!process.env.AINOVEL_BROWSER_TEST},async()=>{
 const {chromium}=require(process.env.AINOVEL_PLAYWRIGHT_PATH||'playwright');
 const browser=await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE||undefined});
 try {
 const page=await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL||'http://127.0.0.1:8000'});
 const fixture=JSON.parse(readFileSync('.tmp/prompt-context-fixture.json','utf8'));let previewRequest,savedRequest;const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/health',r=>r.fulfill({json:{database:true,redis:true,worker:true,provider:'mock',model:'mock'}}));
 await page.route('**/api/**',async r=>{
  const path=new URL(r.request().url()).pathname;const json=x=>r.fulfill({json:x});
  if(path==='/api/prompt-templates/preview'){previewRequest=r.request().postDataJSON();return json({rendered_prompt:'ROLE 港口',strategy_preview:'ROLE 港口',source_hash:'1234567890abcdef',template_path:'prompts/discussion.j2',context_variables:[],context_schema:[],note:'分块预览',rendered_messages:[{role:'system',content:'ROLE 港口'},{role:'user',content:'WORLD 资料'}]});}
  if(path==='/api/commands'){
   const body=r.request().postDataJSON();savedRequest=body.payload;
   assert.equal(savedRequest.kind,'save_prompt_template');
   const row=fixture.library.tasks[0];const v={...row.global_active,id:randomUUID(),version:2,name:savedRequest.name,source:savedRequest.source,editor_source:savedRequest.source,context_source:savedRequest.context_source,builtin:false};row.global_active=v;row.active=v;row.versions=[v];
   return json({submission:body,status:'completed',output:{prompt_template:v,message:'保存'},undone:false});
  }
  if(path==='/api/prompt-templates')return json(fixture.library);
  if(path==='/api/ai-contracts')return json(fixture.catalog);
  return json([]);
 });
 await page.goto('/');await page.getByRole('button',{name:'提示词与上下文',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'提示词模板'});await dialog.waitFor();
 await dialog.getByPlaceholder('自定义模板').fill('验证分离布局');
 await dialog.getByRole('textbox',{name:'行为 Jinja2 源码',exact:true}).fill('ROLE {{ story_title }}');
 await dialog.getByRole('button',{name:'上下文布局',exact:true}).click();
 await dialog.getByRole('textbox',{name:'上下文 Jinja2 源码',exact:true}).fill('WORLD {{ world_context }}');
 await dialog.getByRole('button',{name:'工具与 Schema',exact:true}).click();
 await dialog.getByRole('heading',{name:'参数',exact:true}).waitFor();
 await dialog.getByRole('heading',{name:'返回值',exact:true}).waitFor();
 await dialog.getByRole('button',{name:'渲染预览',exact:true}).click();await dialog.getByText('分块预览',{exact:true}).waitFor();
 assert.equal(previewRequest.context_source,'WORLD {{ world_context }}');assert.equal(previewRequest.source,'ROLE {{ story_title }}');
 await dialog.getByRole('button',{name:'保存新版本',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.template-dialog .n-spin-body'));
 assert.equal(savedRequest.context_source,'WORLD {{ world_context }}');assert.equal(savedRequest.source,'ROLE {{ story_title }}');
 await dialog.getByRole('button',{name:'上下文布局',exact:true}).click();assert.equal(await dialog.getByRole('textbox',{name:'上下文 Jinja2 源码',exact:true}).inputValue(),'WORLD {{ world_context }}');
 assert.deepEqual(errors,[]);
 } finally {await browser.close()}
});
