// 模拟 DOM/fetch；不连接服务器、数据库或真实模型。
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync('app/static/app.js', 'utf8').replace(
  '}());', 'window.authProbe = {initAuthPage, initLogout}; }());');
let passed = 0;
function check(name, value) { assert.ok(value, name); passed++; console.log('PASS ' + name); }
class Node {
  constructor() {
    this.children = []; this.handlers = {}; this.disabled = false;
    this.classList = { add() {}, remove() {} };
  }
  appendChild(node) { this.children.push(node); }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); }
  get firstChild() { return this.children[0]; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
}
function page(action) {
  const ids = Object.fromEntries(['auth-submit', 'auth-status', 'auth-errors', 'auth-logout', 'logout-status']
    .map(id => [id, new Node()]));
  const form = new Node();
  form.elements = Object.fromEntries(Object.entries({ username: ' Alice ', password: ' test-only-password ',
    password_confirmation: ' test-only-password ', email: ' ' }).map(([key, value]) => [key, { value }]));
  form.getAttribute = () => action;
  form.reset = () => { Object.values(form.elements).forEach(field => { field.value = ''; }); };
  const requests = [], replies = [];
  const document = { readyState: 'loading', addEventListener() {}, getElementById: () => form,
    querySelector: selector => ids[/data-testid="([^"]+)"/.exec(selector)[1]],
    createElement: () => new Node() };
  const sandbox = { document, AbortController, window: { setTimeout, clearTimeout,
    location: { search: '', href: '' } }, fetch: async (url, init) => {
      requests.push({ url, init });
      const reply = replies.shift() || { data: { username: 'Alice' } };
      return { ok: !reply.error, status: reply.error ? 401 : 200,
        text: async () => JSON.stringify(reply) };
    } };
  vm.runInNewContext(source, sandbox);
  return { ...sandbox, ids, form, requests, replies, probe: sandbox.window.authProbe };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const submit = p => p.form.handlers.submit({ preventDefault() {} });
const reg = page('register');
reg.probe.initAuthPage(reg.document);
reg.form.elements.password_confirmation.value = 'different';
submit(reg);
check('确认密码不一致时不发请求', reg.requests.length === 0);
reg.form.elements.password_confirmation.value = reg.form.elements.password.value;
submit(reg);
submit(reg);
await flush();
const registration = reg.requests[0], payload = JSON.parse(registration.init.body);
check('注册使用正式接口且防止重复提交', reg.requests.length === 1 && registration.url === '/api/auth/register');
check('注册只发送允许字段，空邮箱为 null', Object.keys(payload).sort().join(',') === 'email,password,username'
  && payload.email === null && payload.username === 'Alice');
check('密码保留用户输入空格', payload.password === ' test-only-password ');
check('注册成功进入登录页', reg.window.location.href === '/login?registered=1');
check('成功后清空密码', reg.form.elements.password.value === '');

const login = page('login');
login.window.location.search = '?registered=1';
login.probe.initAuthPage(login.document);
check('注册后登录页显示成功提示', login.ids['auth-status'].textContent.includes('注册成功'));
submit(login);
await flush();
const request = login.requests[0];
check('登录使用同源 Cookie 和指定请求头', request.url === '/api/auth/login'
  && request.init.credentials === 'same-origin' && request.init.headers['X-Requested-With'] === 'FirstOrderLab');
check('登录成功进入个人项目列表', login.window.location.href === '/projects');

const bad = page('login');
bad.probe.initAuthPage(bad.document);
bad.replies.push({ error: { code: 'AUTH_REQUIRED', message: '用户名或密码错误。', fields: {} } });
submit(bad);
await flush();
check('登录失败显示错误并恢复按钮', bad.ids['auth-status'].textContent.includes('用户名或密码错误')
  && !bad.ids['auth-submit'].disabled && bad.window.location.href === '');
check('登录失败清空密码', bad.form.elements.password.value === '');

const logout = page('login');
logout.probe.initLogout();
logout.ids['auth-logout'].handlers.click();
await flush();
check('退出发 POST 并返回公开首页', logout.requests[0].url === '/api/auth/logout'
  && logout.requests[0].init.body === '{}' && logout.window.location.href === '/');
const authSource = source.slice(source.indexOf('function initAuthPage'), source.indexOf('function boot'));
check('认证交互不读取或保存令牌', !/document\.cookie|localStorage|sessionStorage/.test(authSource));
console.log(`结果：${passed} 项通过，0 项失败`);
