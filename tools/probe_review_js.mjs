/**
 * 复盘按钮行为探针（B 侧，A07 接线）
 *
 * 用最小 DOM 桩加载**真实 app.js**，验证复盘入口的行为（不是只看渲染结果）：
 *   - 未发布：不发请求，给出可读原因（接口会返回 400）
 *   - 已发布：二次确认 → POST /api/projects/{id}/review，带 X-Requested-With 守卫头
 *   - 提交期间按钮禁用，避免重复点击
 *   - 成功：提示份数并刷新页面（reload 被调用）
 *   - 失败：可读错误 + run_id 一并显示，按钮恢复可用，且不刷新
 *   - 二次确认被取消时不发请求
 *
 * 用法：node tools/probe_review_js.mjs [app.js 路径]
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const APP_JS = process.argv[2] || path.resolve('app/static/app.js');
let pass = 0, fail = 0;
const check = (label, ok, extra = '') => {
  console.log(`${ok ? '✅' : '❌'} ${label}${extra ? ' — ' + extra : ''}`);
  ok ? pass++ : fail++;
};

const QUESTIONS = [
  { id: 'q1', type: 'single', title: '是否遇到整理困难？', required: true,
    options: [{ id: 'o1', label: '经常' }, { id: 'o2', label: '偶尔' }] },
];

class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this._text = '';
    this._classes = new Set();
    this.handlers = {};
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
  getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; }
  appendChild(n) { this.children.push(n); n.parent = this; return n; }
  removeChild(n) { this.children = this.children.filter((c) => c !== n); return n; }
  get firstChild() { return this.children[0] || null; }
  addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
  fire(t, ev) { (this.handlers[t] || []).forEach((fn) => fn(ev || {})); }
  set className(v) { String(v).split(/\s+/).filter(Boolean).forEach((c) => this._classes.add(c)); }
  get className() { return [...this._classes].join(' '); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  descendants(out = []) { for (const c of this.children) { out.push(c); c.descendants(out); } return out; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

/** 打开一个工作台页面 */
function newPage(config, options = {}) {
  const nodes = new Map();
  const byTestId = {};
  const ids = [
    'ws-status', 'field-errors', 'review-generate', 'review-status',
    'plan-generate', 'experiment-publish', 'run-log', 'statistics-body',
    'response-count', 'cutoff-response-id', 'statistics-status',
    'responses-body', 'responses-load', 'responses-status',
  ];
  ids.forEach((id) => {
    const node = new Node('button');
    node.setAttribute('data-testid', id);
    nodes.set(id, node);
    byTestId[id] = node;
  });
  byTestId['review-generate'].disabled = Boolean(options.reviewDisabled);
  byTestId['review-generate'].textContent = options.reviewLabel || '生成复盘';

  const body = new Node('body');
  'page-admin page-workspace'.split(' ').forEach((c) => body.classList.add(c));

  const requests = [];
  let scripted = [];
  const confirms = [];
  let reloaded = 0;

  const document = {
    readyState: 'complete',
    body,
    addEventListener: () => {},
    getElementById: () => null,
    querySelector: (sel) => {
      const m = /data-testid="([^"]+)"/.exec(sel);
      return m ? byTestId[m[1]] || null : null;
    },
    querySelectorAll: () => [],
    createElement: (t) => new Node(t),
    createTextNode: (t) => { const n = new Node('#text'); n._text = t; return n; },
  };

  const sandbox = {
    console,
    document,
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    setTimeout, clearTimeout, URL,
    window: {
      setTimeout, clearTimeout,
      __FO_WORKSPACE__: config,
      location: {
        origin: 'http://testserver',
        pathname: '/projects/' + config.projectId,
        reload: () => { reloaded += 1; },
      },
      confirm: (message) => { confirms.push(message); return options.confirm !== false; },
      addEventListener: () => {},
      localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
      crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    },
    fetch: (url, init) => {
      const u = String(url);
      requests.push({ url: u, init: init || {} });
      if (/\/review$/.test(u)) {
        const next = scripted.shift() || { status: 200, body: { data: { report_id: 'r1', run_id: 'run1', response_count: 4, cutoff_response_id: 4 } } };
        return Promise.resolve({ ok: next.status < 300, status: next.status,
          text: () => Promise.resolve(JSON.stringify(next.body)) });
      }
      if (u === '/api/projects/' + config.projectId) {
        const detail = options.detail || {
          running_run_id: null,
          experiment: { published_at: config.published ? '2026-10-11T01:00:00Z' : null },
        };
        return Promise.resolve({ ok: true, status: 200,
          text: () => Promise.resolve(JSON.stringify({ data: detail })) });
      }
      return Promise.resolve({ ok: true, status: 200,
        text: () => Promise.resolve(JSON.stringify({ data: [] })) });
    },
    navigator: { clipboard: null },
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), ctx, { filename: path.basename(APP_JS) });
  return {
    byTestId, requests, confirms,
    reloads: () => reloaded,
    script: (...r) => { scripted = r; },
  };
}

const BASE_CONFIG = {
  projectId: 'p-1', projectStatus: 'collecting', experimentId: 'exp-1',
  published: true, runningRunId: null, questions: QUESTIONS,
};

/* ---------------- 1. 已发布：确认 → POST → 刷新 ---------------- */
console.log('='.repeat(92));
const page = newPage(BASE_CONFIG);
await new Promise((r) => setTimeout(r, 30));
page.byTestId['review-generate'].fire('click');
check('二次确认弹窗出现', page.confirms.length === 1, page.confirms[0] ? page.confirms[0].slice(0, 40) + '…' : '');
check('确认文案写明快照不会随新回答改变',
  (page.confirms[0] || '').includes('不会改变') && (page.confirms[0] || '').includes('已有新反馈'));
check('提交期间按钮被禁用（防重复点击）',
  page.requests.filter((r) => /\/review$/.test(r.url)).length === 1
  && page.byTestId['review-generate'].disabled === true);
const reviewReq = page.requests.find((r) => /\/review$/.test(r.url));
check('POST 到 /api/projects/{id}/review（同源相对路径）',
  reviewReq && reviewReq.init.method === 'POST'
  && reviewReq.url === '/api/projects/p-1/review', reviewReq ? reviewReq.url : '(无请求)');
check('带 X-Requested-With 守卫头',
  (reviewReq.init.headers || {})['X-Requested-With'] === 'FirstOrderLab');
await new Promise((r) => setTimeout(r, 30));
check('成功后提示回答份数',
  page.byTestId['review-status'].textContent.includes('4'), page.byTestId['review-status'].textContent);
check('成功后刷新页面以显示新报告', page.reloads() === 1, 'reload 次数 ' + page.reloads());

/* ---------------- 2. 失败：可读错误 + run_id，不刷新 ---------------- */
console.log('='.repeat(92));
const failed = newPage(BASE_CONFIG);
await new Promise((r) => setTimeout(r, 30));
failed.script({ status: 502, body: { error: { code: 'LLM_UPSTREAM_ERROR', message: '模型服务暂时不可用。', fields: {}, run_id: 'run-abc' } } });
failed.byTestId['review-generate'].fire('click');
await new Promise((r) => setTimeout(r, 30));
const failText = failed.byTestId['review-status'].textContent;
check('失败时显示可读错误', failText.includes('复盘失败') && failText.includes('模型服务暂时不可用'), failText);
check('失败时带上 run_id 便于查运行日志', failText.includes('run-abc'));
check('失败时按钮恢复可用', failed.byTestId['review-generate'].disabled === false);
check('失败时不刷新页面', failed.reloads() === 0);

/* ---------------- 运行冲突：按后端真实锁状态保持禁用 ---------------- */
const busy = newPage(BASE_CONFIG, { detail: {
  running_run_id: 'run-other', experiment: { published_at: '2026-10-11T01:00:00Z' },
} });
busy.script({ status: 409, body: { error: {
  code: 'RUN_IN_PROGRESS', message: '项目已有任务执行中。', fields: {},
} } });
busy.byTestId['review-generate'].fire('click');
await new Promise((r) => setTimeout(r, 30));
check('409 后重新读取项目锁状态', busy.requests.some((r) => r.url === '/api/projects/p-1'));
check('其他任务仍运行时复盘按钮保持禁用', busy.byTestId['review-generate'].disabled === true);

/* ---------------- 3. 未发布：不发请求并说明原因 ---------------- */
console.log('='.repeat(92));
const draft = newPage({ ...BASE_CONFIG, published: false });
await new Promise((r) => setTimeout(r, 30));
const before = draft.requests.length;
draft.byTestId['review-generate'].fire('click');
await new Promise((r) => setTimeout(r, 20));
check('未发布时不发复盘请求', draft.requests.length === before);
check('未发布时说明发布前置步骤',
  draft.byTestId['review-status'].textContent.includes('尚未发布')
  && draft.byTestId['review-status'].textContent.includes('检查草稿并发布'),
  draft.byTestId['review-status'].textContent);

/* ---------------- 4. 取消确认：不发请求 ---------------- */
console.log('='.repeat(92));
const cancelled = newPage(BASE_CONFIG, { confirm: false });
await new Promise((r) => setTimeout(r, 30));
const beforeCancel = cancelled.requests.length;
cancelled.byTestId['review-generate'].fire('click');
await new Promise((r) => setTimeout(r, 20));
check('取消二次确认后不发请求', cancelled.requests.length === beforeCancel);
check('取消后按钮未被禁用', cancelled.byTestId['review-generate'].disabled === false);

console.log('='.repeat(92));
console.log(`结果：${pass} 项通过，${fail} 项失败`);
process.exit(fail === 0 ? 0 : 1);
