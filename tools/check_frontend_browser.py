"""Headless Edge checks against rendered test templates and Mock GETs; no model or database."""
import html
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'evidence/ux-20261012'
EDGE = next((Path(p) / 'Microsoft/Edge/Application/msedge.exe'
             for p in (os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('PROGRAMFILES', ''))
             if (Path(p) / 'Microsoft/Edge/Application/msedge.exe').is_file()), None)
if EDGE is None:
    raise SystemExit('System Edge is unavailable')
ARTIFACTS.mkdir(parents=True, exist_ok=True)
fixtures = runpy.run_path(str(ROOT / 'tests/test_frontend_ux.py'))
render, context = fixtures['render'], fixtures['context']
mock_gets = '''<script>
window.__WRITES__ = [];
window.fetch = async (url, init) => {
  if (init.method !== 'GET') { window.__WRITES__.push(url); throw new Error('Writes prohibited in browser check'); }
  return { ok: true, status: 200, text: async () => JSON.stringify({data:
    String(url).endsWith('/statistics') ? { response_count: 0, cutoff_response_id: 0, questions: [] } : []}) };
};
</script>'''
for status in ('draft', 'planned', 'collecting', 'reviewed', 'stale', 'running'):
    data = context('collecting' if status in ('stale', 'running') else status,
                   stale=status == 'stale', running='run-ux' if status == 'running' else None)
    markup = render('workspace.html', **data).replace('<head>', '<head>' + mock_gets)
    (ARTIFACTS / (status + '.html')).write_text(markup, encoding='utf-8')
data = context('planned')
for name, published, running in [('edit', False, None), ('locked', True, None), ('edit-running', False, 'run-ux')]:
    markup = render('experiment_edit.html', experiment=data['experiment'], is_locked=published, running_run_id=running)
    (ARTIFACTS / (name + '.html')).write_text(markup, encoding='utf-8')
report = dict(data['latest_report_meta'] or {}, id='r-ux', created_at='2026-10-11T00:00:00Z',
              response_count=0, cutoff_response_id=0, stale=True, data_kind='test',
              statistics={'questions': []}, review={'summary': '测试报告快照', 'hypothesis_assessments': [],
              'key_feedback': [], 'limitations': ['证据不足'], 'next_actions': []})
for name, saved in [('report', report), ('report-empty', None)]:
    (ARTIFACTS / (name + '.html')).write_text(render('report.html', project=data['project'],
        report=saved, running_run_id=None), encoding='utf-8')
shutil.copytree(ROOT / 'app/static', ARTIFACTS / 'static', dirs_exist_ok=True)
runner = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>FirstOrder Lab 浏览器检查</title><style>
body{font:14px sans-serif;margin:12px}#frames{display:flex;flex-wrap:wrap;gap:12px}iframe{border:1px solid #ccc;height:700px}
</style><p id="summary">浏览器检查中…</p><pre id="results" hidden>running</pre><div id="frames"></div><script>
const results = [];
function check(name, ok, detail='') { results.push({name, ok:!!ok, detail}); }
async function frame(name, width) {
  const f = document.createElement('iframe'); f.width = width; f.title = name + ' ' + width;
  const loaded = new Promise(resolve => f.onload = resolve);
  f.src = name + '.html'; document.querySelector('#frames').append(f); await loaded;
  return f;
}
const pause = () => new Promise(resolve => setTimeout(resolve, 20));
(async () => {
  for (const width of [320,375,430,1024]) {
    for (const name of ['stale','planned','collecting','edit','locked','edit-running','report','report-empty']) {
      const f = await frame(name,width), w=f.contentWindow, d=f.contentDocument;
      await pause();
      const overflow = [...d.querySelectorAll('body *')].filter(el => {
        const r=el.getBoundingClientRect();
        return !el.classList.contains('sr-only') && r.width>1 && (r.right>w.innerWidth+1 || r.left < -1);
      }).map(el=>el.getAttribute('data-testid') || el.tagName);
      check(name+' '+width+' 无整体溢出', d.documentElement.scrollWidth<=w.innerWidth && !overflow.length,overflow.join(','));
      if (name==='stale') {
        const links=[...d.querySelectorAll('.ws-step')];
        check('四阶段布局 '+width, links.length===4 && (width<600 ? links[2].offsetTop>links[0].offsetTop : links.every(a=>a.offsetTop===links[0].offsetTop)));
      }
      if (name==='collecting') {
        const step=d.querySelector('.ws-step'); step.focus();
        check('键盘焦点样式 '+width, d.activeElement===step && parseFloat(w.getComputedStyle(step).outlineWidth)>=3);
        for (const kind of ['service','survey']) {
          const target=d.querySelector('[data-testid="'+kind+'-url"]');
          const btn=d.querySelector('[data-testid="copy-'+kind+'-url"]');
          const status=d.querySelector('[data-testid="copy-'+kind+'-status"]');
          Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:undefined});
          btn.click(); await pause();
          check(kind+' '+width+' 无剪贴板时真实聚焦选中', d.activeElement===target && target.selectionStart===0 && target.selectionEnd===target.value.length && status.textContent.includes('Ctrl+C'));
          Object.defineProperty(w.navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});
          btn.click(); await pause();
          check(kind+' '+width+' 拒绝权限仍可手动复制', target.selectionEnd===target.value.length && !status.textContent.includes('已复制'));
        }
        check('复制提示与长 IPv6 链接不溢出 '+width,d.documentElement.scrollWidth<=w.innerWidth);
        for (const href of ['#project-overview','#validation-plan','#feedback','#review']) {
          const link=d.querySelector('a.ws-step[href="'+href+'"]');
          link.click(); await pause();
          const box=d.querySelector(href).getBoundingClientRect();
          check('锚点 '+width+' '+href, w.location.hash===href && box.top>=0 && box.top<w.innerHeight);
        }
        check('导航无业务写入 '+width,w.__WRITES__.length===0);
        if(width===320) { w.location.hash='#feedback'; await pause(); }
      }
      if (width!==320 || !['stale','planned','collecting'].includes(name)) f.remove();
    }
  }
  document.querySelector('#results').textContent=JSON.stringify(results);
  document.querySelector('#summary').textContent=results.filter(r=>r.ok).length+' 项通过，'+results.filter(r=>!r.ok).length+' 项失败（静态模板 + Mock GET）';
  document.body.dataset.checkStatus='done';
})().catch(e=>{check('runner',false,String(e.stack));document.querySelector('#results').textContent=JSON.stringify(results);document.body.dataset.checkStatus='done';});
</script></html>'''
(ARTIFACTS / 'browser.html').write_text(runner, encoding='utf-8')


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ARTIFACTS)))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='fo-ux-edge-') as profile:
        result = subprocess.run([str(EDGE), '--headless', '--no-first-run', '--disable-background-networking',
            '--user-data-dir=' + profile, '--dump-dom', '--virtual-time-budget=15000', '--window-size=1280,1000',
            '--screenshot=' + str(ARTIFACTS / 'browser.png'),
            f'http://127.0.0.1:{server.server_port}/browser.html'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (ARTIFACTS / 'browser-dom.html').write_text(result.stdout, encoding='utf-8')
    (ARTIFACTS / 'browser-stderr.log').write_text(result.stderr, encoding='utf-8')
    match = re.search(r'<pre id="results"[^>]*>(.*?)</pre>', result.stdout, re.S)
    if not match or 'data-check-status="done"' not in result.stdout:
        raise SystemExit('Browser checks did not complete; inspect evidence/ux-20261012/browser-stderr.log')
    checks = json.loads(html.unescape(match[1]))
    (ARTIFACTS / 'browser-results.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    for item in checks:
        print(('PASS ' if item['ok'] else 'FAIL ') + item['name'] + (' ' + item['detail'] if item['detail'] else ''))
    failures = sum(not item['ok'] for item in checks)
    print(f'Headless Edge: {len(checks)-failures} passed, {failures} failed; static test templates + Mock GETs')
    raise SystemExit(bool(failures))
finally:
    server.shutdown()
    server.server_close()
