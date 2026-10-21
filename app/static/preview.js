import * as pdfjsLib from './vendor/pdfjs/build/pdf.min.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/build/pdf.worker.min.mjs';

const workspace = document.querySelector('#preview-workspace');
if (workspace) {
  const host = workspace.querySelector('[data-preview-page-host]');
  const scroll = workspace.querySelector('[data-preview-scroll]');
  const status = workspace.querySelector('[data-preview-status]');
  const pageInput = workspace.querySelector('[data-preview-page]');
  const ocrButton = workspace.querySelector('[data-preview-action="ocr-region"]');
  const applyButton = workspace.querySelector('[data-preview-action="apply-proposals"]');
  const proposalHost = workspace.querySelector('[data-preview-proposals]');
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const state = {pdf:null, page:1, pageCount:1, scale:1, fit:true, map:null, selectedBlock:null,
    region:null, manual:false, proposals:new Map(), searchGeneration:0, rendering:false};
  const prepareUrl = workspace.dataset.prepareUrl;
  const pdfUrl = workspace.dataset.pdfUrl;
  const mapUrl = workspace.dataset.mapUrl;
  const previewPost = async (url, body) => {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      let message = payload.error || `Үйлдэл амжилтгүй боллоо (HTTP ${response.status}).`;
      if (response.status === 400 && message === 'Энэ хүсэлтийг гүйцэтгэх эрхгүй эсвэл хүссэн мэдээлэл олдсонгүй.') {
        message = 'Хуудасны аюулгүй байдлын токен хуучирсан байна. Хуудсыг Ctrl+F5-аар шинэчлээд дахин оролдоно уу.';
      }
      throw new Error(message);
    }
    return payload;
  };

  async function loadMap(page) {
    const response = await fetch(`${mapUrl}?page=${encodeURIComponent(page)}`, {headers:{'Accept':'application/json'}, cache:'no-store'});
    if (!response.ok) throw new Error('Байршлын зураг олдсонгүй. Preview-г дахин бэлтгэнэ үү.');
    state.map = await response.json();
    for (const proposal of state.map.proposals || []) state.proposals.set(proposal.id, proposal);
    renderProposalList();
  }

  function rectElement(viewport, rect, className, label='') {
    const converted = viewport.convertToViewportRectangle(rect);
    const left = Math.min(converted[0], converted[2]);
    const top = Math.min(converted[1], converted[3]);
    const width = Math.max(1, Math.abs(converted[2] - converted[0]));
    const height = Math.max(2, Math.abs(converted[3] - converted[1]));
    const element = document.createElement('button');
    element.type = 'button';
    element.className = `preview-mark ${className}`;
    element.style.left = `${left}px`; element.style.top = `${top}px`;
    element.style.width = `${width}px`; element.style.height = `${height}px`;
    element.setAttribute('aria-label', label || 'Баримтын тэмдэглэгээ');
    element.title = label;
    return element;
  }

  function chooseBlock(blockId, {scrollText=false, openDisclosure=true}={}) {
    if (blockId == null) return;
    state.selectedBlock = String(blockId);
    const detail = workspace.querySelector(`.extract-block[data-block-id="${CSS.escape(state.selectedBlock)}"]`);
    if (detail) {
      if (openDisclosure) detail.open = true;
      detail.classList.add('is-selected');
      for (const other of workspace.querySelectorAll('.extract-block.is-selected')) if (other !== detail) other.classList.remove('is-selected');
      if (scrollText) detail.scrollIntoView({block:'center', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    }
    if (state.map) drawMarks();
  }

  function drawMarks() {
    if (!state.map || !host.firstElementChild) return;
    for (const mark of host.querySelectorAll('.preview-mark,.preview-search-mark')) mark.remove();
    const page = state.map;
    const viewport = host._viewport;
    if (!viewport) return;
    const enabled = workspace.querySelector('[data-preview-highlights]').checked;
    for (const mapping of page.mapped || []) {
      if (mapping.status !== 'mapped') continue;
      const selected = String(mapping.block) === state.selectedBlock;
      if (!enabled && !selected) continue;
      for (const rect of mapping.rects || []) {
        const mark = rectElement(viewport, rect, selected ? 'mark-selected' : 'mark-mapped', `${mapping.block}-р хэсэг`);
        mark.addEventListener('click', event => { event.stopPropagation(); chooseBlock(mapping.block, {scrollText:true}); });
        host.append(mark);
      }
    }
    for (const region of page.regions || []) {
      const label = region.kind === 'page_candidate' ? 'Текстийн хэмжээ бага тул OCR-оор шалгах боломжтой хуудас' : 'OCR шалгах боломжтой зураг; текст байгаа нь баталгаагүй';
      const mark = rectElement(viewport, region.rect, 'mark-ocr', label);
      mark.dataset.regionId = region.id;
      mark.addEventListener('click', event => {
        event.stopPropagation(); state.region = {region_id:region.id}; ocrButton.disabled = false;
        for (const item of host.querySelectorAll('.mark-ocr')) item.classList.toggle('is-region-selected', item === mark);
      });
      host.append(mark);
    }
    for (const mapping of page.mapped || []) if (mapping.status !== 'mapped') {
      const badge = document.createElement('span'); badge.className = 'preview-map-uncertain';
      badge.textContent = `Хэсэг ${mapping.block}: байрлал тодорхойгүй`;
      host.append(badge);
    }
    for (const proposal of page.proposals || []) {
      const mark = rectElement(viewport, proposal.rect, 'mark-proposal', 'OCR саналын бүс');
      mark.addEventListener('click', event => { event.stopPropagation(); });
      host.append(mark);
      for (const word of proposal.words || []) {
        const wordMark = rectElement(viewport, word.rect, 'mark-proposal-word', `${word.text} · engine confidence ${Math.round(word.confidence)}%`);
        wordMark.disabled = true;
        host.append(wordMark);
      }
    }
    const search = workspace.querySelector('[data-preview-search]').value.trim().toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,'');
    if (search) for (const line of page.lines || []) if (line.text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,'').includes(search)) {
      const mark = rectElement(viewport, line.rect, 'preview-search-mark', 'Хайлтын илэрц');
      mark.disabled = true;
      host.append(mark);
    }
  }

  async function renderPage(number=state.page) {
    if (!state.pdf || state.rendering) return;
    state.rendering = true;
    state.page = Math.max(1, Math.min(state.pageCount, Number(number) || 1));
    pageInput.value = String(state.page); pageInput.max = String(state.pageCount);
    try {
      // Mapping is an enhancement. A stale or unavailable sidecar must never
      // prevent the source PDF itself from being shown for review.
      let mapWarning = '';
      try {
        await loadMap(state.page);
      } catch (error) {
        state.map = null;
        mapWarning = ` Байршлын тэмдэглэгээ хараахан боломжгүй байна: ${error.message || 'mapping олдсонгүй'}`;
      }
      const page = await state.pdf.getPage(state.page);
      const available = Math.max(260, scroll.clientWidth - 28);
      const unitViewport = page.getViewport({scale:1});
      const requestedScale = state.fit ? Math.min(2, available / unitViewport.width) : state.scale;
      const pixelLimitScale = Math.sqrt(25_000_000 / Math.max(1, unitViewport.width * unitViewport.height)) / Math.min(window.devicePixelRatio || 1, 2);
      const scale = Math.min(requestedScale, pixelLimitScale);
      state.scale = scale;
      const viewport = page.getViewport({scale});
      host.replaceChildren(); host.style.width = `${viewport.width}px`; host.style.height = `${viewport.height}px`;
      host._viewport = viewport;
      const canvas = document.createElement('canvas');
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
      host.append(canvas);
      const context = canvas.getContext('2d', {alpha:false});
      await page.render({canvasContext:context, viewport, transform:ratio === 1 ? null : [ratio,0,0,ratio,0,0], background:'#fff'}).promise;
      const textContent = await page.getTextContent();
      const textLayer = document.createElement('div'); textLayer.className = 'textLayer';
      textLayer.style.setProperty('--scale-factor', String(scale)); host.append(textLayer);
      const text = new pdfjsLib.TextLayer({textContentSource:textContent, container:textLayer, viewport});
      await text.render();
      textLayer.addEventListener('click', event => {
        const point = viewport.convertToPdfPoint(event.clientX - host.getBoundingClientRect().left, event.clientY - host.getBoundingClientRect().top);
        const candidates = [];
        for (const mapping of state.map?.mapped || []) for (const rect of mapping.rects || []) {
          const [left,bottom,right,top] = rect;
          const dx = point[0] < left ? left-point[0] : point[0] > right ? point[0]-right : 0;
          const dy = point[1] < bottom ? bottom-point[1] : point[1] > top ? point[1]-top : 0;
          candidates.push({block:mapping.block,distance:dx*dx+dy*dy,contains:dx===0&&dy===0});
        }
        candidates.sort((a,b) => Number(b.contains)-Number(a.contains) || a.distance-b.distance || Number(a.block)-Number(b.block));
        if (candidates[0] && (candidates[0].contains || candidates[0].distance < 225)) chooseBlock(candidates[0].block, {scrollText:true});
      });
      drawMarks(); status.textContent = `Хуудас ${state.page} / ${state.pageCount}. Сонгож болох ROI зурахын тулд “Хэсэг сонгох”-ыг дарна уу.${mapWarning}`;
    } catch (error) {
      status.textContent = error.message || 'Хуудсыг харуулж чадсангүй.';
    } finally { state.rendering = false; }
  }

  async function openPreview({switchPane=false}={}) {
    try {
      status.textContent = 'Preview болон текстийн байрлалыг локал бэлтгэж байна…';
      await previewPost(prepareUrl, {});
      const loading = pdfjsLib.getDocument({url:pdfUrl, cMapUrl:'/static/vendor/pdfjs/cmaps/', cMapPacked:true,
        standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/', iccUrl:'/static/vendor/pdfjs/iccs/', wasmUrl:'/static/vendor/pdfjs/wasm/', isEvalSupported:false});
      state.pdf = await loading.promise; state.pageCount = state.pdf.numPages;
      if (switchPane && matchMedia('(max-width: 899px)').matches) selectPane('preview');
      await renderPage(1);
      return true;
    } catch (error) { status.textContent = error.message || 'Preview бэлтгэж чадсангүй.'; return false; }
  }

  function renderProposalList() {
    proposalHost.replaceChildren();
    for (const proposal of state.proposals.values()) {
      const row = document.createElement('label'); row.className = 'ocr-proposal-row check';
      const checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.value=proposal.id;
      const text = document.createElement('span');
      text.textContent = `Санал · ${proposal.page}-р хуудас · engine confidence ${proposal.mean_confidence == null ? 'тодорхойгүй' : `${proposal.mean_confidence}%`} — ${proposal.text}`;
      row.append(checkbox,text); proposalHost.append(row);
    }
    applyButton.hidden = state.proposals.size === 0;
  }

  async function findAcrossPages(query) {
    const needle=query.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,'');
    if (!needle || !state.pdf) return;
    const start=state.page;
    for (let step=0;step<state.pageCount;step++) {
      const pageNumber=((start-1+step)%state.pageCount)+1;
      const page=await state.pdf.getPage(pageNumber);
      const content=await page.getTextContent();
      const text=content.items.map(item=>item.str||'').join(' ').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu,'');
      if (text.includes(needle)) {
        await renderPage(pageNumber);
        status.textContent=`Хайлтын илэрц ${pageNumber}-р хуудсанд байна.`;
        return;
      }
    }
    status.textContent='Энэ үг preview-ийн хуудсуудаас олдсонгүй.';
  }

  function selectPane(pane) {
    workspace.querySelectorAll('.preview-tab').forEach(item => { const active=item.dataset.previewTab===pane; item.classList.toggle('is-active',active); item.setAttribute('aria-selected',String(active)); });
    workspace.querySelector('[data-preview-pane="text"]').hidden = pane !== 'text';
    workspace.querySelector('[data-preview-pane="preview"]').hidden = pane !== 'preview';
  }
  // Tabs replace the split only on narrow screens. Do not hide the preview on
  // desktop, where both synchronized panes must remain visible together.
  const paneMode = matchMedia('(max-width: 899px)');
  const syncPaneMode = () => {
    if (paneMode.matches) selectPane('text');
    else {
      workspace.querySelector('[data-preview-pane="text"]').hidden = false;
      workspace.querySelector('[data-preview-pane="preview"]').hidden = false;
    }
  };
  syncPaneMode();
  paneMode.addEventListener?.('change', syncPaneMode);
  workspace.querySelectorAll('.preview-tab').forEach(tab => tab.addEventListener('click', () => {
    const pane=tab.dataset.previewTab;
    selectPane(pane);
    if (pane === 'preview' && !state.pdf) openPreview(); else if (pane === 'preview') renderPage();
  }));

  for (const block of workspace.querySelectorAll('.extract-block')) {
    block.querySelector('summary')?.addEventListener('click', () => {
      // Let <details>/<summary>'s native activation toggle disclosure after
      // this click handler. Opening it here makes the browser immediately
      // toggle it closed again, hiding the chunk text.
      chooseBlock(block.dataset.blockId, {openDisclosure:false});
      const showBlock = () => {
        if (matchMedia('(max-width: 899px)').matches) selectPane('preview');
        const mapping = state.map?.mapped?.find(item => String(item.block) === block.dataset.blockId);
        if (mapping?.page) renderPage(mapping.page);
      };
      if (state.pdf) showBlock();
      else openPreview({switchPane:true}).then(ready => { if (ready) showBlock(); });
    });
  }

  workspace.querySelector('[data-preview-action="prepare"]').addEventListener('click', () => openPreview());
  workspace.querySelector('[data-preview-action="prev"]').addEventListener('click', () => renderPage(state.page-1));
  workspace.querySelector('[data-preview-action="next"]').addEventListener('click', () => renderPage(state.page+1));
  pageInput.addEventListener('change', () => renderPage(pageInput.value));
  workspace.querySelector('[data-preview-action="fit"]').addEventListener('click', () => { state.fit=true; renderPage(); });
  workspace.querySelector('[data-preview-action="zoom-in"]').addEventListener('click', () => { state.fit=false; state.scale=Math.min(3,state.scale*1.2||1.2); renderPage(); });
  workspace.querySelector('[data-preview-action="zoom-out"]').addEventListener('click', () => { state.fit=false; state.scale=Math.max(.4,state.scale/1.2||.8); renderPage(); });
  workspace.querySelector('[data-preview-action="fullscreen"]').addEventListener('click', async () => {
    try { if (!document.fullscreenElement) await workspace.requestFullscreen(); else await document.exitFullscreen(); }
    catch (_) { status.textContent='Бүтэн дэлгэцийн горим боломжгүй байна.'; }
  });
  workspace.querySelector('[data-preview-highlights]').addEventListener('change', drawMarks);
  workspace.querySelector('[data-preview-search]').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer=setTimeout(drawMarks,160); });
  workspace.querySelector('[data-preview-search]').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); findAcrossPages(event.currentTarget.value); }
  });

  workspace.querySelector('[data-preview-action="manual-region"]').addEventListener('click', () => {
    state.manual = !state.manual;
    host.classList.toggle('is-drawing', state.manual);
    status.textContent = state.manual ? 'Хуудсан дээр чирж OCR хийх тэгш өнцөгт зурна уу.' : 'Бүс сонгох горим хаагдлаа.';
  });
  let drag = null;
  host.addEventListener('pointerdown', event => {
    if (!state.manual || !host._viewport) return;
    const bounds=host.getBoundingClientRect(); drag={x1:event.clientX-bounds.left,y1:event.clientY-bounds.top};
    event.preventDefault();
    host.setPointerCapture(event.pointerId);
  });
  host.addEventListener('pointerup', event => {
    if (!drag || !host._viewport) return;
    const bounds=host.getBoundingClientRect(); const x2=event.clientX-bounds.left,y2=event.clientY-bounds.top;
    const points=[host._viewport.convertToPdfPoint(drag.x1,drag.y1),host._viewport.convertToPdfPoint(x2,y2)];
    const rect=[Math.min(points[0][0],points[1][0]),Math.min(points[0][1],points[1][1]),Math.max(points[0][0],points[1][0]),Math.max(points[0][1],points[1][1])];
    drag=null; state.region={rect}; state.manual=false; host.classList.remove('is-drawing'); ocrButton.disabled=false;
    const mark=rectElement(host._viewport,rect,'mark-ocr is-region-selected','Гараар сонгосон OCR бүс'); host.append(mark);
  });
  ocrButton.addEventListener('click', async () => {
    if (!state.region) return;
    ocrButton.disabled=true; status.textContent='Сонгосон хэсгийг энэ төхөөрөмж дээр OCR хийж байна…';
    try {
      const proposal=await previewPost(workspace.dataset.ocrUrl,{page:state.page,...state.region});
      state.proposals.set(proposal.id,proposal); renderProposalList(); drawMarks();
      status.textContent='OCR саналыг үүсгэлээ. Эхтэй тулгаад шинэ review хувилбар үүсгэх бол саналаа сонгоно уу.';
    } catch(error) { status.textContent=error.message; }
    finally { ocrButton.disabled=!state.region; }
  });
  applyButton.addEventListener('click', async () => {
    const ids=[...proposalHost.querySelectorAll('input:checked')].map(input=>input.value);
    if (!ids.length) { status.textContent='Шинэ review хувилбарт оруулах OCR саналаа сонгоно уу.'; return; }
    applyButton.disabled=true; status.textContent='Шинэ шалгах хувилбар үүсгэж байна…';
    try {
      const result=await previewPost(workspace.dataset.applyUrl,{proposal_ids:ids,return_to:workspace.dataset.returnTo});
      window.location.assign(result.url);
    } catch(error) { status.textContent=error.message; applyButton.disabled=false; }
  });

  const divider=workspace.querySelector('#preview-divider');
  let resizeStart=null;
  divider.addEventListener('pointerdown', event => { resizeStart={x:event.clientX,left:workspace.querySelector('.extracted-pane').getBoundingClientRect().width}; divider.setPointerCapture(event.pointerId); });
  divider.addEventListener('pointerup', event => { if (!resizeStart) return; const split=workspace.querySelector('.preview-split'); const width=split.getBoundingClientRect().width; const left=Math.max(320,Math.min(width-396,resizeStart.left+event.clientX-resizeStart.x)); split.style.setProperty('--left-pane',`${left}px`); resizeStart=null; if (state.pdf) renderPage(); });
  divider.addEventListener('keydown', event => { if (!['ArrowLeft','ArrowRight'].includes(event.key)) return; event.preventDefault(); const split=workspace.querySelector('.preview-split'); const current=parseFloat(getComputedStyle(split).getPropertyValue('--left-pane'))||split.clientWidth*.48; split.style.setProperty('--left-pane',`${current+(event.key==='ArrowRight'?24:-24)}px`); if (state.pdf) renderPage(); });
  window.addEventListener('resize', () => { if (state.pdf && state.fit) renderPage(); });
}
