import sqlite3,json
from pathlib import Path
from app.db import init_db
from app.migrations import migrate_agent_tasks

def test_v3_upgrade_preserves_every_existing_row(tmp_path):
    # Construct v3 from the frozen baseline source, never from production user data.
    import subprocess, shutil, sys
    source=tmp_path/'baseline';source.mkdir()
    shutil.copytree(Path(__file__).parent/'fixtures/v3/app',source/'app')
    db=tmp_path/'legacy.sqlite3'
    script="from pathlib import Path; from app.db import init_db; init_db(Path("+repr(str(db))+"),'legacy-admin','isolated-fixture-password')"
    subprocess.run([sys.executable,'-B','-c',script],cwd=source,check=True)
    with sqlite3.connect(db) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==3
        from app import repositories
        from app.schemas import ProjectCreate,Plan
        from conftest import PROJECT,design
        from app.passwords import LEGACY_ADMIN_ID,hash_password
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        pid=repositories.create_project(c,ProjectCreate.model_validate(PROJECT))
        plan=Plan.model_validate(design({})['plan'])
        eid='00000000-0000-4000-8000-000000000002'
        repositories.save_experiment(c,pid,plan,eid,'synthetic-public-token-123456789','test')
        c.execute('UPDATE experiments SET published_at=? WHERE id=?',('test',eid))
        c.execute('INSERT INTO users(id,username,password_hash,created_at) VALUES (?,?,?,?)',('ordinary-user','legacy-user',hash_password('isolated-fixture-password'),'test'))
        c.execute('INSERT INTO sessions VALUES (?,?,?,?)',('synthetic-session','ordinary-user',1,9999999999))
        c.execute('INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES (?,?,?,?,?)',('legacy-run',pid,'review','succeeded','test'))
        c.execute('INSERT INTO tool_calls(id,run_id,tool_name,status,started_at,input_summary_json) VALUES (?,?,?,?,?,?)',('legacy-tool','legacy-run','analyze_experiment','succeeded','test','{}'))
        c.execute('INSERT INTO responses(experiment_id,client_submission_id,answers_json,consent,created_at) VALUES(?,?,?,?,?)',(eid,'synthetic-response','{}',1,'test'))
        c.execute('INSERT INTO reports(id,experiment_id,run_id,response_count,cutoff_response_id,statistics_json,review_json,created_at,plan_snapshot_json) VALUES(?,?,?,?,?,?,?,?,?)',('legacy-report',eid,'legacy-run',1,1,'{}','{}','test',plan.model_dump_json()))
        c.execute('INSERT INTO invitations(id,token_hash,created_by,created_at,expires_at,max_uses) VALUES(?,?,?,?,?,?)',('legacy-invite','synthetic-token-hash',LEGACY_ADMIN_ID,1,9999999999,1))
        c.commit()
        c.row_factory=None
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        columns={t:[r[1] for r in c.execute('PRAGMA table_info('+t+')')] for t in tables}
        before={t:c.execute('SELECT * FROM '+t).fetchall() for t in tables}
    init_db(db,'legacy-admin','isolated-fixture-password')
    with sqlite3.connect(db) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==4
        assert before=={t:c.execute('SELECT '+','.join(columns[t])+' FROM '+t).fetchall() for t in tables}
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        migrate_agent_tasks(c)
    backups=list(tmp_path.glob('*.pre-agents-*.bak'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as c:assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
