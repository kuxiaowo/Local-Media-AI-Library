from fastapi.testclient import TestClient
from localmedia.api import create_app


def test_ollama_capabilities_and_no_settings_mutation(tmp_path):
    app=create_app(tmp_path,background=False)
    calls=[]
    async def request(url,key,path,payload=None):
        calls.append((url,key,path))
        if path=='/api/tags':return {'models':[{'name':'vision'},{'name':'embed'}]}
        return {'capabilities':['vision','completion'] if payload['model']=='vision' else ['embedding']}
    app.state.worker.providers.request=request
    before=app.state.db.settings()
    with TestClient(app) as client:
        result=client.post('/api/services/models',json={'provider':'ollama','url':'http://localhost:11434'}).json()
    assert result['models'][0]['vision'] is True
    assert result['models'][1]['embedding'] is True
    assert result['models'][1]['vision'] is False
    assert app.state.db.settings()==before


def test_codex_pagination_and_login(tmp_path):
    app=create_app(tmp_path,background=False)
    class Codex:
        async def rpc(self,method,params):
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            if not params:return {'data':[{'model':'one','inputModalities':['text','image'], 'defaultReasoningEffort':'medium', 'supportedReasoningEfforts':[{'reasoningEffort':'high','description':'More reasoning'}]}],'nextCursor':'next'}
            return {'data':[{'model':'two','inputModalities':['text']}],'nextCursor':None}
    async def get():return Codex()
    app.state.worker.providers.get_codex=get
    with TestClient(app) as client:
        result=client.post('/api/services/models',json={'provider':'codex'}).json()
    assert result['logged_in'] is True
    assert [m['id'] for m in result['models']]==['one','two']
    assert result['models'][1]['vision'] is False
    assert result['models'][0]['default_effort']=='medium'
    assert result['models'][0]['efforts']==[{'value':'high','description':'More reasoning'}]


def test_discovery_secrets_scoped_to_saved_endpoint(tmp_path):
    app=create_app(tmp_path,background=False)
    app.state.db.set_setting('base_url','https://saved.example/v1')
    app.state.db.set_setting('api_key','private')
    keys=[]
    async def request(url,key,path,payload=None):
        keys.append(key)
        return {'data':[{'id':'available'}]}
    app.state.worker.providers.request=request
    with TestClient(app) as client:
        for url in ['https://saved.example/v1','https://other.example/v1']:
            response=client.post('/api/services/models',json={'provider':'openai','url':url})
            assert 'private' not in response.text
    assert keys==['private','']
