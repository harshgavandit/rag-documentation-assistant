"""HTTP and static interface for the hosted documentation assistant."""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import time
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request as URLRequest, build_opener

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from engine import get_index
from grounding import build_messages
from observability import Operations, OperationsUnavailable
import staging

ROOT=Path(__file__).resolve().parent
REFUSAL='I could not find an answer to this question in the provided documentation.'
REQUIRED=('OPENAI_API_KEY','UPSTASH_REDIS_REST_URL','UPSTASH_REDIS_REST_TOKEN')
ops=Operations.from_env()
SCRIPT="""
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('PTTL', KEYS[1])
if count >= tonumber(ARGV[1]) and ttl > 0 then return {0, 0, ttl} end
if ttl <= 0 then redis.call('SET', KEYS[1], 1, 'PX', ARGV[2]); return {1, tonumber(ARGV[1])-1, tonumber(ARGV[2])} end
count = redis.call('INCR', KEYS[1]); return {1, tonumber(ARGV[1])-count, ttl}
"""
app=FastAPI(title='RAG Documentation Assistant',docs_url=None,redoc_url=None,openapi_url=None)
# Vercel serves public/ from its CDN and removes it from the function bundle.
if (ROOT/'public/assets').is_dir():
    app.mount('/assets',StaticFiles(directory=ROOT/'public/assets'),name='assets')


class Question(BaseModel):
    """Bound question size before any paid work."""
    question:str=Field(min_length=1,max_length=2000)

    @field_validator('question')
    @classmethod
    def nonempty(cls,value:str) -> str:
        """Reject whitespace-only input."""
        if not value.strip():
            raise ValueError('Enter a question.')
        return value.strip()


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    helpful: StrictBool
    category: Literal['clear', 'incomplete', 'incorrect', 'citation_issue', 'other']


class SecurityMiddleware:
    """Bound request bodies and add headers without consuming downstream request streams."""
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        if scope['method']=='POST':
            origin=headers.get(b'origin',b'').decode()
            host=headers.get(b'host',b'').decode()
            if origin and origin.rstrip('/')!=scope.get('scheme','https')+'://'+host:
                return await JSONResponse({'detail':'This endpoint accepts requests from this site only.'},status_code=403)(scope,receive,send)
            parts=[]
            size=0
            while True:
                message=await receive()
                if message['type']=='http.disconnect':
                    return
                body=message.get('body',b'')
                size+=len(body)
                if size>8192:
                    return await JSONResponse({'detail':'Request too large.'},status_code=413)(scope,receive,send)
                parts.append(body)
                if not message.get('more_body',False):
                    break
            body=b''.join(parts)
            delivered=False
            original_receive=receive
            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered=True
                    return {'type':'http.request','body':body,'more_body':False}
                return await original_receive()
            receive=buffered_receive
        async def secure_send(message):
            if message['type']=='http.response.start':
                extra=[(b'x-content-type-options',b'nosniff'),(b'x-frame-options',b'DENY'),
                       (b'referrer-policy',b'strict-origin-when-cross-origin'),
                       (b'content-security-policy',b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
                if scope['path'].startswith('/api/'):
                    extra.append((b'cache-control',b'no-store'))
                message['headers']=list(message.get('headers',[]))+extra
            await send(message)
        return await self.app(scope,receive,secure_send)


app.add_middleware(SecurityMiddleware)


def configuration() -> None:
    """Fail without revealing missing secret values."""
    if any(not os.getenv(name,'').strip() for name in REQUIRED):
        raise HTTPException(503,'The assistant is awaiting server configuration.')


class NoCredentialRedirect(HTTPRedirectHandler):
    """Never forward provider or Redis authorization to a redirect destination."""
    def redirect_request(self,request,fp,code,message,headers,newurl):
        raise HTTPError(request.full_url,code,'Credential-bearing redirects are disabled.',headers,fp)


def post_json(url:str,payload:object,token:str,timeout:int=15) -> dict:
    """Send credentials only in headers; expose safe errors at the API boundary."""
    request=URLRequest(url,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with build_opener(NoCredentialRedirect()).open(request,timeout=timeout) as response:
        body=response.read(1048577)
    if len(body)>1048576:
        raise ValueError('Dependency response exceeds the supported size.')
    result=json.loads(body)
    if not isinstance(result,dict):
        raise ValueError('Dependency response must be a JSON object.')
    return result


def admit(identity:str) -> int:
    """Apply shared minute, per-client minute and global daily public-demo budgets."""
    prefix=os.getenv('RATE_LIMIT_PREFIX','rag:web:v1')
    policies=[('global',int(os.getenv('RATE_LIMIT_REQUESTS','20')),60),
              ('client:'+hashlib.sha256(identity.encode()).hexdigest()[:24],3,60),
              ('daily',int(os.getenv('DAILY_QUERY_LIMIT','100')),86400)]
    client_ttl_ms=None
    for scope,limit,window in policies:
        result=post_json(os.environ['UPSTASH_REDIS_REST_URL'].rstrip('/'),
                         ['EVAL',SCRIPT,1,prefix+':'+scope,limit,window*1000],os.environ['UPSTASH_REDIS_REST_TOKEN'])
        values=result.get('result')
        if 'error' in result or not isinstance(values,list) or len(values)!=3 or any(type(v)is not int for v in values) or values[0] not in (0,1) or values[2]<=0:
            raise HTTPException(503,'Rate-limit service is unavailable. Please try again later.')
        if not values[0]:
            delay=max(1,math.ceil(values[2]/1000))
            raise HTTPException(429,'The demo request limit has been reached. Please try again later.',headers={'Retry-After':str(delay)})
        if scope.startswith('client:'):
            client_ttl_ms=values[2]
    return client_ttl_ms


def answer(question:str,chunks:list[dict]) -> dict:
    """Generate using retrieved context and validate returned citation labels."""
    allowed={f"[Source: {c['metadata']['doc_title']}, Section: {c['metadata']['section_title']}]" for c in chunks}
    try:
        messages=build_messages(question,chunks,REFUSAL)
        result=staging.response(chunks,REFUSAL)
        if result is None:
            result=post_json('https://api.openai.com/v1/chat/completions',{
                'model':os.getenv('OPENAI_MODEL','gpt-6-luna'),'max_completion_tokens':int(os.getenv('OPENAI_MAX_COMPLETION_TOKENS','2048')),
                'messages':messages},os.environ['OPENAI_API_KEY'],timeout=60)
    except HTTPError as exc:
        logging.warning('OpenAI request failed: HTTP %s',exc.code)
        raise HTTPException(503,'The model provider is temporarily unavailable. Please try again later.') from None
    ops.record_usage(ops.current_event(),result.get('usage'))
    choices=result.get('choices')
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        raise HTTPException(502,'The model did not return a complete answer. Please try a more specific question.')
    choice=choices[0]
    message=choice.get('message')
    if not isinstance(message,dict) or not isinstance(message.get('content'),str):
        raise HTTPException(502,'The model did not return a complete answer. Please try a more specific question.')
    text=message['content'].strip()
    if not text or choice.get('finish_reason')!='stop':
        raise HTTPException(502,'The model did not return a complete answer. Please try a more specific question.')
    citations=re.findall(r'\[Source: .+?, Section: .+?\]',text)
    if text.strip('\'"')==REFUSAL:
        text,citations=REFUSAL,[]
    elif not citations or any(c not in allowed for c in citations):
        raise HTTPException(502,'The answer could not be verified against the retrieved source labels. Please rephrase your question.')
    return {'answer':text,'citations':list(dict.fromkeys(citations)),'refused':text==REFUSAL}


@app.get('/')
def home():
    """Serve the accessible, dependency-free web interface."""
    return FileResponse(ROOT/'public/index.html')


@app.get('/api/health')
def health():
    """Return safe configuration and corpus readiness without making paid calls."""
    configured=all(bool(os.getenv(name)) for name in REQUIRED)
    chunks=json.loads((ROOT/'data/corpus.json').read_text(encoding='utf-8'))
    return JSONResponse({'status':'ready' if configured else 'configuration_required','model':os.getenv('OPENAI_MODEL','gpt-6-luna'),
                         'documents':len({c['metadata']['doc_title'] for c in chunks}),'chunks':len(chunks),
                         'retrieval':'BM25 + MiniLM ONNX vectors + RRF'},status_code=200 if configured else 503)


@app.get('/api/documents')
def documents():
    """Expose the bundled public educational corpus, grouped by document."""
    chunks=json.loads((ROOT/'data/corpus.json').read_text(encoding='utf-8'))
    return {'documents':[{'title':name,'chunks':sum(c['metadata']['doc_title']==name for c in chunks),
                        'sections':list(dict.fromkeys(c['metadata']['section_title'] for c in chunks if c['metadata']['doc_title']==name))}
                        for name in sorted({c['metadata']['doc_title'] for c in chunks})]}


@app.post('/api/query')
def query(payload:Question,request:Request):
    """Admit, retrieve and generate one grounded answer, keeping secrets server-side."""
    scenario=staging.authorize(request)
    # Vercel overwrites x-vercel-forwarded-for at its edge; never trust client-supplied X-Forwarded-For.
    identity=(request.headers.get('x-vercel-forwarded-for','') if os.getenv('VERCEL') else '') or (request.client.host if request.client else 'unknown')
    started=time.monotonic()
    event=ops.begin_request(request.headers.get('x-ops-token',''))
    status_code,outcome=500,'error'
    staging_context=staging.enter(scenario)
    try:
        configuration()
        admission_ttl_ms=admit(identity)
        chunks=get_index().search(payload.question)
        result=answer(payload.question,chunks)
        status_code,outcome=200,'refused' if result['refused'] else 'answered'
    except HTTPException as error:
        status_code=error.status_code
        raise
    except (URLError,TimeoutError,OSError,ValueError,KeyError,IndexError):
        status_code=503
        logging.warning('RAG service request failed')
        raise HTTPException(503,'A required service is temporarily unavailable. Please try again shortly.') from None
    finally:
        ticket=ops.finish_request(event,status_code=status_code,outcome=outcome)
        staging.leave(staging_context)
    return dict(result,question=payload.question,model=os.getenv('OPENAI_MODEL','gpt-6-luna'),elapsed_seconds=round(time.monotonic()-started,2),
                feedback_ticket=ticket,
                sources=[{'document':c['metadata']['doc_title'],'section':c['metadata']['section_title'],
                          'subsection':c['metadata'].get('subsection_title',''),'content':c['content'],'rank':c['rank'],'score':c['score']} for c in chunks],
                **({'staging_admission':{'client_ttl_ms':admission_ttl_ms}} if scenario is not None else {}))


@app.get('/api/staging/provenance')
def staging_provenance(request:Request):
    return staging.provenance(request)


@app.get('/api/operations')
def operations(request:Request,days:int=Query(default=7,ge=1,le=7)):
    if not ops.is_enabled():
        raise HTTPException(404,'Not found.')
    if not ops.is_authorized(request.headers.get('x-ops-token','')):
        raise HTTPException(401,'Operations access requires authorization.')
    try:
        return ops.snapshot(days)
    except OperationsUnavailable:
        raise HTTPException(503,'Operations data is temporarily unavailable.') from None


@app.post('/api/feedback')
def feedback(payload:Feedback):
    if not ops.is_enabled():
        raise HTTPException(404,'Not found.')
    result=ops.feedback(payload.ticket,payload.helpful,payload.category)
    status={'accepted':200,'expired_or_used':409,'limited':429,'invalid':422,'unavailable':503}[result['status']]
    return JSONResponse(result,status_code=status,headers={'Retry-After':'60'} if status==429 else None)
