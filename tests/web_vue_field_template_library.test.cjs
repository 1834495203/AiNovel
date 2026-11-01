/* Direct template editing. Every API write is intercepted with synthetic data. */
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const { test } = require('node:test');
const enabled = !!process.env.AINOVEL_BROWSER_TEST;
const field = name => ({ id: randomUUID(), name, type: 'text', description: '填写说明', required: false, allow_extra: true, options: [], children: [], item: null });
const suite = (name, fields, builtin = false) => ({ id: randomUUID(), name, description: '', builtin, parent_id: null, replaces_id: null,
  templates: [{ id: randomUUID(), name: '世界观', scope: 'world', fields, builtin: false, description: '', parent_id: null }] });
async function switchTemplate(page, name, mobile) {
  if (mobile) {
    await page.getByLabel('切换模板', { exact: true }).click();
    await page.locator('.mobile-template-switch input').fill(name);
    await page.locator('.n-base-select-option').filter({ hasText: new RegExp(`^${name}$`) }).click();
  } else await page.locator('.template-item').filter({ hasText: name }).click();
}
async function openBrowser() {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || 'playwright');
  return chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
}
async function mock(page, library, write) {
  const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  await page.route('**/favicon.ico', route => route.fulfill({ status: 204 }));
  await page.route('**/health', route => json(route, { database: true, redis: true, worker: true, provider: 'mock', model: 'synthetic' }));
  await page.route('**/api/**', route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname;
    assert.ok(!endpoint.startsWith('/api/stories/'), '模板页不需要读取任何小说');
    if (request.method() === 'GET') {
      if (endpoint === '/api/field-templates') return json(route, library);
      if (endpoint === '/api/stories' || endpoint === '/api/drafts') return json(route, []);
      throw new Error(`Unexpected GET: ${endpoint}`);
    }
    assert.equal(request.method(), 'POST'); assert.equal(endpoint, '/api/commands');
    return write(route, request.postDataJSON(), json);
  });
}

test('long template lists and inline fields scroll while save remains visible', { skip: !enabled }, async () => {
  const browser = await openBrowser();
  try {
    for (const viewport of [{ width: 1280, height: 650 }, { width: 390, height: 700 }]) {
      const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || 'http://127.0.0.1:8000', viewport });
      const library = { session_id: randomUUID(), templates: [], template_sets: Array.from({ length: 12 }, (_, index) => suite(`长模板 ${index + 1}`, Array.from({ length: 16 }, (_, n) => field(`字段 ${n + 1}`)))) };
      await mock(page, library, () => { throw new Error('切换不应提交写操作'); });
      await page.goto('/#field-templates');
      await switchTemplate(page, '长模板 12', viewport.width < 700);
      assert.equal(await page.getByRole('textbox', { name: '模板名称', exact: true }).inputValue(), '长模板 12');
      assert.equal(await page.locator('.n-drawer').count(), 0, '字段直接展示，不需要抽屉');
      const body = page.locator('.editor-body');
      await body.evaluate(element => { element.scrollTop = element.scrollHeight; });
      assert.ok(await body.evaluate(element => element.scrollTop > 0));
      await page.getByRole('button', { name: '保存', exact: true }).waitFor({ state: 'visible' });
      await page.getByRole('button', { name: '新增模板', exact: true }).waitFor({ state: 'visible' });
      assert.equal(await page.evaluate(() => document.scrollingElement.scrollTop), 0);
      assert.ok(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth + 1));
      await page.close();
    }
  } finally { await browser.close(); }
});

test('direct edit, switching, new template, failed saves and refresh preserve inputs', { skip: !enabled }, async () => {
  const browser = await openBrowser();
  try {
    for (const viewport of [{ width: 1280, height: 850 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || 'http://127.0.0.1:8000', viewport });
      const original = suite('默认小说模板', [field('世界规则')], true);
      original.templates[0].fields[0].description = '填写 世界规则；可在自定义模板中修改解释。';
      const canonical = structuredClone(original.templates[0]);
      canonical.builtin = true;
      canonical.fields[0].description = '说明这个世界独有的规则及其限制。';
      const library = { session_id: randomUUID(), templates: [canonical], template_sets: [original, suite('另一个模板', [field('城市规则')])] };
      const writes = [], errors = [];
      let rejectNext = false;
      let rejectOldApi = true;
      page.on('pageerror', error => errors.push(error.message));
      await mock(page, library, (route, submission, json) => {
        writes.push(submission);
        assert.equal(submission.payload.kind, 'save_field_template_set');
        assert.equal(submission.story_id, null); assert.equal(submission.session_id, library.session_id);
        if (rejectOldApi) { rejectOldApi = false; return json(route, { detail: [{ type: 'extra_forbidden', loc: ['body', 'payload', 'save_field_template_set', 'replace_template_set_id'], msg: 'Extra inputs are not permitted' }] }, 422); }
        if (rejectNext) { rejectNext = false; return json(route, { detail: '模拟保存失败，输入保留' }, 409); }
        const payload = submission.payload;
        const saved = { ...structuredClone(payload.template_set), replaces_id: payload.replace_template_set_id };
        if (payload.replace_template_set_id) {
          const index = library.template_sets.findIndex(item => item.id === payload.replace_template_set_id);
          assert.ok(index >= 0); library.template_sets[index] = saved;
        } else library.template_sets.push(saved);
        return json(route, { submission, status: 'completed', output: { field_template_set: saved, message: '已保存' }, undone: false });
      });
      await page.goto('/#field-templates');
      const fieldName = () => page.getByRole('textbox', { name: '字段名称', exact: true });
      await fieldName().fill('施法代价');
      await page.getByRole('button', { name: '展开字段 施法代价', exact: true }).click();
      assert.equal(await page.getByRole('textbox', { name: '字段解释', exact: true }).inputValue(), canonical.fields[0].description, '旧占位说明显示有效默认解释');
      await page.getByRole('tab', { name: /^章节/ }).click();
      await page.getByRole('button', { name: '添加字段', exact: true }).click();
      await fieldName().fill('章节目标');
      await page.getByRole('tab', { name: /^世界观/ }).click();
      assert.equal(await fieldName().inputValue(), '施法代价');
      await switchTemplate(page, '另一个模板', viewport.width < 700);
      assert.equal(await fieldName().inputValue(), '城市规则');
      await page.getByRole('button', { name: '展开字段 城市规则', exact: true }).click();
      assert.equal(await page.getByRole('textbox', { name: '字段解释', exact: true }).inputValue(), '填写说明', '自定义解释保留原文');
      await switchTemplate(page, '默认小说模板', viewport.width < 700);
      assert.equal(await fieldName().inputValue(), '施法代价'); assert.equal(writes.length, 0);
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'API 版本过旧' }).waitFor();
      assert.equal(await fieldName().inputValue(), '施法代价', '接口校验失败后保留输入');
      assert.ok(!(await page.getByRole('alert').innerText()).includes('[object Object]'));
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.save-status').textContent.includes('已保存'));
      assert.equal(writes[0].payload.replace_template_set_id, original.id);
      assert.equal(library.template_sets.length, 2, '直接保存不会增加复制品');
      assert.equal(original.templates[0].fields[0].name, '世界规则', '保留旧版本内容');
      assert.equal(await page.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
      await page.screenshot({ path: path.resolve(__dirname, `../.tmp/template-editor-${viewport.width < 700 ? 'mobile' : 'desktop'}.png`) });
      await page.getByRole('button', { name: '新增模板', exact: true }).click();
      await page.getByRole('textbox', { name: '模板名称', exact: true }).fill('我的模板');
      await page.getByRole('button', { name: '添加字段', exact: true }).click();
      await fieldName().fill('门派制度');
      await switchTemplate(page, '另一个模板', viewport.width < 700);
      await switchTemplate(page, '我的模板', viewport.width < 700);
      assert.equal(await fieldName().inputValue(), '门派制度');
      await page.reload();
      await fieldName().waitFor();
      assert.equal(await fieldName().inputValue(), '门派制度', '刷新后恢复未保存输入');
      rejectNext = true;
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: '模拟保存失败' }).waitFor();
      assert.equal(await fieldName().inputValue(), '门派制度');
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.save-status').textContent.includes('已保存'));
      assert.equal(library.template_sets.length, 3);
      assert.equal(writes.at(-1).payload.replace_template_set_id, null);
      await page.reload();
      await fieldName().waitFor();
      assert.equal(await fieldName().inputValue(), '门派制度');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});

test('nested fields collapse individually and in bulk without losing edits', { skip: !enabled }, async () => {
  const browser = await openBrowser();
  try {
    for (const viewport of [{ width: 1280, height: 850 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ baseURL: process.env.AINOVEL_TEST_URL || 'http://127.0.0.1:8000', viewport });
      const weapon = field('武器');
      const equipment = { ...field('装备'), type: 'object', children: [weapon] };
      const experience = { ...field('经历'), type: 'list', item: { ...field('经历项目'), type: 'object', children: [field('事件'), field('年份')] } };
      const source = suite('嵌套模板', [{ ...field('人物'), type: 'object', children: [field('姓名'), equipment, experience] }]);
      const library = { session_id: randomUUID(), templates: [], template_sets: [source] };
      const writes = [], errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await mock(page, library, (route, submission, json) => {
        writes.push(submission.payload);
        const saved = { ...structuredClone(submission.payload.template_set), replaces_id: submission.payload.replace_template_set_id };
        library.template_sets = [saved];
        return json(route, { submission, status: 'completed', output: { field_template_set: saved }, undone: false });
      });
      await page.goto('/#field-templates');
      await page.getByRole('button', { name: '展开字段 人物', exact: true }).waitFor();
      assert.equal(await page.locator('.definition-toggle[aria-expanded="true"]').count(), 0, '所有层级默认收起');
      assert.equal(await page.getByRole('textbox', { name: '字段名称', exact: true }).count(), 1);
      await page.getByRole('button', { name: '展开字段 人物', exact: true }).click();
      await page.getByRole('button', { name: '展开字段 装备', exact: true }).click();
      await page.getByRole('button', { name: '展开字段 武器', exact: true }).click();
      const weaponRow = page.locator('.definition').filter({ has: page.getByRole('button', { name: '收起字段 武器', exact: true }) }).last();
      await weaponRow.getByRole('textbox', { name: '字段解释', exact: true }).fill('修改后的武器说明');
      await page.getByRole('button', { name: '收起字段 人物', exact: true }).click();
      assert.equal(await page.getByRole('textbox', { name: '字段名称', exact: true }).count(), 1);
      await page.getByRole('button', { name: '展开字段 人物', exact: true }).click();
      assert.equal(await weaponRow.getByRole('textbox', { name: '字段解释', exact: true }).inputValue(), '修改后的武器说明');
      await page.getByRole('button', { name: '全部收起', exact: true }).click();
      assert.equal(await page.getByRole('textbox', { name: '字段名称', exact: true }).count(), 1);
      await page.getByRole('button', { name: '全部展开', exact: true }).click();
      assert.equal(await page.getByRole('textbox', { name: '字段名称', exact: true }).count(), 8);
      assert.equal(await page.locator('.definition-toggle[aria-expanded="true"]').count(), 8);
      await page.getByRole('tab', { name: /^章节/ }).click();
      await page.getByRole('tab', { name: /^世界观/ }).click();
      assert.equal(await page.locator('.definition-toggle[aria-expanded="true"]').count(), 8, '切换类别后保留展开状态');
      assert.equal(writes.length, 0, '展开收起不提交业务操作');
      assert.ok(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth + 1), '多层嵌套不撑宽手机页面');
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.save-status').textContent.includes('已保存'));
      assert.equal(await page.locator('.definition-toggle[aria-expanded="true"]').count(), 8, '保存后保留展开状态');
      const savedWorld = writes[0].template_set.templates.find(item => item.scope === 'world');
      assert.equal(savedWorld.fields[0].children[1].children[0].description, '修改后的武器说明');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
});
