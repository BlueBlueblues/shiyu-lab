/**
 * 工作台统计/原始回答的渲染探针（B 侧，A06）
 *
 * 目的：证明"接入统计"不只是接口通，而是**浏览器里真的把 A06 的统计对象渲染对了**：
 *   - 有效回答份数、快照截止回答编号
 *   - 百分比按两位小数展示；有分母时 0 计数显示 0.00%；零分母显示"暂无数据"（不是 0%）
 *   - 多选题分母是该题作答份数（3 份），不是全部回答数（4 份）
 *   - 简答列出文本；原始回答按需读取且不出现 client_submission_id
 *
 * DOM 依据：真实渲染出的 workspace HTML（容器是否存在、内联配置内容都从它读）。
 * 用法：node tools/probe_workspace_js.mjs tools/_probe/workspace_published.html app/static/app.js
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const HTML_PATH = process.argv[2];
const APP_JS = process.argv[3] || path.resolve('app/static/app.js');
const html = fs.readFileSync(HTML_PATH, 'utf8');

let pass = 0, fail = 0;
const check = (label, ok, extra = '') => {
  console.log(`${ok ? '✅' : '❌'} ${label}${extra ? ' — ' + extra : ''}`);
  ok ? pass++ : fail++;
};

/* ---------------- 从真实 HTML 取内联配置与容器清单 ---------------- */

const configMatch = /window\.__FO_WORKSPACE__\s*=\s*(\{[\s\S]*?\});/.exec(html);
if (!configMatch) { console.error('渲染结果里没有 __FO_WORKSPACE__ 配置'); process.exit(2); }
const CONFIG = JSON.parse(configMatch[1].replace(/([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:/g, '$1"$2":'));
console.log('内联配置：' + JSON.stringify({ ...CONFIG, questions: (CONFIG.questions || []).length + ' 题' }));

const NEEDED = [
  'statistics-body', 'response-count', 'cutoff-response-id', 'statistics-status',
  'responses-body', 'responses-load', 'responses-status', 'run-log', 'ws-status', 'field-errors',
];
const present = Object.fromEntries(NEEDED.map((id) => [id, html.includes(`data-testid="${id}"`)]));
const missing = NEEDED.filter((id) => !present[id]);
check('渲染结果包含全部实时区域容器', missing.length === 0, missing.length ? '缺 ' + missing.join(', ') : '');
check('body class 含 page-workspace', /<body class="[^"]*page-workspace/.test(html));

/* ---------------- 最小 DOM ---------------- */

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

const byTestId = {};
function container(testid) {
  const node = new Node('div');
  node.setAttribute('data-testid', testid);
  byTestId[testid] = node;
  return node;
}

/** 必须在 newPage() 之前建好容器：app.js 的 boot 会立刻去查这些 testid */
function resetDom() {
  Object.keys(byTestId).forEach((k) => { delete byTestId[k]; });
  NEEDED.forEach(container);
  byTestId['responses-load'].disabled = false;
  return byTestId;
}

const body = new Node('body');
'page-admin page-workspace'.split(' ').forEach((c) => body.classList.add(c));

const document = {
  readyState: 'complete',
  body,
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: (sel) => {
    const m = /data-testid="([^"]+)"/.exec(sel);
    return m ? byTestId[m[1]] || null : null;
  },
  querySelectorAll: () => [],   // localizeTimes 会遍历 [data-utc]，桩里没有时间元素
  createElement: (t) => new Node(t),
  createTextNode: (t) => { const n = new Node('#text'); n._text = t; return n; },
};

/* ---------------- A06 统计与原始回答的固定样本（与交接文档一致） ---------------- */

const STATS = {
  experiment_id: CONFIG.experimentId,
  response_count: 4,
  cutoff_response_id: 4,
  questions: [
    { id: 'q1', type: 'single', answered_count: 4, options: [
      { id: 'o1', label: '经常', count: 2, percentage: 50.0 },
      { id: 'o2', label: '偶尔', count: 2, percentage: 50.0 }] },
    { id: 'q4', type: 'multiple', answered_count: 3, options: [
      { id: 'o1', label: '手机使用方便', count: 2, percentage: 66.67 },
      { id: 'o2', label: '信息导出', count: 3, percentage: 100.0 },
      { id: 'o3', label: '操作简单', count: 0, percentage: 0.0 },
      { id: 'o4', label: '暂时无需求', count: 0, percentage: 0.0 }] },
    { id: 'q3', type: 'text', answered_count: 2, texts: [
      { response_id: 1, text: '第一份反馈' }, { response_id: 4, text: '第四份反馈' }] },
  ],
};
const RAW = [1, 2, 3, 4].map((id) => ({
  id, answers: { q1: 'o1', q3: id % 2 ? '第一份反馈' : '' }, created_at: '2026-10-11T02:00:00+00:00',
}));
const ZERO_STATS = { experiment_id: CONFIG.experimentId, response_count: 0, cutoff_response_id: 0,
                     questions: [{ id: 'q4', type: 'multiple', answered_count: 0, options: [
                       { id: 'o1', label: '手机使用方便', count: 0, percentage: null }] }] };

function newPage(statsPayload) {
  const requests = [];
  const sandbox = {
    console,
    document,
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    setTimeout, clearTimeout, URL,
    window: {
      setTimeout, clearTimeout,
      __FO_WORKSPACE__: CONFIG,
      location: { origin: 'http://testserver', pathname: '/projects/' + CONFIG.projectId, reload: () => {} },
      addEventListener: () => {},
      localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
      crypto: { randomUUID: () => 'x' },
      confirm: () => true,
    },
    fetch: (url, init) => {
      const u = String(url);
      requests.push({ url: u, init: init || {} });
      let body = { data: [] };
      if (/\/statistics$/.test(u)) { body = { data: statsPayload }; }
      else if (/\/responses$/.test(u)) { body = { data: RAW }; }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
    },
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), ctx, { filename: path.basename(APP_JS) });
  return { requests, sandbox };
}

/* ---------------- 用例 1：正常统计 ---------------- */

console.log('='.repeat(94));
resetDom();
const page = newPage(STATS);
await new Promise((r) => setTimeout(r, 60));

const statsBody = byTestId['statistics-body'];
const flat = statsBody.textContent.replace(/\s+/g, ' ');
check('有效回答份数按份展示（不是"人数"）',
  byTestId['response-count'].textContent.includes('有效回答份数：4 份'),
  byTestId['response-count'].textContent);
check('显示统计快照截止编号', byTestId['cutoff-response-id'].textContent.includes('4'),
  byTestId['cutoff-response-id'].textContent);
check('多选题百分比两位小数 66.67%', flat.includes('66.67%'), flat.slice(0, 160));
check('100% 显示为 100.00%', flat.includes('100.00%'));
check('有分母时 0 计数显示 0.00%（不是"暂无数据"）', flat.includes('0.00%'));
check('多选题分母说明为 3 份',
  flat.includes('百分比分母为该题有效作答份数 3 份') || flat.includes('有效作答 3 份'),
  flat.slice(0, 200));
check('简答列出文本条目',
  flat.includes('第一份反馈') && flat.includes('第四份反馈'));
check('统计区含题目题干（来自内联配置，接口不返回题干）',
  STATS.questions.every(question => {
    const original = (CONFIG.questions || []).find(item => item.id === question.id);
    return original && flat.includes(original.title);
  }), flat.slice(0, 60));
check('只请求了该实验的统计与运行日志',
  page.requests.every((r) => r.url.includes(CONFIG.experimentId) || r.url.includes(CONFIG.projectId)),
  page.requests.map((r) => r.url.replace('http://testserver', '')).join(' , '));

/* ---------------- 用例 2：零回答 → 暂无数据，不显示 0% ---------------- */

console.log('='.repeat(94));
resetDom();
const zeroPage = newPage(ZERO_STATS);
await new Promise((r) => setTimeout(r, 60));
const zeroFlat = byTestId['statistics-body'].textContent.replace(/\s+/g, ' ');
check('零回答：有效回答份数 0 份', byTestId['response-count'].textContent.includes('0 份'),
  byTestId['response-count'].textContent);
check('零分母：显示"暂无数据"而不是 0%',
  zeroFlat.includes('暂无数据') && !zeroFlat.includes('0.00%'), zeroFlat.slice(0, 140));
check('零回答：状态提示说明暂无数据',
  byTestId['statistics-status'].textContent.includes('暂无数据'),
  byTestId['statistics-status'].textContent);
check('零回答时也确实发起了统计请求（不是前端编的）',
  zeroPage.requests.some((r) => /\/statistics$/.test(r.url)));

/* ---------------- 用例 3：原始回答按需读取 ---------------- */

console.log('='.repeat(94));
resetDom();
const rawPage = newPage(STATS);
await new Promise((r) => setTimeout(r, 30));
byTestId['responses-load'].fire('click');
await new Promise((r) => setTimeout(r, 60));
check('点击后请求原始回答接口',
  rawPage.requests.some((r) => /\/responses$/.test(r.url)),
  rawPage.requests.map((r) => r.url.replace('http://testserver', '')).join(' , '));
const rawFlat = byTestId['responses-body'].textContent.replace(/\s+/g, ' ');
check('渲染出 4 条回答', (byTestId['responses-body'].children || []).length === 4,
  String((byTestId['responses-body'].children || []).length));
check('回答含编号与北京时间', rawFlat.includes('回答 #1')
  && /2026[/-]10[/-]11/.test(rawFlat) && rawFlat.includes('北京时间'),
  rawFlat.slice(0, 60));
check('页面不出现 client_submission_id',
  !rawFlat.includes('client_submission_id') && !html.includes('client_submission_id'));
check('读取后按钮恢复可用', byTestId['responses-load'].disabled === false);
check('状态提示报告条数', byTestId['responses-status'].textContent.includes('4 份'),
  byTestId['responses-status'].textContent);

console.log('='.repeat(94));
console.log(`结果：${pass} 项通过，${fail} 项失败`);
process.exit(fail === 0 ? 0 : 1);
