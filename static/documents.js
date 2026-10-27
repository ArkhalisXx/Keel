/* Poll metadata only; original files and extracted content remain private. */
(() => {
  const cards = [...document.querySelectorAll('[data-document-status]')];
  let delay = 3000;
  async function poll() {
    let active = false;
    for (const card of cards) {
      if (card.dataset.active !== 'true') continue;
      active = true;
      try {
        const response = await fetch(card.dataset.documentStatus, {headers: {'Accept': 'application/json'}});
        if (response.status === 404 || response.redirected) { card.dataset.active = 'false'; continue; }
        if (!response.ok) continue;
        const data = await response.json();
        const pill = card.querySelector('.status-pill');
        pill.textContent = data.label;
        pill.dataset.state = data.status;
        card.querySelector('.processing-progress').textContent = data.status === 'processing'
          ? `${data.processed} / ${data.total} pages`
          : data.status === 'ready' ? `${data.total} pages / sections`
          : data.status === 'queued' ? 'Waiting for the worker. Retry is available after two minutes.' : '';
        card.querySelector('.processing-error').textContent = data.error;
        card.querySelector('.retry-form').hidden = !data.can_retry;
        const ocr = card.querySelector('.ocr-note');
        ocr.hidden = !data.ocr_pages;
        ocr.textContent = `${data.ocr_pages} page(s) read with OCR. Verify equations and symbols against the original.`;
        card.dataset.active = String(['queued', 'processing'].includes(data.status));
      } catch (_) { delay = Math.min(delay * 2, 15000); }
    }
    if (active) window.setTimeout(poll, delay);
  }
  window.setTimeout(poll, delay);
})();
