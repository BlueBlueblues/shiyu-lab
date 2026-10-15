"""Loopback-only synthetic Mock preview. Prompts for an isolated preview password."""
import sys,getpass,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from pydantic import SecretStr
from app.config import Settings
from app.main import create_app
from app.agents import runtime
from conftest import FakeModel
import uvicorn
if not sys.stdin.isatty():raise SystemExit('Run in your own interactive terminal')
username=input('Isolated preview username: ').strip()
from pydantic import TypeAdapter
from app.users import Username
TypeAdapter(Username).validate_python(username)
password=getpass.getpass('Isolated preview password (12-128 characters): ')
if not 12<=len(password)<=128:raise SystemExit('Invalid password length')
with tempfile.TemporaryDirectory(prefix='shiyu-preview-') as td:
    settings=Settings(app_env='test',database_path=Path(td)/'preview.sqlite3',llm_base_url='https://mock.invalid',llm_api_key=SecretStr('mock'),llm_model='mock-demo-offline',admin_user=username,admin_password=SecretStr(password),public_base_url='http://127.0.0.1:8012',mail_provider='disabled')
    runtime.call_model=FakeModel()
    print('Synthetic Mock preview: http://127.0.0.1:8012 ; stop with Ctrl-C. No real mail/model calls.')
    uvicorn.run(create_app(settings),host='127.0.0.1',port=8012,access_log=False)
