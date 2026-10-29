(() => {
  'use strict';
  const panel = document.querySelector('[data-reviewer-status]');
  if (!panel) return;
  const progress = panel.querySelector('[data-generation-progress]');
  const count = panel.querySelector('[data-generation-count]');
  const total = panel.querySelector('[data-generation-total]');
  const message = panel.querySelector('[data-generation-message]');
  const resume = panel.querySelector('[data-process-resume]');
  const serverless = Boolean(panel.dataset.serverlessProcess);
  let stopped = false;
  let checking = false;
  let paused = false;
  let timer;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(check, 5000); };

  function render(data) {
    if (['ready', 'failed'].includes(data.status) || (data.can_retry && panel.dataset.stalled !== 'true' && (!serverless || data.status === 'processing'))) {
      stopped = true;
      location.reload();
      return;
    }
    if (Number.isInteger(data.generated_count) && Number.isInteger(data.requested_count)) {
      if (progress) { progress.max = data.requested_count; progress.value = data.generated_count; }
      if (count) count.textContent = String(data.generated_count);
      if (total) total.textContent = String(data.requested_count);
    }
    if (resume) resume.hidden = !paused || data.status !== 'queued';
    if (message) message.textContent = paused && data.status === 'queued'
      ? 'The last request was interrupted. Check progress, then choose Continue generation.'
      : data.status === 'queued' ? (serverless ? 'Keep this page open to prepare the next batch…' : 'Waiting for the next batch…')
      : 'Your AI provider is preparing the next batch…';
  }

  async function processQueued() {
    const csrf = panel.querySelector('[name=csrfmiddlewaretoken]')?.value;
    if (!csrf) throw new Error('Missing CSRF token');
    if (message) message.textContent = 'Preparing the next batch… Keep this page open.';
    const response = await fetch(panel.dataset.serverlessProcess, {
      method: 'POST', credentials: 'same-origin',
      headers: {Accept: 'application/json', 'X-CSRFToken': csrf},
    });
    if (response.redirected || response.status === 404) { stopped = true; return; }
    if (!response.ok) throw new Error('Generation did not finish');
    render(await response.json());
  }

  async function check() {
    if (stopped || checking) return;
    if (document.hidden) { schedule(); return; }
    checking = true;
    try {
      const response = await fetch(panel.dataset.reviewerStatus, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
      if (response.redirected || response.status === 404) { stopped = true; return; }
      if (response.ok) {
        const data = await response.json();
        render(data);
        if (!stopped && serverless && data.status === 'queued' && !paused && !document.hidden) {
          try { await processQueued(); }
          catch (_) {
            // Never silently retry an AI request with an unknown outcome.
            // Continue checking metadata; another POST needs a user's click.
            paused = true;
            render(data);
          }
        }
      } else if (message) {
        message.textContent = serverless
          ? 'Progress is temporarily unavailable. Checking again shortly.'
          : 'Progress is temporarily unavailable. Your background job continues; checking again shortly.';
      }
    } catch (_) {
      if (message) message.textContent = 'Connection interrupted. Checking progress again shortly…';
    } finally {
      checking = false;
      if (!stopped) schedule();
    }
  }
  resume?.addEventListener('click', () => {
    paused = false;
    resume.hidden = true;
    clearTimeout(timer);
    check();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(timer); check(); } });
  timer = setTimeout(check, 3000);
})();
