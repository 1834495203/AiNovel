const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const source = fs.readFileSync(path.resolve(__dirname, '../src/ainovel/frontend/src/api/errors.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(compiled, sandbox);
const { formatApiError } = sandbox.exports;

test('validation errors explain old API fields without exposing submitted content', () => {
  const result = formatApiError({ detail: [{ type: 'extra_forbidden', loc: ['body', 'payload', 'save_field_template_set', 'replace_template_set_id'], msg: 'Extra inputs are not permitted', input: 'private submitted value' }] }, 422);
  assert.match(result, /API 版本过旧/);
  assert.match(result, /重启 API 和 Worker/);
  assert.ok(!result.includes('[object Object]') && !result.includes('private submitted value'));
});

test('nested validation paths and business errors remain readable', () => {
  const result = formatApiError({ detail: [
    { type: 'string_too_short', loc: ['body', 'payload', 'save_field_template_set', 'template_set', 'templates', 2, 'fields', 1, 'name'], msg: 'String should have at least 1 character', ctx: { min_length: 1 } },
    { type: 'value_error', loc: ['body', 'template_set'], msg: 'Value error, 枚举必须有不重复的选项' },
  ] }, 422);
  assert.match(result, /资料类别\[3\].*字段\[2\].*名称：不能为空/);
  assert.match(result, /枚举必须有不重复的选项/);
});

test('string, object and unknown errors always produce readable text', () => {
  assert.equal(formatApiError({ detail: '模板已变化，请刷新' }, 409), '模板已变化，请刷新');
  assert.equal(formatApiError({ detail: { message: '数据库暂时不可用' } }, 503), '数据库暂时不可用');
  assert.match(formatApiError({ detail: {} }, 500), /HTTP 500/);
  assert.match(formatApiError({ detail: [{ type: 'union_tag_invalid', loc: ['body', 'payload'], msg: 'old schema' }] }, 422), /无法识别这个操作/);
});
