import json, re, os, time, sqlite3, unicodedata
from pathlib import Path
from contextlib import contextmanager
from datetime import date, datetime
import httpx
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
DATA = ROOT / 'backend' / 'data'
STORE = ROOT / 'storage'
STORE.mkdir(exist_ok=True)
DB = STORE / 'app.sqlite3'
LANGS = ('en','hi','mr')
KCC = 'https://dackkms.gov.in/account/aboutus.aspx'
REFUSAL = {'en':'I do not have enough verified evidence. Please contact a local KVK or Kisan Call Centre at 1800-180-1551.', 'hi':'मेरे पास पर्याप्त प्रमाणित जानकारी नहीं है। स्थानीय कृषि विज्ञान केंद्र या किसान कॉल सेंटर 1800-180-1551 से संपर्क करें।', 'mr':'माझ्याकडे पुरेशी पडताळलेली माहिती नाही. स्थानिक कृषी विज्ञान केंद्र किंवा किसान कॉल सेंटर 1800-180-1551 शी संपर्क करा.'}
CLARIFY = {'en':'Please supply the required location or market and commodity.', 'hi':'कृपया स्थान या मंडी और फसल का नाम बताएं।', 'mr':'कृपया ठिकाण किंवा बाजार आणि पिकाचे नाव सांगा.'}
CHEM = ['dose','dosage','pesticide','insecticide','fungicide','spray','chemical','ml/','g/l','दवा','दवाई','कीटनाशक','फवार','औषध','मात्रा','डोस','रसायन','छिड़क','छिडक']
UNKNOWN = ['ignore previous','ignore instructions','system prompt','deadline','last date','exact date','अंतिम तारीख','शेवटची तारीख','कधी जमा','कब आए','कब आये','which variety','कौन सी किस्म','कोणता वाण','yellow','पीले','पिवळ','पीला','पीली','diagnose','diagnosis']

def norm(s): return unicodedata.normalize('NFC',s).casefold().strip()
def hit(q, term):
    t=norm(term)
    return bool(re.search(r'(?<![a-z0-9])'+re.escape(t)+r'(?![a-z0-9])',q)) if t.isascii() else t in q

@contextmanager
def connect():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, intent TEXT, decision TEXT, latency REAL, reason TEXT); CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, lang TEXT); CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY);')
    try:
        with db: yield db
    finally: db.close()

def documents():
    base={d['id']:d for d in json.loads((DATA/'knowledge.json').read_text(encoding='utf-8'))}
    with connect() as db:
        for row in db.execute('SELECT payload FROM documents'):
            d=json.loads(row['payload']); base[d['id']]=d
    return list(base.values())

def save_doc(d):
    with connect() as db: db.execute('INSERT OR REPLACE INTO documents VALUES (?,?)',(d['id'],json.dumps(d,ensure_ascii=False)))

def retrieve(q, docs=None):
    q=norm(q); results=[]
    for d in documents() if docs is None else docs:
        if not d.get('approved'): continue
        topic=sum(hit(q,t) for t in d['topic']); terms=sum(hit(q,t) for t in d['terms'])
        if topic:
            results.append((d, min(1.0, .40+.25*min(terms,2)+.05*min(topic-1,2))))
    return sorted(results,key=lambda r:r[1],reverse=True)[:5]

def intent(q):
    q=norm(q)
    if any(hit(q,t) for t in ['weather','rain forecast','temperature','forecast','हवामान','मौसम','तापमान','पाऊस पडेल','बारिश होगी','बारिश कब']): return 'weather'
    if any(hit(q,t) for t in ['mandi','price','market rate','market price','बाजार भाव','बाजारभाव','मंडी','भाव','कीमत','बाजार दर']): return 'mandi'
    return 'knowledge'

CITY_ALIASES={'Pune':['pune','पुणे','पुण्यात'],'Nashik':['nashik','nasik','नाशिक','नासिक'],'Nagpur':['nagpur','नागपूर','नागपुर'],'Mumbai':['mumbai','मुंबई'],'Lasalgaon':['lasalgaon','लासलगाव'],'Solapur':['solapur','सोलापूर','सोलापुर'],'Ahmednagar':['ahmednagar','अहमदनगर','अहिल्यानगर'],'Kolhapur':['kolhapur','कोल्हापूर','कोल्हापुर']}
COMMODITY_ALIASES={'Onion':['onion','कांदा','कांद्य','प्याज','प्याज़'],'Cotton':['cotton','कापूस','कापस','कपास'],'Soyabean':['soybean','soyabean','सोयाबीन'],'Tomato':['tomato','टमाटर','टोमॅटो'],'Wheat':['wheat','गेहूं','गहू']}
def infer(q, mapping):
    matches=[name for name,terms in mapping.items() if any(hit(q,t) for t in terms)]
    return matches[0] if len(matches)==1 else ''

def source(name,url): return {'name':name,'url':url}
def result(lang,kind,decision,text,reason,score=0,sources=None,ids=None):
    return {'language':lang,'intent':kind,'decision':decision,'answer':text,'reason':reason,'confidence':score,'sources':sources or [],'evidence_ids':ids or []}
def refuse(lang,kind,reason,score=0,ids=None): return result(lang,kind,'refuse',REFUSAL[lang],reason,score,[source('Kisan Call Centre',KCC)],ids)

def weather(location,lang):
    if not location: return result(lang,'weather','clarify',CLARIFY[lang],'missing_location')
    with httpx.Client(timeout=12) as c:
        r=c.get('https://geocoding-api.open-meteo.com/v1/search',params={'name':location,'count':10,'language':'en','format':'json'}); r.raise_for_status()
        matches=[x for x in r.json().get('results',[]) if x.get('country_code')=='IN']
        if not matches: return refuse(lang,'weather','location_not_found')
        matches.sort(key=lambda x: x.get('admin1')!='Maharashtra'); p=matches[0]
        r=c.get('https://api.open-meteo.com/v1/forecast',params={'latitude':p['latitude'],'longitude':p['longitude'],'current':'temperature_2m,precipitation','daily':'precipitation_probability_max','forecast_days':1,'timezone':'Asia/Kolkata'}); r.raise_for_status(); v=r.json()
    now=v['current']; rain=v['daily']['precipitation_probability_max'][0]; temp=now['temperature_2m']; stamp=now['time'].replace('T',' '); name=f"{p['name']}, {p.get('admin1','India')}"
    if rain is None or temp is None: return refuse(lang,'weather','incomplete_data')
    msg={'en':f'{name}: temperature {temp}°C at {stamp} IST. Today maximum rain probability: {rain}%.', 'hi':f'{name}: {stamp} भारतीय समय पर तापमान {temp} डिग्री सेल्सियस। आज बारिश की अधिकतम संभावना {rain} प्रतिशत।', 'mr':f'{name}: {stamp} भारतीय वेळेनुसार तापमान {temp} अंश सेल्सिअस. आज पावसाची कमाल शक्यता {rain} टक्के.'}[lang]
    return result(lang,'weather','answer',msg,'live_data',sources=[source('Open-Meteo','https://open-meteo.com/en/docs')])

def mandi(market,commodity,lang):
    if not market or not commodity: return result(lang,'mandi','clarify',CLARIFY[lang],'missing_market_or_commodity')
    key=os.getenv('DATA_GOV_API_KEY')
    if not key: return refuse(lang,'mandi','missing_api_key')
    url='https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070'
    r=httpx.get(url,params={'api-key':key,'format':'json','limit':100,'filters[state]':'Maharashtra','filters[market]':market,'filters[commodity]':commodity},timeout=15); r.raise_for_status()
    rows=[]
    for rec in r.json().get('records',[]):
        if norm(str(rec.get('market','')))!=norm(market) or norm(str(rec.get('commodity','')))!=norm(commodity): continue
        try:
            dt=datetime.strptime(rec['arrival_date'],'%d/%m/%Y').date(); price=float(rec['modal_price'])
            if price>0 and dt<=date.today(): rows.append((dt,price,rec))
        except (ValueError,KeyError,TypeError): continue
    if not rows: return refuse(lang,'mandi','no_market_records')
    dt,price,rec=max(rows,key=lambda x:x[0]); variety=rec.get('variety',''); stamp=dt.isoformat()
    msg={'en':f'{market}, {commodity} ({variety}): reported modal price ₹{price:g} per quintal on {stamp}. Not a guaranteed current sale price.', 'hi':f'{market}, {commodity} ({variety}): {stamp} को दर्ज प्रचलित भाव {price:g} रुपये प्रति क्विंटल। यह आज का निश्चित बिक्री भाव नहीं है।', 'mr':f'{market}, {commodity} ({variety}): {stamp} रोजी नोंदवलेला प्रचलित भाव {price:g} रुपये प्रति क्विंटल. हा आजचा निश्चित विक्रीभाव नाही.'}[lang]
    return result(lang,'mandi','answer',msg,'dated_market_record',sources=[source('AGMARKNET / Data.gov.in','https://data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi')])

def answer(q,lang='mr',kind='auto',location='',market='',commodity='',docs=None,log=True):
    start=time.perf_counter(); lang=lang if lang in LANGS else 'mr'; q=norm(q); kind=intent(q) if kind=='auto' else kind
    try:
        if not q or any(hit(q,t) for t in CHEM+UNKNOWN): out=refuse(lang,kind,'unsafe_or_unsupported')
        elif kind=='weather': out=weather(location or infer(q,CITY_ALIASES),lang)
        elif kind=='mandi': out=mandi(market or infer(q,CITY_ALIASES),commodity or infer(q,COMMODITY_ALIASES),lang)
        else:
            ranked=retrieve(q,docs); ids=[d['id'] for d,s in ranked]; score=ranked[0][1] if ranked else 0
            if score<.65 or (len(ranked)>1 and abs(score-ranked[1][1])<.06): out=refuse(lang,'knowledge','weak_or_ambiguous_evidence',score,ids)
            else:
                d=ranked[0][0]; out=result(lang,d['intent'],'answer',d['answer'][lang],'approved_evidence',score,[source(d['source'],d['url'])],ids)
    except (httpx.HTTPError,ValueError,KeyError,TypeError): out=refuse(lang,kind,'data_service_unavailable')
    out['latency_ms']=round((time.perf_counter()-start)*1000,1)
    if log:
        with connect() as db: db.execute('INSERT INTO logs(intent,decision,latency,reason) VALUES(?,?,?,?)',(out['intent'],out['decision'],out['latency_ms'],out['reason']))
    return out
