(() => {
  const launcher = document.querySelector('.assistant-launcher');
  const drawer = document.querySelector('.assistant-drawer');
  let returnFocus;
  function closeDrawer() {
    if (!drawer) return;
    drawer.hidden = true;
    launcher.setAttribute('aria-expanded', 'false');
    (returnFocus || launcher).focus();
  }
  launcher?.addEventListener('click', () => {
    if (!drawer.hidden) { closeDrawer(); return; }
    returnFocus = document.activeElement;
    drawer.hidden = false;
    launcher.setAttribute('aria-expanded', 'true');
    drawer.querySelector('textarea').focus();
  });
  document.querySelector('.drawer-close')?.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawer && !drawer.hidden) closeDrawer(); });

  document.querySelectorAll('.assistant-panel').forEach(panel => {
    const form = panel.querySelector('.chat-form');
    const log = panel.querySelector('.chat-log');
    const feedback = panel.querySelector('.chat-feedback');
    const mode = form.elements.mode;
    const question = form.elements.question;
    let busy = false;
    question.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (!event.repeat) submitPrompt();
    });
    const append = (role, text) => {
      panel.querySelector('.chat-welcome')?.remove();
      const bubble = document.createElement('article');
      bubble.className = `chat-bubble chat-${role}`;
      const heading = document.createElement('strong'); heading.textContent = role === 'user' ? 'You' : 'Keel';
      const content = document.createElement('div'); content.textContent = text;
      bubble.append(heading, content); log.append(bubble); bubble.scrollIntoView({block: 'nearest'}); return bubble;
    };
    const sourceLink = (container, text, url, external) => {
      try {
        const target = new URL(url, window.location.origin);
        if (!['http:', 'https:'].includes(target.protocol)) return;
        if (!external && target.origin !== location.origin) return;
        const link = document.createElement('a'); link.href = target.href; link.textContent = text;
        if (external) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
        container.append(link);
      } catch (_) { /* Invalid provider URLs are never displayed as links. */ }
    };
    const sources = (bubble, data) => {
      const box = document.createElement('div'); box.className = 'chat-sources';
      for (const [i, item] of (data.document_sources || []).entries()) {
        const details = document.createElement('details'); const summary = document.createElement('summary');
        summary.textContent = `[D${i+1}] ${item.title} · Page/section ${item.page}${item.source === 'ocr' ? ' · OCR' : ''}`;
        const text = document.createElement('p'); text.textContent = item.text;
        details.append(summary, text); sourceLink(details, 'Download original', `/documents/${Number(item.id)}/`, false);box.append(details);
      }
      if (box.childNodes.length) bubble.append(box);
    };
    async function send(payload) {
      const response = await fetch(panel.dataset.chatUrl, {method:'POST', body:payload, headers:{'X-CSRFToken':form.elements.csrfmiddlewaretoken.value, 'Accept':'application/json'}});
      if (response.redirected || response.status === 403) throw new Error('Your session expired. Refresh the page and log in again.');
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The server could not respond. Try again shortly.');
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || 'Could not complete this request.'); error.data = data;throw error;
      }
      return data;
    }
    try {
      const initial = JSON.parse(document.getElementById('assistant-initial-state')?.textContent || '{}');
      if (['study','documents'].includes(initial.mode)) mode.value = initial.mode;
      for (const checkbox of form.querySelectorAll('[name=documents]')) checkbox.checked = (initial.documents || []).includes(Number(checkbox.value));
      if (['study','documents'].includes(initial.mode)) {
        for (const item of initial.history || []) if (['user','assistant'].includes(item.role)) append(item.role, item.content);
      }
    } catch (_) { /* Start a fresh display if there is no valid saved conversation. */ }
    panel.querySelector('.chat-documents').hidden = mode.value !== 'documents';
    function changedScope() {
      log.replaceChildren();feedback.textContent = 'Sources changed. Your next question starts a fresh conversation.';
    }
    mode.addEventListener('change', () => { panel.querySelector('.chat-documents').hidden = mode.value !== 'documents'; changedScope(); });
    panel.querySelector('.chat-documents').addEventListener('change', changedScope);
    panel.querySelector('.chat-reset').addEventListener('click', async () => {
      if (busy) return;
      const payload = new FormData();payload.append('reset','true');
      try { await send(payload);log.replaceChildren();feedback.textContent = 'Conversation cleared. Start a new topic.'; } catch(e) { feedback.textContent=e.message; }
    });
    async function submitPrompt() {
      if (busy) return;
      if (!form.reportValidity()) return;
      if (mode.value === 'documents' && !form.querySelector('[name=documents]:checked')) {feedback.textContent='Select at least one ready document.';return;}
      const payload = new FormData(form); const text = form.elements.question.value.trim();if (!text) return;
      busy=true;question.value='';append('user',text);form.querySelector('button[type=submit]').disabled=true;mode.disabled=true;for(const c of form.querySelectorAll('[name=documents]')) c.disabled=true;log.setAttribute('aria-busy','true');
      const pending=append('assistant','Preparing your answer…');
      pending.classList.add('chat-pending');
      feedback.textContent='Keel is working on your question…';
      const slowNotice=setTimeout(() => {
        pending.querySelector('div').textContent='Still waiting for your AI provider…';
      },8000);
      try {
        const data = await send(payload);pending.remove();const bubble=append('assistant',data.answer || 'No explanation returned.');sources(bubble,data);
        feedback.textContent = data.warning || '';
      } catch(error) {
        pending.remove();
        if (!question.value) question.value=text;
        feedback.textContent=error.message;
        if (error.data?.document_sources?.length) { const bubble=append('assistant','Matching passages from your documents:'); sources(bubble,error.data); }
      } finally {clearTimeout(slowNotice);busy=false;form.querySelector('button[type=submit]').disabled=false;mode.disabled=false;for(const c of form.querySelectorAll('[name=documents]')) c.disabled=false;log.removeAttribute('aria-busy');}
    }
    form.addEventListener('submit', event => { event.preventDefault(); submitPrompt(); });
  });
})();
