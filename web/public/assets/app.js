'use strict';
const $ = id => document.getElementById(id);
let lastAnswer = '';
let busy = false;
const names = {'DOCUMENTATION.md':'AI agents & Google Maps','MCP_Documentation.md':'Building an MCP server'};
const node = (tag, cls, text) => { const el=document.createElement(tag); if(cls)el.className=cls; if(text!==undefined)el.textContent=text; return el; };
function inline(text, parent) {
  const pattern = /(`[^`]+`|\[Source: [^\]]+\]|\*\*[^*]+\*\*)/g;
  let offset=0;
  for(const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset,match.index)));
    const token=match[0];
    parent.append(token.startsWith('`')?node('code','',token.slice(1,-1)):token.startsWith('**')?node('strong','',token.slice(2,-2)):node('span','citation',token));
    offset=match.index+token.length;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}
function renderAnswer(text) {
  const box=$('answer-content'); box.replaceChildren();
  const parts=text.split(/```[^\n]*\n([\s\S]*?)```/g);
  parts.forEach((part,i)=> { if(i%2) {const pre=node('pre');pre.append(node('code','',part));box.append(pre);} else {part.split(/\n\n+/).filter(Boolean).forEach(p=>{const el=node('p');inline(p,el);box.append(el);});} });
}
async function loadLibrary() {
  try {
    const response=await fetch('/api/documents'); if(!response.ok) throw Error();
    const {documents}=await response.json(); $('document-library').replaceChildren();
    let count=0;
    documents.forEach(doc=> { count+=doc.chunks; const item=node('div','document'); const icon=node('span','document-icon','▤');icon.setAttribute('aria-hidden','true');const meta=node('div'); meta.append(node('strong','',names[doc.title]||doc.title),node('small','',`${doc.chunks} indexed passages · Markdown`)); item.append(icon,meta);$('document-library').append(item); });
    $('doc-count').textContent=documents.length; $('document-total').textContent=documents.length; $('chunk-total').textContent=count;
  } catch { $('document-library').replaceChildren(node('p','muted','Document details are temporarily unavailable.')); }
}
function reset() {
  if(busy)return;
  $('intro').hidden=false;$('conversation').hidden=true;$('suggestions').hidden=false;$('question').value='';$('status').className='status';$('status').textContent='Public demo · 3 questions per minute per visitor · 100 questions per day shared';$('question').focus();
}
async function ask(question) {
  if(busy||!question.trim())return;
  busy=true;$('submit-button').disabled=true;$('question').disabled=true;$('intro').hidden=true;$('suggestions').hidden=true;$('conversation').hidden=false;$('asked-question').textContent=question;$('answer-content').replaceChildren(node('div','loading','Finding relevant passages and preparing a cited answer…'));$('copy-answer').hidden=true;$('answer-meta').hidden=true;$('sources-section').hidden=true;$('status').textContent='Searching the documentation…';$('status').className='status';
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),115000);
  try {
    const response=await fetch('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question}),signal:controller.signal});
    let data;try{data=await response.json();}catch{throw Error('The server is temporarily unavailable. Please try again shortly.');}
    if(!response.ok) {const retry=response.headers.get('Retry-After');throw Error((typeof data.detail==='string'?data.detail:'Please enter a question between 1 and 2,000 characters.')+(retry?` Retry in ${retry} seconds.`:''));}
    lastAnswer=data.answer;renderAnswer(data.answer);$('copy-answer').hidden=false;$('answer-meta').hidden=false;$('answer-meta').textContent=`${data.refused?'No documented answer found':'Source labels verified'} · ${data.model} · ${data.elapsed_seconds}s`;
    $('sources').replaceChildren();data.sources.forEach(source=>{const details=node('details','source');const summary=node('summary','',source.section);summary.append(node('small','',`${source.document} · Passage ${source.rank}${source.subsection?' · '+source.subsection:''}`));details.append(summary,node('pre','',source.content));$('sources').append(details);});
    $('source-count').textContent=`${data.sources.length} passages`;$('sources-section').hidden=false;$('status').textContent='Ask another question, or expand a source to inspect the evidence.';$('question').value='';
  } catch(error) { $('answer-content').replaceChildren(node('p','', 'We couldn’t complete that question.'));$('status').textContent=error.name==='AbortError'?'The request took too long. Please try again.':error.message;$('status').className='status error'; }
  finally {clearTimeout(timeout);busy=false;$('submit-button').disabled=false;$('question').disabled=false;}
}
$('question-form').addEventListener('submit',e=>{e.preventDefault();ask($('question').value.trim());});
$('question').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();ask($('question').value.trim());}});
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{$('question').value=button.dataset.question;ask(button.dataset.question);}));
$('new-question').addEventListener('click',reset);
$('copy-answer').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(lastAnswer);$('copy-answer').textContent='Copied';setTimeout(()=>$('copy-answer').textContent='Copy answer',1800);}catch{$('copy-answer').textContent='Select text to copy';}});
loadLibrary();
