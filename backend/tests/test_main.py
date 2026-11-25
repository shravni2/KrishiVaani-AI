import json
from fastapi.testclient import TestClient
from app.main import app
from app import core
import evaluate
client=TestClient(app)

def test_health(): assert client.get('/health').json()['status']=='ok'
def test_languages(): assert {x['code'] for x in client.get('/languages').json()['languages']}=={'hi','mr','en'}
def test_home(): assert 'KrishiVaani' in client.get('/').text
def test_grounded():
    r=client.post('/api/ask',json={'question':'PM Kisan benefit amount','language':'en'}).json(); assert r['decision']=='answer' and r['sources'][0]['url'].startswith('https://www.pmkisan')
def test_refusal(): assert client.post('/api/ask',json={'question':'cotton pesticide dosage','language':'en'}).json()['decision']=='refuse'
def test_missing_location(): assert core.answer('weather','en',log=False)['decision']=='clarify'
def test_admin_denied(monkeypatch):
    monkeypatch.setenv('ADMIN_KEY','secret'); assert client.get('/api/admin/documents').status_code==403

def test_admin_metrics(monkeypatch):
    monkeypatch.setenv('ADMIN_KEY','secret'); assert client.get('/api/admin/metrics',headers={'X-Admin-Key':'secret'}).status_code==200

def test_unapproved_excluded():
    docs=json.loads((core.DATA/'knowledge.json').read_text(encoding='utf-8')); docs[0]['approved']=False; assert core.answer('PM Kisan benefit amount','en',docs=docs,log=False)['decision']=='refuse'
def test_mandi_key(monkeypatch):
    monkeypatch.delenv('DATA_GOV_API_KEY',raising=False); assert core.answer('mandi price','en',market='Pune',commodity='Onion',log=False)['reason']=='missing_api_key'
def test_benchmark():
    r=evaluate.run(); assert r['recall_at_5_percent']>=85 and r['correct_refusal_percent']>=90

def test_weather_mock(monkeypatch):
    class R:
        def __init__(self,d): self.d=d
        def raise_for_status(self): pass
        def json(self): return self.d
    class C:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def get(self,url,**kw):
            return R({'results':[{'country_code':'IN','name':'Pune','admin1':'Maharashtra','latitude':18,'longitude':73}]}) if 'geocoding' in url else R({'current':{'temperature_2m':25,'time':'2026-11-24T12:00'},'daily':{'precipitation_probability_max':[20]}})
    monkeypatch.setattr(core.httpx,'Client',C); r=core.weather('Pune','en'); assert r['decision']=='answer' and '25' in r['answer'] and '2026-11-24' in r['answer']

def test_mandi_mock(monkeypatch):
    monkeypatch.setenv('DATA_GOV_API_KEY','fake')
    class R:
        def raise_for_status(self): pass
        def json(self): return {'records':[{'market':'Pune','commodity':'Onion','arrival_date':'01/01/2024','modal_price':'1000','variety':'Local'},{'market':'Pune','commodity':'Onion','arrival_date':'02/01/2024','modal_price':'1200','variety':'Local'}]}
    monkeypatch.setattr(core.httpx,'get',lambda *a,**kw:R()); r=core.mandi('Pune','Onion','en'); assert '1200' in r['answer'] and '2024-01-02' in r['answer']
