const {test}=require('node:test');
const {assert,setup,create,removeNovel}=require('./studio_browser_helpers.cjs');
test('loading a saved error never raises a banner; a new failure does',{skip:process.env.AINOVEL_STUDIO_BROWSER!=='1'},async()=>{
 const {browser,page,errors}=await setup();let id;
 try{
  id=await create(page);let marker='old-run',error='每步只能调用一个工具';
  await page.route('**/api/studio/novels/'+id,async route=>{
   const response=await route.fetch();const novel=await response.json();
   novel.run_id=marker;novel.error=error;novel.busy=false;
   await route.fulfill({response,json:novel});
  });
  let mutations=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/api/studio/novels/'))mutations++});
  for(let i=0;i<3;i++){
   await page.reload();await page.getByRole('textbox',{name:'聊天消息'}).waitFor();
   assert.equal(await page.locator('.app-error').count(),0);
  }
  assert.equal(mutations,0);
  await page.route('**/api/studio/novels/'+id+'/message',async route=>{
   const response=await route.fetch();const novel=await response.json();
   marker='new-run';error='测试本轮失败';
   await route.fulfill({response,json:{...novel,run_id:marker,error,busy:false}});
  });
  await page.getByRole('textbox',{name:'聊天消息'}).fill('测试新一轮失败的提示');
  await page.getByRole('button',{name:'发送消息',exact:true}).click();
  await page.locator('.app-error').getByText('测试本轮失败',{exact:true}).waitFor();
  assert.equal(mutations,1);
  // An undismissed error also becomes historical after a page reload.
  await page.reload();await page.getByRole('textbox',{name:'聊天消息'}).waitFor();
  assert.equal(await page.locator('.app-error').count(),0);
  assert.equal(mutations,1);assert.deepEqual(errors,[]);
 }finally{await removeNovel(id);await browser.close()}
});
