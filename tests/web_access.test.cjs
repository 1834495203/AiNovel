const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
test('whole-site password login, authenticated API and logout',{skip:process.env.AINOVEL_ACCESS_BROWSER!=='1'},async()=>{
 const {chromium}=require(process.env.AINOVEL_PLAYWRIGHT_PATH||path.resolve('.tmp/lifecycle-browser/node_modules/playwright'));
 const baseURL=process.env.AINOVEL_ACCESS_URL||'http://192.168.137.1:8000';
 for(let attempt=0;attempt<40;attempt++){try{if((await fetch(baseURL+'/login')).ok)break}catch{}if(attempt===39)throw new Error('Password server did not start');await new Promise(resolve=>setTimeout(resolve,150))}
 const password=fs.readFileSync(process.env.AINOVEL_ACCESS_PASSWORD_FILE||'.tmp/access-password-once.txt','utf8').trim();
 const browser=await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const page=await browser.newPage({baseURL,viewport:{width:1200,height:850}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  const denied=await page.request.get('/api/studio/novels');assert.equal(denied.status(),401);
  await page.goto('/');await page.getByRole('heading',{name:'请输入访问密码',exact:true}).waitFor();
  await page.screenshot({path:'.tmp/access-login.png',fullPage:true});
  await page.getByLabel('访问密码',{exact:true}).fill('incorrect-password');
  await page.getByRole('button',{name:'进入工作区',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'密码不正确'}).waitFor();
  await page.getByLabel('访问密码',{exact:true}).fill(password);
  await page.getByLabel('显示密码',{exact:true}).check();
  assert.equal(await page.getByLabel('访问密码',{exact:true}).getAttribute('type'),'text');
  await page.getByLabel('显示密码',{exact:true}).uncheck();
  await page.getByRole('button',{name:'进入工作区',exact:true}).click();
  await page.locator('.header-actions').getByRole('button',{name:'字段模板',exact:true}).waitFor();
  assert.equal((await page.request.get('/api/studio/novels')).status(),200);
  const cookies=await page.context().cookies();const session=cookies.find(c=>c.name==='ainovel_access');
  assert.ok(session&&session.httpOnly&&session.sameSite==='Strict');
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await page.getByRole('heading',{name:'请输入访问密码',exact:true}).waitFor();
  assert.equal((await page.request.get('/api/studio/novels')).status(),401);
  assert.deepEqual(errors,[]);
 }finally{await browser.close()}
});
