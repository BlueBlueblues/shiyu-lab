// 模拟 DOM 与 fetch，验证管理页交互；不连接服务器或模型。
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync('app/static/app.js', 'utf8').replace(
  '}());', 'window.probe = {validateProject, beijingTime, initProjectNewPage, initWorkspacePage, request}; }());');
let passed = 0;
function check(name, ok) { assert.ok(ok, name); console.log('PASS ' + name); passed++; }
vm.runInNewContext(source, {
  document: { readyState: 'complete', body: { classList: { contains: name => name === 'page-landing' } },
    querySelector: () => null },
  window: {},
  fetch() { throw new Error('公开首页不应请求受保护 API'); }
});
check('公开首页启动不请求受保护 API', true);
class Node {
  constructor() { this.children = []; this.hidden = false; this.disabled = false; this.handlers = {}; this.attrs = {}; this.classList = { add() {}, remove() {} }; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(''); }
  get firstChild() { return this.children[0]; }
  get lastChild() { return this.children.at(-1); }
  appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(event, callback) { this.handlers[event] = callback; }
}
function page(detail) {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const requests = [];
  const replies = [];
  const form = new Node();
  form.elements = Object.fromEntries(Object.entries({ name: '测试', idea: '想法', target_users: '用户', skills: '能力', budget_cny: '100', validation_goal: '目标', data_kind: 'test' }).map(([k, value]) => [k, { value }]));
  const document = {
    readyState: 'loading', addEventListener() {},
    getElementById: id => id === 'project-form' ? form : null,
    querySelector: selector => node(/data-testid="([^"]+)"/.exec(selector)[1]),
    querySelectorAll: () => [], createElement: () => new Node(),
    createTextNode: text => { const n = new Node(); n.textContent = text; return n; }
  };
  const sandbox = { document, AbortController, Intl, Date, console, navigator: { clipboard: null },
    window: { setTimeout, clearTimeout, confirm: () => true,
      __FO_WORKSPACE__: detail ? { projectId: detail.project.id, projectStatus: detail.project.status, experimentId: detail.experiment?.id,
        published: Boolean(detail.experiment?.published_at), questions: detail.experiment?.questions || [] } : {},
      location: { pathname: '/projects/p1', reload() { this.reloads = (this.reloads || 0) + 1; } } },
    fetch: async (url, init) => {
      requests.push({ url, init });
      const reply = replies.shift() || { data: url.endsWith('/runs') ? [] : detail };
      if (reply.reject) throw new Error('network');
      return { ok: !reply.error, status: reply.error ? 409 : 200, text: async () => JSON.stringify(reply) };
    }
  };
  vm.runInNewContext(source, sandbox);
  return { ...sandbox, nodes, node, requests, replies, form, api: sandbox.window.probe,
    setFetch(fn) { sandbox.fetch = fn; } };
}
const flush = () => new Promise(resolve => setTimeout(resolve, 20));
const p = page();
const payload = Object.fromEntries(Object.entries(p.form.elements).map(([k, v]) => [k, k === 'budget_cny' ? Number(v.value) : v.value]));
check('项目字段与预算契约', Object.keys(p.api.validateProject(payload)).length === 0);
check('预算拒绝 NaN 和超限', 'budget_cny' in p.api.validateProject({ ...payload, budget_cny: NaN })
  && 'budget_cny' in p.api.validateProject({ ...payload, budget_cny: 100001 }));
check('UTC 转北京时间', p.api.beijingTime('2026-10-11T00:00:00Z').includes('08:00'));
p.api.initProjectNewPage(p.document);
p.form.elements.budget_cny.value = '';
p.form.handlers.submit({ preventDefault() {} });
check('空预算本地拦截', p.requests.length === 0 && !p.node('field-errors').hidden);
p.form.elements.budget_cny.value = '0';
p.replies.push({ data: { project_id: 'new-id', status: 'draft' } });
p.form.handlers.submit({ preventDefault() {} });
await flush();
const created = p.requests[0];
check('创建请求字段和零预算', created.url === '/api/projects' && JSON.parse(created.init.body).budget_cny === 0
  && Object.keys(JSON.parse(created.init.body)).length === 7);
check('管理写请求凭据与请求头', created.init.credentials === 'same-origin'
  && created.init.headers['X-Requested-With'] === 'FirstOrderLab');
check('创建成功跳转真实项目 ID', p.window.location.href === '/projects/new-id');

const detail = { project: { id: 'p1', name: '测试', status: 'planned', data_kind: 'test' },
  plan: null, experiment: { id: 'e1', landing: {}, questions: [], published_at: null }, running_run_id: null };
const ws = page(detail);
ws.api.initWorkspacePage(ws.document);
await flush();
check('服务端首屏只读取运行列表', ws.requests.map(r => r.url).join(',') === '/api/projects/p1/runs');
check('草稿不请求统计与原始回答', ws.requests.every(r => !/statistics|responses/.test(r.url)));
ws.replies.push({ error: { code: 'RUN_IN_PROGRESS' } }, { data: { ...detail, running_run_id: 'running' } }, { data: [] });
ws.node('plan-generate').handlers.click();
check('任务发起时禁用全部写操作', ws.node('plan-generate').disabled && ws.node('experiment-publish').disabled && ws.node('review-generate').disabled);
await flush();
check('生成请求使用正式 API 与空对象', ws.requests[1].url === '/api/projects/p1/plan' && ws.requests[1].init.body === '{}');
check('任务冲突后刷新状态并保持禁用', ws.node('plan-generate').disabled && ws.node('experiment-publish').disabled);
const published = { ...detail, experiment: { ...detail.experiment, published_at: '2026-10-11T00:00:00Z', service_url: 'http://testserver/e/token', survey_url: 'http://testserver/e/token/survey' } };
const pub = page(detail);
pub.api.initWorkspacePage(pub.document);
await flush();
pub.replies.push({ data: { survey_url: published.experiment.survey_url } });
pub.node('experiment-publish').handlers.click();
await flush();
check('发布请求使用正式 API 与空对象', pub.requests[1].url === '/api/experiments/e1/publish' && pub.requests[1].init.body === '{}');
check('发布成功重载正式服务端工作台', pub.window.location.reloads === 1 && pub.node('experiment-publish').disabled);
check('禁用的复盘入口未发出请求', [...ws.requests, ...pub.requests].every(r => !/review|report/.test(r.url)));
const uncertain = page(detail);
uncertain.api.initWorkspacePage(uncertain.document);
await flush();
uncertain.replies.push({ reject: true }, { data: { ...published, project: { ...detail.project, status: 'collecting' } } }, { data: [] });
uncertain.node('experiment-publish').handlers.click();
await flush();
check('请求异常但后端已完成发布时重载真实阶段', uncertain.window.location.reloads === 1 && uncertain.node('experiment-publish').hidden);

// Response-body waits stay inside the deadline after headers have arrived.
const timed = page();
let deadline, cleared = false;
timed.window.setTimeout = (callback, ms) => { deadline = { callback, ms }; return 1; };
timed.window.clearTimeout = () => { cleared = true; };
timed.setFetch(async (_url, init) => ({ ok: true, status: 200,
  text: () => new Promise((_resolve, reject) => init.signal.addEventListener('abort',
    () => reject(Object.assign(new Error('controlled abort'), { name: 'AbortError' })))) }));
const waiting = timed.api.request('GET', '/controlled', undefined, { admin: true }).catch(e => e);
await flush();
check('管理 90 秒期限覆盖响应体读取', deadline.ms === 90000 && !cleared);
deadline.callback();
check('响应体超时映射 TIMEOUT 并清理定时器', (await waiting).code === 'TIMEOUT' && cleared);
const shareUrl = 'http://[2001:db8:1234:5678:abcd:ef01:2345:6789]:8000/e/token/survey';
function copyPage(clipboard) {
  const p = page(published);
  p.navigator.clipboard = clipboard;
  for (const kind of ['service', 'survey']) {
    const target = p.node(kind + '-url');
    target.value = shareUrl + (kind === 'service' ? '?service' : '');
    target.focus = () => { p.document.activeElement = target; };
    target.select = () => { target.selectionStart = 0; target.selectionEnd = target.value.length; };
    target.setSelectionRange = (start, end) => { target.selectionStart = start; target.selectionEnd = end; };
  }
  p.api.initWorkspacePage(p.document);
  return p;
}
let finishCopy, copied;
const success = copyPage({ writeText: value => { copied = value; return new Promise(resolve => { finishCopy = resolve; }); } });
success.node('copy-survey-url').handlers.click();
check('剪贴板未完成时不宣称已复制', !success.node('copy-survey-status').textContent.includes('已复制'));
finishCopy();
await flush();
check('成功复制后端 IPv6 链接并就地提示', copied === shareUrl && success.node('copy-survey-status').textContent === '链接已复制。');
check('复制提示不覆盖顶部或另一条链接', success.node('ws-status').textContent === '' && success.node('copy-service-status').textContent === '');
for (const [name, clipboard] of [
  ['不支持剪贴板', null],
  ['拒绝剪贴板权限', { writeText: () => Promise.reject(new Error('denied')) }],
  ['剪贴板同步异常', { writeText: () => { throw new Error('denied'); } }],
]) {
  const p = copyPage(clipboard);
  p.node('copy-service-url').handlers.click();
  await flush();
  const target = p.node('service-url');
  check(name + '时选中对应链接', p.document.activeElement === target && target.selectionStart === 0 && target.selectionEnd === target.value.length);
  check(name + '时提示手动复制且不宣称已复制', p.node('copy-service-status').textContent.includes('Ctrl+C')
    && p.node('copy-service-status').textContent.includes('长按') && !p.node('copy-service-status').textContent.includes('已复制'));
}
const failed = copyPage(null);
failed.node('survey-url').select = () => { throw new Error('selection blocked'); };
failed.node('copy-survey-url').handlers.click();
check('选中回退异常有明确反馈', failed.node('copy-survey-status').textContent.includes('无法自动选中链接'));
const unfocused = copyPage(null);
unfocused.node('survey-url').focus = () => {};
unfocused.node('copy-survey-url').handlers.click();
check('静默聚焦失败也有反馈', unfocused.node('copy-survey-status').textContent.includes('无法自动选中链接'));
const missing = copyPage(null);
missing.node('survey-url').value = '';
missing.node('copy-survey-url').handlers.click();
check('空链接不会触发复制或误报成功', missing.node('copy-survey-status').textContent.includes('先发布实验'));
console.log(`结果：${passed} 项通过，0 项失败`);
