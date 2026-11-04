const assert=require('node:assert/strict');
const path=require('node:path');
const baseURL=process.env.AINOVEL_STUDIO_URL||'http://127.0.0.1:8002';
async function setup(width=390,height=844){
 assert.equal((await fetch(baseURL+'/health').then(r=>r.json())).provider,'mock','Use an isolated offline model server');
 const {chromium}=require(process.env.AINOVEL_PLAYWRIGHT_PATH||path.resolve('.tmp/lifecycle-browser/node_modules/playwright'));
 const browser=await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const page=await browser.newPage({baseURL,viewport:{width,height},isMobile:width<900,hasTouch:width<900});
 // LAN HTTP, unlike localhost, does not offer secure-context randomUUID.
 await page.addInitScript(()=>Object.defineProperty(Crypto.prototype,'randomUUID',{value:undefined,configurable:true}));
 const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='warning'&&m.text().includes('Failed to resolve component'))errors.push(m.text())});
 await page.goto('/');await page.getByRole('button',{name:'创建小说',exact:true}).waitFor();
 return {browser,page,errors};
}
async function create(page){await page.getByRole('button',{name:'创建小说',exact:true}).click();await page.getByRole('textbox',{name:'聊天消息'}).waitFor();await page.waitForFunction(()=>!document.querySelector('textarea[aria-label="聊天消息"]').readOnly);return new URL(page.url()).hash.split('/')[1]}
async function send(page,text){await page.getByRole('textbox',{name:'聊天消息'}).fill(text);await page.getByRole('button',{name:'发送消息',exact:true}).click();await page.locator('.message.assistant .message-bubble').last().waitFor();await page.waitForFunction(()=>!document.querySelector('.thinking')&&!document.querySelector('textarea[aria-label="聊天消息"]').readOnly)}
async function choose(page,container,label){await container.click();await page.locator('.n-base-select-menu:visible').getByText(label,{exact:true}).click()}
async function removeNovel(id){if(!id)return;const n=await fetch(baseURL+'/api/studio/novels/'+id).then(r=>r.json());if(!n.id||n.busy)return;const d=await fetch(baseURL+'/api/studio/novels/'+id+'/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:n.version,action:'delete'})}).then(r=>r.json());await fetch(baseURL+'/api/studio/novels/'+id+'?version='+d.version,{method:'DELETE'})}
async function closeDrawer(page){await page.keyboard.press('Escape');await page.locator('.n-drawer:visible').waitFor({state:'hidden'})}
module.exports={assert,baseURL,setup,create,send,choose,removeNovel,closeDrawer};
