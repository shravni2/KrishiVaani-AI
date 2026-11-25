import os, hmac, hashlib, io, json, uuid, re
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Request, BackgroundTasks
from fastapi.responses import FileResponse, Response, PlainTextResponse
from pydantic import BaseModel, Field, HttpUrl
from typing import Literal
from . import core
import httpx
app=FastAPI(title='KrishiVaani AI',version='1.0')
STATIC=Path(__file__).parent/'static'
class Query(BaseModel):
    question:str=Field(min_length=1,max_length=600)
    language:Literal['en','hi','mr']='mr'
    intent:Literal['auto','knowledge','weather','mandi']='auto'
    location:str=Field(default='',max_length=100)
    market:str=Field(default='',max_length=100)
    commodity:str=Field(default='',max_length=100)
class Doc(BaseModel):
    source:str=Field(min_length=2,max_length=200)
    url:HttpUrl
    publisher:str
    region:str
    crop:str
    season:str='all'
    date:str
    licence:str
    intent:Literal['crop','scheme']
    topic:list[str]=Field(min_length=1,max_length=30)
    terms:list[str]=Field(min_length=1,max_length=30)
    answer:dict[Literal['en','hi','mr'],str]
    evidence:str=Field(min_length=10,max_length=20000)

def admin(key):
    expected=os.getenv('ADMIN_KEY','')
    if not expected or not hmac.compare_digest(expected,key or ''): raise HTTPException(403,'Set ADMIN_KEY in .env and enter it in the admin panel.')
@app.get('/')
def home(): return FileResponse(STATIC/'index.html')
@app.get('/health')
def health(): return {'status':'ok','service':'krishivaani-api'}
@app.get('/languages')
def languages(): return {'languages':[{'code':'hi','name':'Hindi'},{'code':'mr','name':'Marathi'},{'code':'en','name':'English'}]}
@app.post('/api/ask')
def ask(q:Query): return core.answer(q.question,q.language,q.intent,q.location,q.market,q.commodity)
@app.post('/api/speech')
def speech(q:Query):
    try: return Response(tts(q.question,q.language),media_type='audio/mpeg')
    except Exception: raise HTTPException(503,'Speech service unavailable; use browser speech or text.')
def tts(text,lang):
    from gtts import gTTS
    buf=io.BytesIO(); gTTS(text=text[:600],lang=lang,timeout=12).write_to_fp(buf); return buf.getvalue()
def transcribe(data,filename,lang=None):
    key=os.getenv('GROQ_API_KEY')
    if not key: raise HTTPException(503,'Set GROQ_API_KEY or use browser microphone.')
    form={'model':'whisper-large-v3-turbo','response_format':'verbose_json'}
    if lang: form['language']=lang
    r=httpx.post('https://api.groq.com/openai/v1/audio/transcriptions',headers={'Authorization':'Bearer '+key},data=form,files={'file':(filename,data)},timeout=35); r.raise_for_status(); j=r.json()
    segments=j.get('segments') or []
    weak=not segments or any(s.get('avg_logprob',0)<-1 or s.get('no_speech_prob',0)>.6 for s in segments)
    return {'text':j.get('text','').strip(),'weak':weak,'language':j.get('language','')}
@app.post('/api/transcribe')
async def audio(file:UploadFile=File(...),language:Literal['en','hi','mr']='mr'):
    data=await file.read(10*1024*1024+1)
    if len(data)>10*1024*1024: raise HTTPException(413,'Audio must be below 10 MB.')
    try: return transcribe(data,Path(file.filename or 'audio.ogg').name,language)
    except httpx.HTTPError: raise HTTPException(503,'Transcription provider unavailable.')
@app.get('/api/admin/documents')
def list_docs(x_admin_key:str=Header(default='')):
    admin(x_admin_key); return core.documents()
@app.post('/api/admin/documents')
def add_doc(d:Doc,x_admin_key:str=Header(default='')):
    admin(x_admin_key)
    if set(d.answer)!=set(core.LANGS) or any(not x.strip() or len(x)>500 for x in d.answer.values()): raise HTTPException(422,'Provide all three short answers, maximum 500 characters each.')
    if any(not t.strip() for t in d.topic+d.terms): raise HTTPException(422,'Empty keywords are not allowed.')
    payload=d.model_dump(mode='json'); payload.update(id=uuid.uuid4().hex,approved=False,version=1,reviewed='pending',language='en/hi/mr'); core.save_doc(payload); return payload
@app.post('/api/admin/documents/{doc_id}/approve')
def approve(doc_id:str,approved:bool=True,x_admin_key:str=Header(default='')):
    admin(x_admin_key); d=next((d for d in core.documents() if d['id']==doc_id),None)
    if not d: raise HTTPException(404,'Unknown document')
    d.update(approved=approved,version=d['version']+1,reviewed=core.date.today().isoformat()); core.save_doc(d); return d
@app.post('/api/admin/pdf')
async def pdf(file:UploadFile=File(...),x_admin_key:str=Header(default='')):
    admin(x_admin_key); data=await file.read(5*1024*1024+1)
    if len(data)>5*1024*1024: raise HTTPException(413,'PDF limit 5 MB.')
    from pypdf import PdfReader
    try: text='\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(data)).pages[:20])[:20000]
    except Exception: raise HTTPException(400,'Unreadable PDF')
    return {'text':text,'note':'Review extracted text and enter short answers and source metadata; scanned PDFs require OCR separately.'}
@app.get('/api/admin/metrics')
def metrics(x_admin_key:str=Header(default='')):
    admin(x_admin_key)
    with core.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT intent,decision,COUNT(*) AS count,ROUND(AVG(latency),1) AS mean_ms FROM logs GROUP BY intent,decision')]; failures=[dict(r) for r in db.execute("SELECT reason,COUNT(*) AS count FROM logs WHERE decision!='answer' GROUP BY reason")]
    return {'interaction_counts':rows,'failure_reasons':failures,'privacy':'No questions, recordings or phone numbers stored in logs.'}
@app.delete('/api/admin/logs')
def clear_logs(x_admin_key:str=Header(default='')):
    admin(x_admin_key)
    with core.connect() as db: db.execute('DELETE FROM logs')
    return {'deleted':True}

def wa_config():
    keys=['WA_TOKEN','WA_PHONE_ID','WA_VERIFY_TOKEN','WA_APP_SECRET','WA_GRAPH_VERSION']; vals=[os.getenv(k,'') for k in keys]
    if not all(vals): raise HTTPException(503,'WhatsApp credentials not configured')
    return vals
@app.get('/webhook')
def verify(request:Request):
    _,_,verify_token,_,_=wa_config(); p=request.query_params
    if p.get('hub.mode')=='subscribe' and hmac.compare_digest(p.get('hub.verify_token',''),verify_token): return PlainTextResponse(p.get('hub.challenge',''))
    raise HTTPException(403,'Invalid verification token')
@app.post('/webhook')
async def webhook(request:Request,bg:BackgroundTasks):
    _,_,_,secret,_=wa_config(); raw=await request.body()
    sig='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig,request.headers.get('x-hub-signature-256','')): raise HTTPException(403,'Invalid signature')
    body=json.loads(raw)
    for e in body.get('entry',[]):
        for change in e.get('changes',[]):
            for msg in change.get('value',{}).get('messages',[]): bg.add_task(process_wa,msg)
    return {'ok':True}
def process_wa(msg):
    token,phone,_,secret,version=wa_config(); base='https://graph.facebook.com/'+version; headers={'Authorization':'Bearer '+token}; sender=msg.get('from',''); mid=msg.get('id','')
    if not sender or not mid: return
    sid=hmac.new(secret.encode(),sender.encode(),hashlib.sha256).hexdigest()
    with core.connect() as db:
        if db.execute('SELECT id FROM seen WHERE id=?',(mid,)).fetchone(): return
        db.execute('INSERT INTO seen VALUES (?)',(mid,)); session=db.execute('SELECT lang FROM sessions WHERE id=?',(sid,)).fetchone()
    def send(text):
        r=httpx.post(f'{base}/{phone}/messages',headers=headers,json={'messaging_product':'whatsapp','to':sender,'type':'text','text':{'body':text[:3500]}},timeout=20); r.raise_for_status()
    text=msg.get('text',{}).get('body','').strip(); cmd=text.casefold()
    if cmd in ('stop','delete'):
        with core.connect() as db: db.execute('DELETE FROM sessions WHERE id=?',(sid,))
        send('Consent removed. / सहमती रद्द.'); return
    if cmd in ('start mr','start hi','start en'):
        lang=cmd.split()[1]
        with core.connect() as db: db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(sid,lang))
        send('Ready / तयार. Weather: weather Pune | Mandi: mandi Pune; Onion | STOP to withdraw consent.'); return
    if not session:
        send('Voice uses external Groq transcription and Google speech. We store a pseudonymous language preference and aggregate logs, not recordings. Reply START MR, START HI or START EN to consent. STOP to withdraw.'); return
    lang=session['lang']; kind=msg.get('type'); loc=market=commodity=''
    try:
        if kind=='audio':
            r=httpx.get(f"{base}/{msg['audio']['id']}",headers=headers,timeout=15); r.raise_for_status(); url=r.json()['url']
            with httpx.stream('GET',url,headers=headers,timeout=20) as media:
                media.raise_for_status(); data=bytearray()
                for chunk in media.iter_bytes():
                    data.extend(chunk)
                    if len(data)>10*1024*1024: raise ValueError('audio too large')
            t=transcribe(bytes(data),'voice.ogg',lang)
            if t['weak'] or not t['text']: send(core.REFUSAL[lang]); return
            text=t['text']
        elif kind!='text': send('Send text or voice.'); return
        mode='auto'; q=text
        if text.lower().startswith('weather '): mode='weather'; loc=text[8:].strip()
        elif text.lower().startswith('mandi '):
            mode='mandi'; parts=text[6:].split(';',1)
            if len(parts)==2: market,commodity=[x.strip() for x in parts]
        elif core.intent(text)=='weather':
            # Known local city aliases for simple spoken requests.
            for city,aliases in {'Pune':['pune','पुणे'],'Nashik':['nashik','नाशिक','नासिक'],'Nagpur':['nagpur','नागपूर','नागपुर'],'Mumbai':['mumbai','मुंबई']}.items():
                if any(core.hit(core.norm(text),a) for a in aliases): loc=city; break
        out=core.answer(q,lang,mode,loc,market,commodity); names=', '.join(s['name'] for s in out['sources']); send(out['answer']+'\n'+ '\n'.join(s['url'] for s in out['sources']))
        if names: spoken=out['answer']+' '+names
        else: spoken=out['answer']
        mp3=tts(spoken,lang)
        r=httpx.post(f'{base}/{phone}/media',headers=headers,data={'messaging_product':'whatsapp'},files={'file':('reply.mp3',mp3,'audio/mpeg')},timeout=30); r.raise_for_status()
        r=httpx.post(f'{base}/{phone}/messages',headers=headers,json={'messaging_product':'whatsapp','to':sender,'type':'audio','audio':{'id':r.json()['id']}},timeout=20); r.raise_for_status()
    except Exception:
        # No personal content is logged; if the channel fails, the web UI remains available.
        try: send(core.REFUSAL[lang])
        except Exception: pass
