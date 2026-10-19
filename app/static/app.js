const csrf = document.querySelector('meta[name="csrf-token"]').content;
const sourceBlock = document.getElementById(window.location.hash.slice(1));
if (sourceBlock?.tagName === 'DETAILS') {
  sourceBlock.open = true;
  sourceBlock.scrollIntoView();
}
if (location.pathname === '/documents') {
  const params = new URLSearchParams(location.search);
  const expanded = (params.get('expanded') || '').split(',').filter(Boolean);
  for (const id of expanded) {
    const row = document.getElementById(id);
    if (row) for (const detail of row.querySelectorAll('details')) detail.open = true;
  }
  const restoreScroll = Number(params.get('restore_scroll'));
  if (Number.isFinite(restoreScroll) && restoreScroll > 0) requestAnimationFrame(() => window.scrollTo(0, restoreScroll));
  for (const link of document.querySelectorAll('.document-row a[href*="/versions/"]')) {
    link.addEventListener('click', () => {
      try {
        const target = new URL(link.href, location.origin);
        const returnValue = target.searchParams.get('return_to');
        if (!returnValue) return;
        const returnUrl = new URL(returnValue, location.origin);
        const openRows = [...document.querySelectorAll('.document-row')].filter(row => [...row.querySelectorAll('details')].some(detail => detail.open)).map(row => row.id);
        if (openRows.length) returnUrl.searchParams.set('expanded', openRows.join(','));
        returnUrl.searchParams.set('restore_scroll', String(Math.round(window.scrollY)));
        target.searchParams.set('return_to', returnUrl.pathname + returnUrl.search + returnUrl.hash);
        link.href = target.href;
      } catch (_) { /* Keep the server-rendered local destination. */ }
    });
  }
}
for (const form of document.querySelectorAll('form:not(#chat-form)')) {
  form.addEventListener('submit', (event) => {
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
    form.dataset.submitting = 'true';
    form.setAttribute('aria-busy', 'true');
    const button = event.submitter || form.querySelector('button[type="submit"],button:not([type])');
    if (button) { button.disabled = true; button.textContent = 'Түр хүлээнэ үү…'; }
  });
}
const reviewCheckbox = document.querySelector('#reviewed-checkbox');
const stickyApprove = document.querySelector('#sticky-approve');
if (reviewCheckbox && stickyApprove) {
  const syncReview = () => { stickyApprove.disabled = !reviewCheckbox.checked; };
  reviewCheckbox.addEventListener('change', syncReview);
  syncReview();
}
const backToTop = document.querySelector('#back-to-top');
if (backToTop) {
  let queued = false;
  const updateBackToTop = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      const header = document.querySelector('#page-header');
      const visible = header ? header.getBoundingClientRect().bottom > 0 : window.scrollY < 160;
      backToTop.hidden = visible;
      queued = false;
    });
  };
  window.addEventListener('scroll', updateBackToTop, {passive:true});
  window.addEventListener('resize', updateBackToTop);
  updateBackToTop();
  backToTop.addEventListener('click', () => window.scrollTo({top:0, behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}));
}
const chat = document.querySelector('#chat-form');
const questionField = document.querySelector('#question');
if (chat && questionField) questionField.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chat.requestSubmit(document.querySelector('#ask-button'));
  }
});
if (chat) chat.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = document.querySelector('#ask-button');
  const status = document.querySelector('#chat-status');
  button.disabled = true;
  status.textContent = 'Нотолгоо хайж, хариулт боловсруулж байна…';
  try {
    const question = document.querySelector('#question').value;
    const response = await fetch('/chat/messages', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({question, return_to:window.location.pathname + window.location.search, top_k:document.querySelector('#top-k').value, document_id:document.querySelector('#document-filter').value,
        language:document.querySelector('#language-filter').value, retrieval_only:document.querySelector('#retrieval-only').checked})});
    const data = await response.json();
    if (!data.id) throw new Error(data.error || 'Хүсэлт амжилтгүй боллоо. Дахин оролдоно уу.');
    document.querySelector('#welcome')?.remove();
    const article = document.createElement('article'); article.className = 'exchange';
    const heading = document.createElement('h3'); heading.textContent = question;
    const badge = document.createElement('span'); badge.className='badge'; badge.textContent=({answered:'хариулсан',insufficient_evidence:'нотолгоо хангалтгүй',retrieved:'хайлт дууссан',error:'алдаа'})[data.status] || data.status;
    const answer = document.createElement('p'); answer.className='answer'; answer.textContent=data.answer || 'Хайлт дууслаа. Доорх эрэмбэлсэн нотолгоог шалгана уу.';
    const link = document.createElement('a'); link.href=data.details_url; link.textContent='Эх сурвалж, хугацааг харах ↗';
    article.append(heading,badge,answer,link);
    if (data.error) {const error=document.createElement('p'); error.className='notice error'; error.textContent=data.error; article.append(error);}
    const details = document.createElement('details'); const summary = document.createElement('summary');
    summary.textContent=`${data.trace.length} хэсэг олдсон · ${Math.round(data.timings.total_ms)} мс`; details.append(summary);
    for (const hit of data.trace) {const pre=document.createElement('pre'); const score=hit.rrf_score == null ? (hit.distance == null ? '' : ` · зай ${hit.distance.toFixed(4)}`) : ` · RRF ${hit.rrf_score.toFixed(5)}`; pre.textContent=`#${hit.rank} ${hit.title}${score}\n${hit.text}`; details.append(pre);}
    article.append(details); document.querySelector('#messages').append(article);
    if (response.ok) document.querySelector('#question').value='';
    status.textContent=response.ok?'Дараагийн асуултаа бичнэ үү.':'Дээрх алдааг шалгана уу. Таны асуулт хэвээр үлдлээ.';
  } catch(error) {status.textContent=error.message || 'Хүсэлт амжилтгүй боллоо. Дахин оролдоно уу.';}
  finally {button.disabled=false;}
});

for (const field of document.querySelectorAll('input,textarea,select')) {
  field.addEventListener('invalid', () => {
    if (field.validity.valueMissing) field.setCustomValidity('Энэ талбарыг бөглөнө үү.');
    else if (field.validity.tooLong) field.setCustomValidity('Оруулсан текстийн урт хэтэрсэн байна.');
    else if (field.validity.typeMismatch || field.validity.badInput) field.setCustomValidity('Оруулсан утгаа шалгана уу.');
    else field.setCustomValidity('Оруулсан мэдээллээ шалгана уу.');
  });
  field.addEventListener('input', () => field.setCustomValidity(''));
  field.addEventListener('change', () => field.setCustomValidity(''));
}
