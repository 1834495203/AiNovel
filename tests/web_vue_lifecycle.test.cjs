/* Static SPA + synthetic API only: no user story is read or changed. */
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { createServer } = require('node:http');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { test } = require('node:test');

test('bookshelf draft deletion, recovery and permanent erasure work on desktop and mobile', {
  skip: !process.env.AINOVEL_BROWSER_TEST,
}, async () => {
  const root = path.resolve(__dirname, '../src/ainovel/web');
  const server = createServer(async (request, response) => {
    const relative = request.url === '/' ? 'ui/index.html' : request.url.split('?')[0].slice(1);
    const file = path.resolve(root, relative);
    if (!file.startsWith(root + path.sep)) { response.writeHead(404).end(); return; }
    try {
      const content = await readFile(file);
      const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      response.writeHead(200, { 'Content-Type': type }).end(content);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || 'playwright');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined });
  try {
    for (const viewport of [{ width: 1280, height: 850 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ baseURL: `http://127.0.0.1:${server.address().port}`, viewport });
      const draft = { id: randomUUID(), title: '合成构思草稿', input_text: '草稿', field_template_ids: [] };
      const book = { id: randomUUID(), revision_id: randomUUID(), title: '合成回收站小说', scene_count: 2 };
      const chatSession = randomUUID(), creationSession = randomUUID();
      const writes = [], errors = [];
      let deleted = false, purged = false, bookPurged = false, rejectPurge = true;
      page.on('pageerror', error => errors.push(error.message));
      const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      await page.route('**/favicon.ico', route => route.fulfill({ status: 204 }));
      await page.route('**/health', route => json(route, { database: true, redis: true, worker: true, provider: 'mock', model: 'synthetic' }));
      await page.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url()), endpoint = url.pathname;
        if (request.method() === 'GET') {
          if (endpoint === '/api/stories') return json(route, url.searchParams.get('deleted') === 'true' && !bookPurged ? [book] : []);
          if (endpoint === '/api/drafts') return json(route, !purged && deleted === (url.searchParams.get('deleted') === 'true') ? [draft] : []);
          throw new Error(`Unexpected read: ${endpoint}`);
        }
        writes.push({ method: request.method(), endpoint, body: request.postDataJSON() });
        if (request.method() === 'POST' && endpoint === '/api/commands') {
          const submission = request.postDataJSON();
          assert.equal(submission.payload.kind, 'set_setup_draft_deleted');
          assert.equal(submission.session_id, draft.id);
          deleted = submission.payload.deleted;
          return json(route, { submission, status: 'completed', output: { message: '已处理' }, undone: false });
        }
        assert.equal(request.method(), 'DELETE');
        if (rejectPurge) { rejectPurge = false; return json(route, { detail: '后台任务仍在结束处理中，请稍后重试永久删除' }, 409); }
        if (endpoint === `/api/drafts/${draft.id}`) purged = true;
        else if (endpoint === `/api/stories/${book.id}`) bookPurged = true;
        else throw new Error(`Unexpected deletion: ${endpoint}`);
        return json(route, { command_ids: [], cache_ids: [draft.id, book.id, book.revision_id, chatSession, creationSession] });
      });
      await page.goto('/');
      const card = title => page.locator('.book-card').filter({ has: page.locator('strong', { hasText: title }) });
      await card(draft.title).waitFor();
      page.once('dialog', dialog => dialog.dismiss());
      await card(draft.title).getByRole('button', { name: '删除', exact: true }).click();
      assert.equal(writes.length, 0, '取消删除不提交请求');
      page.once('dialog', dialog => dialog.accept());
      await card(draft.title).getByRole('button', { name: '删除', exact: true }).click();
      await card(draft.title).waitFor({ state: 'hidden' });
      assert.equal(new URL(page.url()).hash, '', '点击删除不会进入创作页面');
      await page.getByRole('button', { name: '回收站', exact: true }).click();
      await card(draft.title).getByRole('button', { name: '恢复', exact: true }).click();
      await page.locator('.book-section').filter({ hasText: '创作草稿' }).getByRole('button', { name: '删除', exact: true }).waitFor();
      page.once('dialog', dialog => dialog.accept());
      await card(draft.title).getByRole('button', { name: '删除', exact: true }).click();
      await card(draft.title).getByRole('button', { name: '永久删除', exact: true }).waitFor();
      page.once('dialog', dialog => dialog.dismiss());
      await card(draft.title).getByRole('button', { name: '永久删除', exact: true }).click();
      assert.equal(writes.filter(write => write.method === 'DELETE').length, 0);
      page.once('dialog', dialog => dialog.accept());
      await card(draft.title).getByRole('button', { name: '永久删除', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '后台任务仍在结束处理中' }).waitFor();
      assert.equal(await card(draft.title).count(), 1, '失败时保留回收站项目');
      await page.evaluate(ids => {
        for (const id of ids) localStorage.setItem(`ainovel.synthetic.${id}`, '关联输入');
        localStorage.setItem('ainovel.synthetic.unrelated', '其他作品');
      }, [draft.id, book.id, book.revision_id, chatSession, creationSession]);
      page.once('dialog', dialog => dialog.accept());
      await card(draft.title).getByRole('button', { name: '永久删除', exact: true }).click();
      await card(draft.title).waitFor({ state: 'hidden' });
      assert.deepEqual(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('ainovel.synthetic.'))), ['ainovel.synthetic.unrelated']);
      page.once('dialog', dialog => dialog.accept());
      await card(book.title).getByRole('button', { name: '永久删除', exact: true }).click();
      await page.getByText('回收站为空', { exact: true }).waitFor();
      await page.getByRole('button', { name: '关闭回收站', exact: true }).click();
      await page.getByRole('button', { name: '回收站', exact: true }).click();
      await page.getByText('回收站为空', { exact: true }).waitFor();
      await page.reload();
      await page.getByRole('button', { name: '回收站', exact: true }).click();
      await page.getByText('回收站为空', { exact: true }).waitFor();
      assert.deepEqual(errors, []);
      assert.equal(writes.filter(write => write.method === 'POST').length, 3);
      await page.close();
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
