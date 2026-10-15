"""Local Mock browser acceptance, loopback only, synthetic DB and no real email/model."""
import os,sys,tempfile,threading,time,json,socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import uvicorn
from pydantic import SecretStr
from playwright.sync_api import sync_playwright
from app.config import Settings
from app.main import create_app
from app.agents import runtime
from conftest import FakeModel,FakeMail

def run():
    root=Path(__file__).resolve().parents[1]/'artifacts'/'browser';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='shiyu-browser-') as td:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        settings=Settings(app_env='test',database_path=Path(td)/'test.sqlite3',llm_base_url='https://mock.invalid',llm_api_key=SecretStr('mock'),llm_model='mock-demo-offline',admin_user='browser-reviewer',admin_password=SecretStr('isolated-browser-fixture-123!'),public_base_url=base)
        runtime.call_model=FakeModel();app=create_app(settings)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        assert server.started
        results=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch()
                for label,width,height in [('phone',390,844),('tablet',768,1024),('desktop',1440,1000)]:
                    page=browser.new_page(viewport={'width':width,'height':height})
                    errors=[];page.on('pageerror',lambda e:errors.append(type(e).__name__))
                    page.goto(base+'/');assert page.get_by_test_id('four-agent-intro').is_visible()
                    page.screenshot(path=str(root/(label+'-home.png')),full_page=True)
                    page.goto(base+'/login');page.locator('#username').fill(settings.admin_user);page.locator('#password').fill(settings.admin_password.get_secret_value());page.get_by_test_id('auth-submit').click();page.wait_for_url('**/projects')
                    page.goto(base+'/projects/new')
                    for name,value in [('name','合成测试：'+label),('idea','大学生创业验证练习，合成测试'),('target_users','大学生'),('skills','教学设计'),('budget_cny','100'),('validation_goal','区分试用与购买意愿')]:
                        page.locator('[name="'+name+'"]').fill(value)
                    page.locator('[name="data_kind"]').select_option('test')
                    page.get_by_test_id('project-create').click()
                    page.get_by_test_id('agent-dashboard').wait_for(state='visible')
                    page.locator('[data-agent-run="shiyu"]').click()
                    page.wait_for_function("document.querySelector('[data-agent-state=shiyu]').textContent.includes('已完成')")
                    page.locator('[data-agent-run="zhuoyu"]').click()
                    page.wait_for_function("document.querySelector('[data-agent-state=zhuoyu]').textContent.includes('已完成')")
                    page.screenshot(path=str(root/(label+'-workspace.png')),full_page=True)
                    pid=page.url.rsplit('/',1)[1]
                    for role in ['shiyu','zhuoyu','jianyu','gongyu']:
                        page.goto(base+f'/projects/{pid}/agents/{role}')
                        assert page.locator('h1').is_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
                    # Prepare synthetic feedback through HTTP, then exercise actual iteration UI controls.
                    h={'Origin':base,'X-Requested-With':'FirstOrderLab','Accept':'application/json'}
                    detail=page.request.get(base+f'/api/projects/{pid}').json()['data']
                    eid=detail['experiment']['id']
                    assert page.request.post(base+f'/api/experiments/{eid}/publish',headers=h,data={}).status==200
                    detail=page.request.get(base+f'/api/projects/{pid}').json()['data']
                    token=detail['experiment']['survey_url'].split('/')[4]
                    from uuid import uuid4
                    for _ in range(2):
                        r=page.request.post(base+f'/api/public/e/{token}/responses',data={'client_submission_id':str(uuid4()),'consent':True,'answers':{'q1':'a','q2':'b','q3':['a'],'q4':'合成浏览器验收回答'}})
                        assert r.status==201
                    assert page.request.post(base+f'/api/projects/{pid}/review',headers=h,data={}).status==200
                    page.goto(base+f'/projects/{pid}/report')
                    assert page.get_by_test_id('report-statistics').is_visible()
                    page.screenshot(path=str(root/(label+'-report.png')),full_page=True)
                    page.goto(base+f'/projects/{pid}/rounds')
                    page.on('dialog',lambda dialog:dialog.accept())
                    page.get_by_test_id('iteration-generate').click()
                    page.get_by_test_id('iteration-proposal').wait_for(state='visible')
                    page.locator('#approved-goals').fill('人工批准的合成测试下一轮目标')
                    page.screenshot(path=str(root/(label+'-iteration.png')),full_page=True)
                    page.get_by_test_id('iteration-confirm').click()
                    page.wait_for_url('**/experiments/*/edit')
                    assert page.locator('h2').filter(has_text='琢玉 Agent').is_visible()
                    assert not errors,errors
                    results.append({'viewport':label,'width':width,'login':True,'create_project':True,'separate_roles':True,'role_pages':True,'iteration_approval':True,'next_round_draft':True,'javascript_errors':len(errors)})
                    page.close()
                browser.close()
        finally:
            server.should_exit=True;thread.join(5)
        (root/'report.json').write_text(json.dumps({'status':'passed','model':'mock','results':results},indent=2))
        print('browser_acceptance',json.dumps(results),flush=True)
if __name__=='__main__':run()
