import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / 'tests/c_integration/fixtures/plan.json').read_text(encoding='utf-8'))
ENV = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=select_autoescape())
ENV.globals['url_for'] = lambda name, path: '/static' + path
REQUEST = SimpleNamespace(state=SimpleNamespace(current_user={'username': '体验测试用户'}))


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))

    def by_testid(self, name):
        return next(attrs for _, attrs in self.items if attrs.get('data-testid') == name)


def render(template, **context):
    return ENV.get_template(template).render(request=REQUEST, **context)


def context(status='planned', stale=False, running=None):
    project = dict(id='p-ux', name='创业体验测试', status=status, data_kind='test',
                   idea='验证校园需求', target_users='大学生', skills='Python', budget_cny=0,
                   validation_goal='了解需求', created_at='2026-10-11T00:00:00Z')
    published = status in ('collecting', 'reviewed')
    url = 'http://[2001:db8:1234:5678:abcd:ef01:2345:6789]:8000/e/token'
    experiment = None if status == 'draft' else dict(
        id='e-ux', project_id=project['id'], landing=PLAN['landing'], questions=PLAN['questions'],
        published_at='2026-10-11T00:00:00Z' if published else None,
        service_url=url if published else None, survey_url=url + '/survey' if published else None)
    report = dict(id='r-ux', response_count=0, cutoff_response_id=0,
                  created_at='2026-10-11T00:00:00Z', stale=stale) if status == 'reviewed' or stale else None
    return dict(project=project, plan=None if status == 'draft' else PLAN, experiment=experiment,
                latest_report_meta=report, running_run_id=running)


@pytest.mark.parametrize('status,stale,stage,hint', [
    ('draft', False, 2, '生成计划'),
    ('planned', False, 2, '保存修改'),
    ('collecting', False, 3, '没有回答也可复盘'),
    ('reviewed', False, 4, '验证报告已完成'),
    ('collecting', True, 3, '已有新反馈'),
])
def test_stage_navigation_and_guidance(status, stale, stage, hint):
    data = context(status, stale)
    html = render('workspace.html', **data)
    tags = Tags(html)
    links = [attrs for tag, attrs in tags.items if tag == 'a' and 'ws-step' in attrs.get('class', '').split()]
    assert [a['href'] for a in links] == ['#project-overview', '#validation-plan', '#feedback',
        '/projects/p-ux/report' if data['latest_report_meta'] else '#review']
    assert [i + 1 for i, a in enumerate(links) if a.get('aria-current') == 'step'] == [stage]
    assert hint in html and '下一步' in html
    for a in links:
        if a['href'].startswith('#'):
            assert any(attrs.get('id') == a['href'][1:] for _, attrs in tags.items)
    if stage == 2:
        assert 'ws-step-done' not in links[2]['class'] and 'ws-step-done' not in links[3]['class']
    if stale:
        assert tags.by_testid('ws-step-report-stale')['href'] == '/projects/p-ux/report'
    assert 'data-testid="user-nav"' in html and '体验测试用户' in html
    assert 'PUBLIC_BASE_URL' not in html and 'INVALID_STATE' not in html


@pytest.mark.parametrize('status', ['draft', 'planned', 'collecting', 'reviewed'])
def test_running_disables_writes_but_keeps_navigation(status):
    html = render('workspace.html', **context(status, running='run-ux'))
    tags = Tags(html)
    assert '请等待完成后再编辑、生成或发布' in html
    assert 'disabled' in tags.by_testid('plan-generate')
    assert 'disabled' in tags.by_testid('review-generate')
    if status == 'planned':
        assert 'disabled' in tags.by_testid('experiment-publish')


def test_share_controls_use_real_backend_urls():
    data = context('collecting')
    tags = Tags(render('workspace.html', **data))
    for kind in ('service', 'survey'):
        link = tags.by_testid(kind + '-url')
        assert 'readonly' in link and link['value'] == data['experiment'][kind + '_url']
        assert tags.by_testid('open-' + kind + '-url')['href'] == link['value']
        assert tags.by_testid('copy-' + kind + '-status')['id'] == link['aria-describedby']


@pytest.mark.parametrize('published,running', [(False, None), (True, None), (False, 'run-ux')])
def test_draft_back_links_and_locks(published, running):
    data = context('collecting' if published else 'planned', running=running)
    html = render('experiment_edit.html', experiment=data['experiment'],
                  is_locked=published, running_run_id=running)
    tags = Tags(html)
    assert tags.by_testid('draft-back')['href'] == '/projects/p-ux#experiment-draft'
    assert '第 2 步 / 共 4 步' in html
    if published or running:
        assert tags.by_testid('experiment-form')['data-locked'] == 'true'
        assert all('readonly' in attrs for tag, attrs in tags.items if tag in ('input', 'textarea') and attrs.get('type') != 'checkbox')
    if published:
        assert '已发布，题目与文案已锁定' in html
        assert not any(attrs.get('data-testid') == 'experiment-save' for _, attrs in tags.items)
    else:
        assert '保存 → 预览' in html
        assert ('disabled' in tags.by_testid('experiment-save')) == bool(running)


@pytest.mark.parametrize('status,anchor', [('draft', '#project-actions'), ('planned', '#project-actions'), ('collecting', '#review')])
def test_empty_report_guides_to_real_prerequisites(status, anchor):
    html = render('report.html', project=context(status)['project'], report=None, running_run_id=None)
    tags = Tags(html)
    assert '第 4 步 / 共 4 步' in html and '尚无成功复盘报告' in html
    assert tags.by_testid('report-back-cta')['href'] == '/projects/p-ux' + anchor
    assert tags.by_testid('report-back-feedback')['href'] == '/projects/p-ux#statistics'
    assert tags.by_testid('report-back-review')['href'] == '/projects/p-ux#review'
    assert 'disabled' in tags.by_testid('report-download')


def test_running_without_report_does_not_claim_saved_snapshot():
    html = render('report.html', project=context('collecting')['project'], report=None, running_run_id='run-ux')
    assert '目前尚无已保存的复盘报告' in html
    assert '本页显示的是已保存的报告快照' not in html
