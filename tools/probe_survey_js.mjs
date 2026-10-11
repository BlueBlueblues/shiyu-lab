/**
 * 问卷页前端行为探针（B 侧，覆盖 C 承认未测的"前端 JS 交互"）
 *
 * 做法：读 probe_survey_render.py 渲染出的**真实 HTML**，按它搭最小 DOM，加载真实 app.js，
 *       模拟用户填写与提交，检查发出去的请求体与页面反馈。
 * 不用手写假设，渲染结果是唯一事实来源。
 *
 * 用法：node tools/probe_survey_js.mjs <渲染出的 survey html> [app.js 路径]
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const HTML = process.argv[2];
const APP_JS = process.argv[3] || path.resolve('app/static/app.js');
if (!HTML || !fs.existsSync(HTML)) {
  console.error('缺少渲染后的 HTML：' + HTML);
  process.exit(2);
}
const html = fs.readFileSync(HTML, 'utf8');

let pass = 0, fail = 0;
const check = (label, ok, extra = '') => {
  console.log(`${ok ? '✅' : '❌'} ${label}${extra ? ' — ' + extra : ''}`);
  ok ? pass++ : fail++;
};

/* ---------------- 从真实 HTML 解析出表单契约 ---------------- */

function attrs(tag) {
  const out = {};
  // 属性值可能是双引号或单引号（data-questions='[...]' 用的就是单引号）
  for (const m of tag.matchAll(/([a-zA-Z0-9_:@.-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g)) {
    const key = m[1];
    if (key === 'input' || key === 'textarea' || key === 'form' || key === 'body') continue;
    const value = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : '');
    out[key] = value;
  }
  return out;
}

const bodyTag = /<body\b([^>]*)>/i.exec(html);
const bodyClass = bodyTag ? (attrs('<body' + bodyTag[1] + '>')['class'] || '') : '';
const formTag = /<form\b([^>]*)>/i.exec(html);
if (!formTag) { console.error('渲染结果里没有 <form>'); process.exit(2); }
const formAttrs = attrs('<form' + formTag[1] + '>');

const fields = [];
for (const m of html.matchAll(/<(input|textarea)\b([^>]*)>/gi)) {
  const a = attrs('<' + m[1] + m[2] + '>');
  fields.push({
    tag: m[1].toLowerCase(),
    type: a.type || (m[1].toLowerCase() === 'textarea' ? 'textarea' : 'text'),
    name: a.name || '',
    value: a.value || '',
    id: a.id || '',
    testid: a['data-testid'] || '',
    disabled: 'disabled' in a,
    checked: 'checked' in a,
  });
}

const spec = {
  bodyClass,
  submitUrl: formAttrs['data-submit-url'] || '',
  isPreview: formAttrs['data-is-preview'] || '',
  hasDataQuestions: 'data-questions' in formAttrs,
  dataQuestions: formAttrs['data-questions'] || '',
  hasFieldErrors: /data-testid="field-errors"/.test(html),
  hasStatus: /data-testid="survey-status"/.test(html),
  hasSuccessBox: /data-testid="submission-success"/.test(html),
  hasSubmitButton: /data-testid="survey-submit"/.test(html),
  hasConsent: /data-testid="survey-consent"/.test(html),
  fields,
};

console.log('='.repeat(94));
console.log('探针输入：' + HTML);
console.log(`  body class       = ${JSON.stringify(spec.bodyClass)}`);
console.log(`  data-submit-url  = ${spec.submitUrl ? '有' : '无'}`);
console.log(`  data-is-preview  = ${JSON.stringify(spec.isPreview)}`);
console.log(`  data-questions   = ${spec.hasDataQuestions ? '有（' + spec.dataQuestions.length + ' 字符）' : '❌ 无'}`);
console.log(`  field-errors 容器 = ${spec.hasFieldErrors ? '有' : '❌ 无'}`);
console.log(`  表单控件         = ${fields.length} 个（radio ${fields.filter(f=>f.type==='radio').length} / checkbox ${fields.filter(f=>f.type==='checkbox').length} / textarea ${fields.filter(f=>f.type==='textarea').length}）`);
console.log('='.repeat(94));

/* ---------------- 最小 DOM ---------------- */

class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.name = '';
    this.type = '';
    this.type2 = '';
    this._text = '';
    this._classes = new Set();
    this.handlers = {};
    this.elements = [];
    this.classList = {
      add: (c) => this._classes.add(c),
      remove: (...cs) => cs.forEach((c) => this._classes.delete(c)),
      contains: (c) => this._classes.has(c),
    };
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k.startsWith('data-')) {
      const camel = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      this.dataset[camel] = String(v);
    }
  }
  appendChild(n) { this.children.push(n); n.parent = this; return n; }
  get firstChild() { return this.children[0] || null; }
  removeChild(n) { this.children.splice(this.children.indexOf(n), 1); }
  scrollIntoView() { this.scrolled = true; }
  addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
  fire(t, ev) { (this.handlers[t] || []).forEach((fn) => fn(ev || {})); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  matches(sel) {
    let s = sel.trim();
    let needChecked = false;
    if (s.endsWith(':checked')) { needChecked = true; s = s.slice(0, -8); }
    const tag = /^[a-zA-Z]+/.exec(s);
    if (tag && this.tagName !== tag[0].toUpperCase()) return false;
    for (const m of s.matchAll(/\[([^\]=]+)(?:="([^"]*)")?\]/g)) {
      const key = m[1];
      let actual;
      if (key === 'name') actual = this.name;
      else if (key.startsWith('data-')) {
        const camel = key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        actual = this.dataset[camel];
      } else actual = this.attrs[key] ?? this[key];
      if (actual === undefined || actual === null) return false;
      if (m[2] !== undefined && String(actual) !== m[2]) return false;
    }
    if (needChecked && !this.checked) return false;
    return true;
  }
  descendants(out = []) { for (const c of this.children) { out.push(c); c.descendants(out); } return out; }
  querySelectorAll(sel) {
    const parts = sel.split(',').map((s) => s.trim());
    return this.descendants().filter((n) => parts.some((p) => n.matches(p)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

/* ---------------- 按解析结果搭 DOM ---------------- */

function buildDom() {
  const byTestId = {};
  const byId = {};
  const form = new Node('form');
  form.setAttribute('id', 'survey-form');
  if (spec.submitUrl) form.setAttribute('data-submit-url', spec.submitUrl);
  if (spec.isPreview) form.setAttribute('data-is-preview', spec.isPreview);
  if (spec.hasDataQuestions) form.setAttribute('data-questions', spec.dataQuestions);
  byId['survey-form'] = form;
  byTestId['survey-form'] = form;

  const qGroups = {};
  const allInputs = [];
  for (const f of spec.fields) {
    const n = new Node(f.tag === 'textarea' ? 'textarea' : 'input');
    n.type = f.tag === 'textarea' ? 'textarea' : f.type;
    n.name = f.name;
    n.value = f.value;
    n.disabled = f.disabled;
    n.checked = f.checked;
    for (const [k, v] of Object.entries(f)) {
      if (k === 'testid' && v) { n.setAttribute('data-testid', v); byTestId[v] = n; }
      else if (k === 'id' && v) { n.setAttribute('id', v); byId[v] = n; }
    }
    const qid = /^answer-(.+)$/.exec(f.name);
    if (qid) {
      (qGroups[qid[1]] = qGroups[qid[1]] || []).push(n);
      // radio 互斥：同 name 自动取消其它
      if (n.type === 'radio') {
        Object.defineProperty(n, 'checked', {
          get() { return this._checked === true; },
          set(v) {
            this._checked = !!v;
            if (v) qGroups[qid[1]].forEach((sib) => { if (sib !== this) sib._checked = false; });
          },
        });
        n._checked = false;
      }
    }
    allInputs.push(n);
    form.appendChild(n);
  }
  form.elements = allInputs;

  // 页面级容器
  const mk = (testid, id) => {
    const n = new Node('div');
    if (testid) { n.setAttribute('data-testid', testid); byTestId[testid] = n; }
    if (id) { n.setAttribute('id', id); byId[id] = n; }
    return n;
  };
  const status = mk('survey-status', 'survey-status');
  const success = mk('submission-success', null);
  success.hidden = true;
  const time = mk('submission-time', null);
  // 关键：field-errors 容器只在渲染结果里真的存在时才创建（基线没有）
  const errorBox = spec.hasFieldErrors ? mk('field-errors', null) : null;
  if (errorBox) errorBox.hidden = true;
  for (const qid of Object.keys(qGroups)) mk('question-' + qid);
  const submitBtn = mk('survey-submit', null);
  submitBtn.disabled = /data-testid="survey-submit" disabled/.test(html);
  allInputs.push(submitBtn);

  return { form, byTestId, byId, qGroups, status, success, time, errorBox, submitBtn };
}

/* ---------------- 打开一个页面 ---------------- */

/* 真实 v4 UUID：后端 Answers.client_submission_id 是 UUID 类型，桩必须给合法值，否则误判 */
function realUuid() {
  const b = new Uint8Array(16);
  for (let i = 0; i < 16; i += 1) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b).map((x) => x.toString(16).padStart(2, '0'));
  return `${h.slice(0, 4).join('')}-${h.slice(4, 6).join('')}-${h.slice(6, 8).join('')}-`
    + `${h.slice(8, 10).join('')}-${h.slice(10, 16).join('')}`;
}

function newPage(existingStore) {
  const dom = buildDom();
  const store = existingStore || new Map();
  const requests = [];
  let scripted = [];

  class AbortControllerStub {
    constructor() { this.signal = { aborted: false }; }
    abort() { this.signal.aborted = true; }
  }

  const document = {
    readyState: 'complete',
    body: (() => {
      const b = new Node('body');
      spec.bodyClass.split(/\s+/).filter(Boolean).forEach((c) => b.classList.add(c));
      return b;
    })(),
    addEventListener: () => {},
    getElementById: (id) => dom.byId[id] || null,
    querySelector: (sel) => {
      const m = /data-testid="([^"]+)"/.exec(sel);
      if (m) return dom.byTestId[m[1]] || null;
      const g = /^#([\w-]+)$/.exec(sel);
      if (g) return dom.byId[g[1]] || null;
      return null;
    },
    createElement: (t) => new Node(t),
  };

  const sandbox = {
    console,
    document,
    AbortController: AbortControllerStub,
    setTimeout, clearTimeout,
    window: {
      setTimeout, clearTimeout,
      localStorage: {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
        removeItem: (k) => store.delete(k),
      },
      crypto: { randomUUID: realUuid },
      location: { origin: 'http://testserver', href: 'http://testserver/e/x/survey' },
      addEventListener: () => {},
    },
    fetch: (url, init) => {
      requests.push({ url: String(url), init: init || {} });
      const next = scripted.shift() || {
        kind: 'ok', status: 201,
        body: { data: { response_id: 1, submitted_at: '2026-10-11T01:13:32+00:00', duplicate: false } },
      };
      if (next.kind === 'netfail') return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve({
        ok: next.status >= 200 && next.status < 300,
        status: next.status,
        text: () => Promise.resolve(JSON.stringify(next.body)),
      });
    },
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), ctx, { filename: path.basename(APP_JS) });
  return { dom, store, requests, script: (...r) => { scripted = r; } };
}

/* ---------------- 断言 ---------------- */

const page = newPage();
const { dom } = page;
check('页面被 app.js 识别为问卷页（body class 含 page-survey）', spec.bodyClass.includes('page-survey'),
  JSON.stringify(spec.bodyClass));

const isPreview = spec.isPreview === 'true';
console.log('');

if (isPreview) {
  dom.form.fire('submit', { preventDefault() {} });
  check('预览页：提交按钮禁用', dom.submitBtn.disabled === true);
  check('预览页：状态提示说明不能提交', /不能提交|预览/.test(dom.status.textContent), dom.status.textContent);
  check('预览页：不发出任何请求', page.requests.length === 0);
} else {
  // 模拟用户真实填写（按渲染出的控件）
  const pick = (qid, value, multi) => {
    const group = dom.qGroups[qid] || [];
    if (multi) group.forEach((n) => { if (value.includes(n.value)) n.checked = true; });
    else group.find((n) => n.value === value).checked = true;
  };
  pick('q1', 'o1', false);
  pick('q2', 'o2', false);
  pick('q4', ['o1', 'o2'], true);
  const ta = (dom.qGroups['q3'] || [])[0];
  if (ta) ta.value = '报名信息散在聊天记录里';
  if (dom.byTestId['survey-consent']) dom.byTestId['survey-consent'].checked = true;

  // ---- 提交 ----
  page.script({ kind: 'ok', status: 201, body: { data: { response_id: 7, submitted_at: '2026-10-11T01:13:32+00:00', duplicate: false } } });
  dom.form.fire('submit', { preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));

  check('提交发出 1 条请求', page.requests.length === 1);
  if (page.requests.length) {
    const req = page.requests[0];
    check('POST 到 data-submit-url', req.init.method === 'POST' && req.url === spec.submitUrl, req.url);
    let body = {};
    try { body = JSON.parse(req.init.body); } catch (e) { /* ignore */ }
    const expected = { q1: 'o1', q2: 'o2', q3: '报名信息散在聊天记录里', q4: ['o1', 'o2'] };
    console.log('   实际请求体 answers = ' + JSON.stringify(body.answers));
    console.log('   期望请求体 answers = ' + JSON.stringify(expected));
    check('三类题答案全部被收集', JSON.stringify(body.answers) === JSON.stringify(expected),
      '缺字段或类型不符');
    check('body 带 client_submission_id（合法 UUID）',
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.client_submission_id || ''),
      String(body.client_submission_id));
    check('body consent 为 true', body.consent === true);
  }
  check('成功后显示收条', dom.success.hidden === false && /提交成功/.test(dom.status.textContent),
    dom.status.textContent);
  check('成功后表单被禁用', dom.form.elements.every((n) => n.disabled === true));

  // ---- 网络失败 → 重发同 UUID；改答案 → 应换新 UUID（B 指南 §B05 第 5 条）----
  const p2 = newPage();
  const pick2 = (qid, value, multi) => {
    const group = p2.dom.qGroups[qid] || [];
    if (multi) group.forEach((n) => { if (value.includes(n.value)) n.checked = true; });
    else group.find((n) => n.value === value).checked = true;
  };
  pick2('q1', 'o1', false); pick2('q2', 'o2', false); pick2('q4', ['o1'], true);
  const ta2 = (p2.dom.qGroups['q3'] || [])[0]; if (ta2) ta2.value = '第一次填写';
  if (p2.dom.byTestId['survey-consent']) p2.dom.byTestId['survey-consent'].checked = true;

  p2.script({ kind: 'netfail' });
  p2.dom.form.fire('submit', { preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  const sid1 = p2.requests.length ? JSON.parse(p2.requests[0].init.body).client_submission_id : '(无请求)';

  p2.script({ kind: 'netfail' });
  p2.dom.form.fire('submit', { preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  const sid2 = p2.requests.length > 1 ? JSON.parse(p2.requests[1].init.body).client_submission_id : '(无请求)';
  check('网络失败重发沿用同一提交编号', sid1 === sid2, `${sid1} / ${sid2}`);

  p2.script({ kind: 'ok', status: 201, body: { data: { response_id: 8, submitted_at: '2026-10-11T02:00:00+00:00', duplicate: false } } });
  if (ta2) ta2.value = '我改了答案';
  pick2('q2', 'o3', false);
  p2.dom.form.fire('submit', { preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  const sid3 = p2.requests.length > 2 ? JSON.parse(p2.requests[2].init.body).client_submission_id : '(无请求)';
  check('改了答案后应换新提交编号（避免同编号不同内容被 409 顶回）', sid3 !== sid2, `${sid2} → ${sid3}`);

  // ---- 422 字段错误是否可见（B 指南 §B05 第 7 条：422 定位到题）----
  const p3 = newPage();
  // 必答题必须全部填上，否则会被前端必答校验拦住，根本走不到服务端 422
  const pick3 = (qid, value, multi) => {
    const group = p3.dom.qGroups[qid] || [];
    if (multi) group.forEach((n) => { if (value.includes(n.value)) n.checked = true; });
    else if (group[0]) group[0].checked = true;
  };
  pick3('q1', null, false); pick3('q2', null, false); pick3('q4', ['o1'], true);
  if (p3.dom.byTestId['survey-consent']) p3.dom.byTestId['survey-consent'].checked = true;
  p3.script({ kind: 'err', status: 422, body: { error: { code: 'VALIDATION_ERROR', message: '输入校验失败，请检查字段。', fields: { q1: '选项不存在', 'answers.q2': '此项为必答' } } } });
  p3.dom.form.fire('submit', { preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  const errText = p3.dom.errorBox ? p3.dom.errorBox.textContent : '';
  const visibleText = [p3.dom.status.textContent, errText].join(' | ');
  check('422 时页面能说出字段级原因', /选项不存在|此项为必答/.test(visibleText), visibleText.trim() || '(页面无任何字段提示)');
  check('422 的错误落到可读的题号上（第 N 题）',
    /第 1 题：选项不存在/.test(errText) && /第 2 题：此项为必答/.test(errText), errText.trim() || '(空)');
  check('422 滚动到首个错误题目', p3.dom.byTestId['question-q1'].scrolled === true);

  // ---- 刷新后是否有"已提交"标记（B 指南 §B05 第 8 条）----
  // 必须复用刚才那个页面的 localStorage 才算"刷新"，新建空 store 是无效测试
  const reloaded = newPage(page.store);
  check('刷新后识别本浏览器已提交过',
    reloaded.dom.success.hidden === false &&
    /已经提交|此前已提交|已提交过|无需重复/.test(reloaded.dom.status.textContent),
    '状态="' + reloaded.dom.status.textContent + '" 收条可见=' + (reloaded.dom.success.hidden === false));
  check('刷新后表单保持禁用，且不再发请求',
    reloaded.dom.form.elements.every((n) => n.disabled === true) && reloaded.requests.length === 0);

  const fill = (p) => {
    p.dom.qGroups.q1[0].checked = true;
    p.dom.qGroups.q2[0].checked = true;
    p.dom.qGroups.q4[0].checked = true;
    p.dom.byTestId['survey-consent'].checked = true;
  };
  const submit = async (p) => {
    p.dom.form.fire('submit', { preventDefault() {} });
    await new Promise((r) => setTimeout(r, 30));
  };
  const incomplete = newPage();
  await submit(incomplete);
  check('必答缺失在本地拦截且定位题目', incomplete.requests.length === 0
    && incomplete.dom.byTestId['question-q1'].scrolled === true);
  fill(incomplete);
  incomplete.dom.byTestId['survey-consent'].checked = false;
  await submit(incomplete);
  check('未同意参与不发请求', incomplete.requests.length === 0);

  const conflict = newPage();
  fill(conflict);
  conflict.script({ status: 409, body: { error: { code: 'IDEMPOTENCY_CONFLICT' } } });
  await submit(conflict);
  const conflictId = JSON.parse(conflict.requests[0].init.body).client_submission_id;
  check('409 保留答案并恢复按钮', !conflict.dom.submitBtn.disabled
    && conflict.dom.qGroups.q1[0].checked);
  await submit(conflict);
  check('409 后重试换新 UUID', JSON.parse(conflict.requests[1].init.body).client_submission_id !== conflictId);

  const duplicate = newPage();
  fill(duplicate);
  duplicate.script({ status: 200, body: { data: { duplicate: true, submitted_at: '2026-10-11T02:00:00Z' } } });
  await submit(duplicate);
  check('幂等响应显示收条与未重复记录说明', duplicate.dom.success.hidden === false
    && /未重复记录/.test(duplicate.dom.status.textContent));
  check('公开请求不带管理凭据', duplicate.requests[0].init.credentials === 'omit'
    && !duplicate.requests[0].init.headers['X-Requested-With']);

  const blocked = newPage({ has() { throw new Error('storage blocked'); }, set() { throw new Error('storage blocked'); } });
  fill(blocked);
  blocked.script({ kind: 'netfail' });
  await submit(blocked);
  const blockedId = JSON.parse(blocked.requests[0].init.body).client_submission_id;
  blocked.script({ kind: 'netfail' });
  await submit(blocked);
  check('存储禁用时同内容重试沿用 UUID', JSON.parse(blocked.requests[1].init.body).client_submission_id === blockedId);
  blocked.dom.qGroups.q3[0].value = '修改后的回答';
  await submit(blocked);
  check('存储禁用时修改答案换 UUID', JSON.parse(blocked.requests[2].init.body).client_submission_id !== blockedId);
}

console.log('='.repeat(94));
console.log(`结果：${pass} 项通过，${fail} 项失败`);
process.exit(fail === 0 ? 0 : 1);
