const {test}=require('node:test');
const {assert,setup,create,send,removeNovel}=require('./studio_browser_helpers.cjs');
test('phone last user turn retries, edits and withdraws its tool changes',{skip:process.env.AINOVEL_STUDIO_BROWSER!=='1'},async()=>{
 const {browser,page,errors}=await setup();let id;
 try{
  id=await create(page);await send(page,'这一版是灯塔故事。');
  const read=async()=>await (await page.request.get('/api/studio/novels/'+id)).json();
  const first=await read();const firstId=first.turn_checkpoint.message_id;
  await page.getByRole('button',{name:'重试本轮',exact:true}).click();
  await page.getByRole('button',{name:'恢复并重试',exact:true}).click();
  await page.waitForFunction(old=>document.querySelector('.message.user')?.dataset.messageId!==old,firstId);
  await page.getByRole('button',{name:'修改消息',exact:true}).waitFor();
  const retry=await read();assert.equal(retry.world.length,1);assert.equal(retry.creation_messages.filter(m=>m.role==='user').length,1);assert.ok(retry.calls>first.calls);
  await page.getByRole('button',{name:'修改消息',exact:true}).click();
  await page.getByRole('textbox',{name:'修改消息内容'}).fill('换成山村故事。');
  await page.getByRole('button',{name:'保存并重新运行',exact:true}).click();
  await page.getByRole('textbox',{name:'修改消息内容'}).waitFor({state:'hidden'});
  await page.getByRole('button',{name:'撤回本轮',exact:true}).waitFor();
  const edited=await read();assert.ok(!JSON.stringify(edited).includes('灯塔故事'));assert.ok(JSON.stringify(edited.world).includes('山村故事'));
  await page.screenshot({path:'.tmp/mobile-last-turn-actions.png'});
  await page.getByRole('button',{name:'撤回本轮',exact:true}).click();
  await page.getByRole('button',{name:'撤回对话及操作',exact:true}).click();
  await page.getByRole('heading',{name:'想写一个怎样的故事？'}).waitFor();
  const undone=await read();assert.deepEqual(undone.world,[]);assert.deepEqual(undone.creation_messages,[]);
  assert.equal(await page.getByRole('textbox',{name:'聊天消息'}).inputValue(),'换成山村故事。');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  assert.deepEqual(errors,[]);
 }finally{await removeNovel(id);await browser.close()}
});
