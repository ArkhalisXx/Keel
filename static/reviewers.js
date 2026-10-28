(() => {
  'use strict';
  const panel = document.querySelector('[data-reviewer-status]');
  if (!panel) return;
  const progress = panel.querySelector('[data-generation-progress]');
  const count = panel.querySelector('[data-generation-count]');
  const total = panel.querySelector('[data-generation-total]');
  const message = panel.querySelector('[data-generation-message]');
  let stopped = false;
  async function check() {
    if (stopped) return;
    try {
      const response = await fetch(panel.dataset.reviewerStatus, {headers: {Accept: 'application/json'}});
      if (response.redirected || response.status === 404) { stopped = true; return; }
      if (response.ok) {
        const data = await response.json();
        if (['ready', 'failed'].includes(data.status) || (data.can_retry && panel.dataset.stalled !== 'true')) {
          location.reload();
          return;
        }
        if (Number.isInteger(data.generated_count) && Number.isInteger(data.requested_count)) {
          if (progress) { progress.max = data.requested_count; progress.value = data.generated_count; }
          if (count) count.textContent = String(data.generated_count);
          if (total) total.textContent = String(data.requested_count);
        }
        if (message) message.textContent = data.status === 'queued'
          ? 'Waiting for the next batch…'
          : 'Your AI provider is preparing the next batch…';
      } else if (message) {
        message.textContent = 'Progress is temporarily unavailable. Your background job continues; checking again shortly.';
      }
    } catch (_) {
      if (message) message.textContent = 'Connection interrupted. Checking progress again shortly…';
    }
    setTimeout(check, 5000);
  }
  setTimeout(check, 3000);
})();
