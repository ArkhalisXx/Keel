/* Poll private metadata; serverless deployments process one queued file per POST. */
(() => {
  const cards = [...document.querySelectorAll('[data-document-status]')];
  const paused = new WeakSet();
  let delay = 3000;
  let polling = false;
  let timer;
  const schedule = () => { clearTimeout(timer); timer = window.setTimeout(poll, delay); };

  function render(card, data) {
    const serverless = Boolean(card.dataset.serverlessProcess);
    const pill = card.querySelector('.status-pill');
    pill.textContent = data.label;
    pill.dataset.state = data.status;
    card.querySelector('.processing-progress').textContent = data.status === 'processing'
      ? `${data.processed} / ${data.total} pages`
      : data.status === 'ready' ? `${data.total} pages / sections`
      : data.status === 'queued' ? (serverless ? 'Keep this page open to process this document.' : 'Waiting for the worker. Retry is available after two minutes.') : '';
    card.querySelector('.processing-error').textContent = data.error || (paused.has(card) && data.status === 'queued'
      ? 'The connection interrupted processing. Check its status, then choose Continue processing.' : '');
    card.querySelector('.retry-form').hidden = !data.can_retry;
    const resume = card.querySelector('[data-process-resume]');
    if (resume) resume.hidden = !paused.has(card) || data.status !== 'queued';
    const useDocument = card.querySelector('.import-document-link');
    if (useDocument) useDocument.hidden = data.status !== 'ready';
    const ocr = card.querySelector('.ocr-note');
    ocr.hidden = !data.ocr_pages;
    ocr.textContent = `${data.ocr_pages} page(s) read with OCR. Verify equations and symbols against the original.`;
    card.dataset.active = String(['queued', 'processing'].includes(data.status));
  }

  async function processQueued(card) {
    const csrf = card.querySelector('[name=csrfmiddlewaretoken]')?.value;
    if (!csrf) throw new Error('Missing CSRF token');
    card.querySelector('.processing-progress').textContent = 'Reading your document… Keep this page open.';
    const response = await fetch(card.dataset.serverlessProcess, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Accept': 'application/json', 'X-CSRFToken': csrf},
    });
    if (response.redirected || response.status === 404) { card.dataset.active = 'false'; return; }
    if (!response.ok) throw new Error('Processing did not finish');
    render(card, await response.json());
  }

  async function poll() {
    if (polling) return;
    if (document.hidden) { schedule(); return; }
    polling = true;
    try {
      for (const card of cards) {
        if (card.dataset.active !== 'true') continue;
        try {
          const response = await fetch(card.dataset.documentStatus, {credentials: 'same-origin', headers: {'Accept': 'application/json'}});
          if (response.status === 404 || response.redirected) { card.dataset.active = 'false'; continue; }
          if (!response.ok) continue;
          const data = await response.json();
          render(card, data);
          if (card.dataset.serverlessProcess && data.status === 'queued' && !paused.has(card) && !document.hidden) {
            try { await processQueued(card); }
            catch (_) {
              // A failed response may hide a successful request. Poll status,
              // but require an explicit click before issuing another POST.
              paused.add(card);
              render(card, data);
            }
          }
        } catch (_) { delay = Math.min(delay * 2, 15000); }
      }
    } finally {
      polling = false;
      if (cards.some(card => card.dataset.active === 'true')) schedule();
    }
  }
  cards.forEach(card => card.querySelector('[data-process-resume]')?.addEventListener('click', () => {
    paused.delete(card);
    card.querySelector('[data-process-resume]').hidden = true;
    clearTimeout(timer);
    poll(); // Always read status again before another processing request.
  }));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(timer); poll(); } });
  schedule();
})();
