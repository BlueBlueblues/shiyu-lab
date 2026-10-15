import json
from app.db import connect_db
from app.agents.contracts import Opportunity, ExperimentDesign
from conftest import HEADERS,create_project

def test_independent_initial_roles(client,settings,model):
    pid=create_project(client)
    r=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={})
    assert r.status_code==200,r.text
    assert [r for r,_ in model.calls]==['shiyu','zhuoyu']
    assert 'plan' not in model.calls[0][1]['context']
    assert model.calls[1][1]['context']['opportunity']['hypotheses']
    with connect_db(settings.database_path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==4
        tasks=c.execute('SELECT * FROM agent_tasks ORDER BY started_at').fetchall()
        assert len(tasks)==2 and all(t['status']=='succeeded' for t in tasks)
        artifacts=c.execute('SELECT output_json FROM agent_artifacts ORDER BY created_at').fetchall()
        Opportunity.model_validate_json(artifacts[0][0]);ExperimentDesign.model_validate_json(artifacts[1][0])
        assert c.execute('SELECT count(*) FROM experiments').fetchone()[0]==1
