/* Complete template-suite UI, isolated static assets and intercepted synthetic writes. */
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { createServer } = require('node:http');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { test } = require('node:test');

test('novel suites are copied, locally editable and exportable on desktop and mobile', {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const root = path.resolve(__dirname, '../src/ainovel/web');
  const server = createServer(async (request, response) => {
    const file = path.resolve(root, request.url === '/' ? 'ui/index.html' : request.url.split('?')[0].slice(1));
    if (!file.startsWith(root + path.sep)) return response.writeHead(404).end();
    try { response.writeHead(200, { 'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' }).end(await readFile(file)); }
    catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || 'playwright');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ baseURL: `http://127.0.0.1:${server.address().port}`, viewport });
      const definition = name => ({ id: randomUUID(), name, description: '字段填写说明', type: 'text', required: false, allow_extra: true, children: [], options: [], item: null });
      const scopes = ['story', 'world', 'character', 'state', 'preferences', 'writer', 'chapter', 'scene'];
      const source = { id: randomUUID(), name: '修仙源模板', description: '合成套装', builtin: false, parent_id: null,
        templates: scopes.map(scope => ({ id: randomUUID(), scope, name: scope, description: '', builtin: false, parent_id: null,
          fields: scope === 'world' ? [definition('世界观简介'), definition('法则')] : scope === 'chapter' ? [definition('本章目标')] : scope === 'scene' ? [definition('发生地点')] : [] })) };
      const original = JSON.stringify(source);
      const library = { session_id: randomUUID(), templates: source.templates.map(item => ({ ...item, builtin: true })), template_sets: [source] };
      const draftId = randomUUID(), storyId = randomUUID();
      const instantiate = field => ({ id: randomUUID(), name: field.name, value: null, definition: structuredClone(field), children: [], description: '', reason: '' });
      const fields = scope => source.templates.find(item => item.scope === scope).fields.map(instantiate);
      const story = { id: storyId, title: '套装合成小说', world: { premise: '合成世界', entries: fields('world') },
        preferences: { entries: [], likes: [], dislikes: [], custom_fields: [] }, writer: { entries: [], mainlines: [], subplots: [], foreshadows: [], outline: [], future_directions: [], notes: [], drafts: [], progress: '', current_user_intent: '', custom_fields: [] },
        characters: [], chapters: [{ id: randomUUID(), title: '第一章', custom_fields: fields('chapter') }], scenes: [], directions: [], initial_snapshots: [], custom_fields: [], field_template_set: { ...structuredClone(source), id: randomUUID(), parent_id: source.id } };
      story.scenes = [{ id: randomUUID(), chapter_id: story.chapters[0].id, title: '第一幕', text: '合成正文', summary: '摘要', character_patches: [], custom_fields: fields('scene') }];
      const draft = { id: draftId, title: story.title, input_text: '', pending_input: '', editing_setup: false, edits: [], updated_at: new Date().toISOString(), save_event_id: 0, field_template_ids: [], field_template_set_id: null,
        proposal: { title: story.title, world: structuredClone(story.world), characters: [], preferences: structuredClone(story.preferences), custom_fields: [] } };
      let revision = randomUUID();
      const writes = [], errors = [];
      const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/favicon.ico', route => route.fulfill({ status: 204 }));
      await page.route('**/health', route => json(route, { database: true, redis: true, worker: true, provider: 'mock', model: 'synthetic' }));
      await page.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url()), endpoint = url.pathname;
        if (request.method() === 'GET') {
          if (endpoint === '/api/field-templates') return json(route, library);
          if (endpoint === '/api/stories') return json(route, [{ id: storyId, title: story.title, revision_id: revision, scene_count: 1 }]);
          if (endpoint === '/api/drafts') return json(route, [draft]);
          if (endpoint === `/api/drafts/${draftId}`) return json(route, draft);
          if (endpoint === `/api/stories/${storyId}`) return json(route, { story, revision_id: revision, characters_at_scenes: [] });
          if (endpoint.endsWith('/fields')) return json(route, { revision_id: revision, target: { scope: 'world' }, entries: story.world.entries });
          return json(route, []);
        }
        assert.equal(request.method(), 'POST');
        if (endpoint.endsWith('/field-template-set/export')) {
          const input = request.postDataJSON();
          assert.equal(input.revision_id, revision);
          assert.ok(input.targets.some(item => item.scope === 'chapter' && item.entity_id === story.chapters[0].id));
          const result = structuredClone(story.field_template_set);
          result.id = randomUUID(); result.name = input.name; result.builtin = false;
          result.templates.find(item => item.scope === 'world').fields = story.world.entries.map(entry => ({ ...entry.definition, name: entry.name }));
          return json(route, result);
        }
        assert.equal(endpoint, '/api/commands');
        const submission = request.postDataJSON(), payload = submission.payload;
        writes.push(payload);
        if (payload.kind === 'save_field_template_set') {
          assert.equal(submission.session_id, library.session_id);
          library.template_sets.push(structuredClone(payload.template_set));
        } else if (payload.kind === 'save_setup_draft') Object.assign(draft, payload.draft);
        else if (payload.kind === 'create_story') {
          assert.equal(payload.field_template_set_id, library.template_sets[1].id);
          story.field_template_set = { ...structuredClone(library.template_sets[1]), id: randomUUID() };
        } else if (payload.kind === 'update_story_field_schema') {
          story.field_template_set.templates = structuredClone(payload.templates); revision = randomUUID();
        } else if (payload.kind === 'edit_fields') {
          story.world.entries = story.world.entries.filter(item => !payload.patch.remove?.includes(item.id)); revision = randomUUID();
        } else throw new Error(`Unexpected command: ${payload.kind}`);
        return json(route, { submission, status: 'completed', output: { story_id: payload.kind === 'create_story' ? storyId : null, message: '已保存' }, undone: false });
      });
      await page.goto('/#field-templates');
      await page.getByRole('button', { name: '新增模板', exact: true }).click();
      await page.getByRole('textbox', { name: '模板名称', exact: true }).fill('我的独立套装');
      await page.getByRole('tab', { name: /^章节/ }).click();
      await page.getByRole('button', { name: '添加字段', exact: true }).click();
      await page.getByRole('textbox', { name: '字段名称', exact: true }).fill('本章目标');
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.save-status').textContent.includes('已保存'));
      assert.equal(JSON.stringify(source), original);
      await page.goto(`/#draft/${draftId}`);
      await page.getByLabel('小说模板套装', { exact: true }).click();
      await page.locator('.n-base-select-option').filter({ hasText: /^我的独立套装$/ }).click();
      await page.getByRole('button', { name: '确认设定并创建作品', exact: true }).click();
      await page.waitForURL(`**/#story/${storyId}`);
      assert.equal(writes.find(item => item.kind === 'create_story').field_template_set_id, library.template_sets[1].id);
      await page.goto(`/#materials/${storyId}`);
      await page.getByRole('button', { name: '编辑本书初始化结构', exact: true }).click();
      await page.getByRole('tab', { name: /^章节/ }).click();
      await page.getByRole('textbox', { name: '字段名称', exact: true }).fill('本书独有章节字段');
      await page.getByRole('button', { name: '保存本书初始化结构', exact: true }).click();
      await page.locator('.n-drawer').waitFor({ state: 'hidden' });
      assert.equal(story.field_template_set.templates.find(item => item.scope === 'chapter').fields[0].name, '本书独有章节字段');
      assert.equal(library.template_sets[1].templates.find(item => item.scope === 'chapter').fields[0].name, '本章目标');
      const worldEntry = page.locator('.entry-node').filter({ has: page.locator('strong', { hasText: /^法则$/ }) });
      await worldEntry.getByRole('button', { name: '删除', exact: true }).click();
      await worldEntry.waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: '将本书结构导出为新模板', exact: true }).click();
      await page.getByLabel('模板套装名称', { exact: true }).fill('导出的修改版');
      await page.getByRole('button', { name: '另存为新模板套装', exact: true }).click();
      await page.locator('.n-drawer').waitFor({ state: 'hidden' });
      const exported = library.template_sets.at(-1);
      assert.equal(exported.name, '导出的修改版');
      assert.equal(exported.templates.find(item => item.scope === 'world').fields.some(item => item.name === '法则'), false);
      assert.equal(exported.templates.find(item => item.scope === 'chapter').fields[0].name, '本书独有章节字段');
      assert.equal(JSON.stringify(source), original);
      assert.ok(!JSON.stringify(exported).includes('合成正文'));
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
});
