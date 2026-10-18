const csrf = document.querySelector('meta[name="csrf-token"]').content;
const sourceBlock = document.getElementById(window.location.hash.slice(1));
if (sourceBlock?.tagName === 'DETAILS') {
  sourceBlock.open = true;
  sourceBlock.scrollIntoView();
}
for (const form of document.querySelectorAll('.busy-form')) {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button');
    button.disabled = true;
    button.textContent = 'Working…';
  });
}
const chat = document.querySelector('#chat-form');
if (chat) chat.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = document.querySelector('#ask-button');
  const status = document.querySelector('#chat-status');
  button.disabled = true;
  status.textContent = 'Retrieving evidence and preparing your response…';
  try {
    const question = document.querySelector('#question').value;
    const response = await fetch('/chat/messages', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({question, top_k:document.querySelector('#top-k').value, document_id:document.querySelector('#document-filter').value,
        language:document.querySelector('#language-filter').value, retrieval_only:document.querySelector('#retrieval-only').checked})});
    const data = await response.json();
    if (!data.id) throw new Error(data.error || 'Request failed. Refresh and retry.');
    document.querySelector('#welcome')?.remove();
    const article = document.createElement('article'); article.className = 'exchange';
    const heading = document.createElement('h3'); heading.textContent = question;
    const badge = document.createElement('span'); badge.className='badge'; badge.textContent=data.status;
    const answer = document.createElement('p'); answer.className='answer'; answer.textContent=data.answer || 'Retrieval complete. Inspect the ranked evidence below.';
    const link = document.createElement('a'); link.href=data.details_url; link.textContent='Inspect sources & timings ↗';
    article.append(heading,badge,answer,link);
    if (data.error) {const error=document.createElement('p'); error.className='notice error'; error.textContent=data.error; article.append(error);}
    const details = document.createElement('details'); const summary = document.createElement('summary');
    summary.textContent=`${data.trace.length} retrieved chunks · ${Math.round(data.timings.total_ms)} ms`; details.append(summary);
    for (const hit of data.trace) {const pre=document.createElement('pre'); pre.textContent=`#${hit.rank} ${hit.title} · distance ${hit.distance.toFixed(4)}\n${hit.text}`; details.append(pre);}
    article.append(details); document.querySelector('#messages').append(article);
    if (response.ok) document.querySelector('#question').value='';
    status.textContent=response.ok?'Ready for your next question.':'See the error above; your question is preserved.';
  } catch(error) {status.textContent=error.message;}
  finally {button.disabled=false;}
});
