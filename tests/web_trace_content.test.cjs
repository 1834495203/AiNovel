const assert = require("node:assert/strict");
const {test} = require("node:test");

test("node records decode content, omit empty values, show tools and returned reasoning", {skip:!process.env.AINOVEL_LEGACY_BROWSER_TEST}, async () => {
  const {chromium} = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({headless:true,executablePath:process.env.AINOVEL_BROWSER_EXECUTABLE || undefined});
  try {
    const page=await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    await page.goto("/characters");
    await page.evaluate(async () => {
      const {ProcessRecords}=await import("/assets/process-records.js");
      const {nodeGroups}=await import("/assets/trace-content.js");
      const output={use_retrieval:false,draft_version:0,reason:"直接衔接上一幕",empty:{},unused:[],absent:null,reply:"<img src=x onerror=alert(1)>"};
      const request={messages:[{role:"system",content:"判断创作路径"},{role:"user",content:JSON.stringify({instruction:"继续",extra:{}})}],tools:[{function:{name:"submit_Intent",parameters:{$defs:{secret:"不应默认展示的格式规范"}}}}]};
      const trace={node:"Intent",summary:"判断路径",structured_input:JSON.stringify(request),structured_output:JSON.stringify(output),raw_output:JSON.stringify({choices:[{message:{content:null,reasoning_content:"供应商返回的推理文字",tool_calls:[{function:{name:"submit_Intent",arguments:JSON.stringify(output)}}]}}]}),model:"mock",usage:{input_tokens:12,output_tokens:8},duration_ms:30};
      const events=[{command_id:"a",type:"step.started",trace:{node:"analyze",summary:"分析需求",structured_input:JSON.stringify({instruction:"继续",extra:{}})}},{command_id:"a",type:"llm.completed",trace},{command_id:"a",type:"step.completed",trace:{node:"analyze",summary:"完成",structured_output:JSON.stringify({intent:output})}}];
      const process=new ProcessRecords({});
      const host=document.createElement("div");host.id="trace-test";document.body.append(host);
      nodeGroups(events).forEach(group => host.append(process.row(group)));
      host.querySelector(".process-node").open=true;
      const empty=process.row(nodeGroups([{command_id:"b",type:"llm.completed",trace:{node:"空结果",summary:"无内容",model:"mock",structured_input:'"{}"',structured_output:"{}",raw_output:"{}"}}])[0]);
      empty.id="empty-trace";empty.open=true;host.append(empty);
      const malformed=process.row(nodeGroups([{command_id:"c",type:"llm.failed",trace:{node:"异常结果",summary:"输出校验失败",model:"mock",error:"工具参数解析失败",structured_output:'{"unfinished":',raw_output:JSON.stringify({choices:[{message:{tool_calls:[{function:{name:"submit_Plan",arguments:'{"unfinished":'}}]}}]})}}])[0]);
      malformed.id="malformed-trace";malformed.open=true;host.append(malformed);
    });
    const node=page.locator("#trace-test .process-node").first();
    assert.match(await node.innerText(),/节点输入/); assert.match(await node.innerText(),/节点输出/);
    assert.match(await node.innerText(),/供应商返回的推理文字/);
    assert.match(await node.innerText(),/否/); assert.match(await node.innerText(),/0/);
    assert.doesNotMatch(await node.innerText(),/\{\}|\[\]|不应默认展示的格式规范|empty|unused|absent/);
    assert.equal(await node.locator("img").count(),0);
    await node.locator(".trace-call > summary").click();
    assert.match(await node.innerText(),/工具：submit_Intent/); assert.match(await node.innerText(),/提交参数/);
    assert.doesNotMatch(await node.innerText(),/不应默认展示的格式规范/);
    assert.equal(await page.locator("#empty-trace .trace-section").count(),0);
    assert.doesNotMatch(await page.locator("#empty-trace").innerText(),/\{\}|\[\]/);
    assert.equal(await page.locator("#malformed-trace .trace-section").count(),0);
    await page.locator("#malformed-trace .trace-call > summary").click();
    assert.match(await page.locator("#malformed-trace").innerText(),/未能解析/);
    assert.doesNotMatch(await page.locator("#malformed-trace").innerText(),/unfinished/);
  } finally { await browser.close(); }
});
