/* Small, progressive actions: text stays readable when JavaScript is unavailable. */
(() => {
  'use strict';

  function addCopyButton(container, rawText, label = 'Copy answer') {
    if (!container || typeof rawText !== 'string' || !rawText.trim()) return;
    const tools = document.createElement('div');
    tools.className = 'study-answer-tools';
    tools.dataset.noMath = '';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'study-copy-answer';
    button.textContent = label;
    const status = document.createElement('span');
    status.className = 'study-copy-status';
    status.setAttribute('role', 'status');
    tools.append(button, status);
    container.append(tools);
    let resetTimer;
    let fallback;
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      clearTimeout(resetTimer);
      button.disabled = true;
      try {
        if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
        // Keep the original LaTeX/plain text, not KaTeX's generated visual/MathML DOM.
        await navigator.clipboard.writeText(rawText);
        button.textContent = 'Copied ✓';
        status.textContent = 'Answer copied.';
        if (fallback) fallback.hidden = true;
        resetTimer = setTimeout(() => {
          button.textContent = label;
          status.textContent = '';
        }, 2200);
      } catch (_) {
        button.textContent = label;
        if (!fallback) {
          fallback = document.createElement('textarea');
          fallback.className = 'study-copy-fallback';
          fallback.readOnly = true;
          fallback.rows = 4;
          fallback.setAttribute('aria-label', 'Answer text to copy');
          fallback.value = rawText;
          tools.after(fallback);
        }
        fallback.hidden = false;
        fallback.focus({preventScroll: true});
        fallback.select();
        status.textContent = 'Text selected below. Use your device’s Copy command.';
      } finally {
        button.disabled = false;
      }
    });
  }

  document.querySelectorAll('.notice').forEach(notice => {
    if (notice.querySelector('[data-dismiss-notice]')) return;
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'study-notice-dismiss';
    dismiss.dataset.dismissNotice = '';
    dismiss.setAttribute('aria-label', 'Dismiss notification');
    dismiss.textContent = '×';
    notice.classList.add('study-notice-dismissible');
    notice.append(dismiss);
    dismiss.addEventListener('click', () => {
      const hadFocus = notice.contains(document.activeElement);
      notice.remove();
      if (hadFocus) document.getElementById('workspace-content')?.focus({preventScroll: true});
    });
  });

  window.KeelStudy = Object.freeze({addCopyButton});
})();
