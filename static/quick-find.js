/* Private quick find. Ordinary search remains a normal form without JavaScript. */
(() => {
  'use strict';
  const dialog = document.getElementById('quick-find');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const input = dialog.querySelector('#quick-find-query');
  const list = dialog.querySelector('[data-quick-find-results]');
  const status = dialog.querySelector('.quick-find-status');
  const empty = dialog.querySelector('[data-quick-find-empty]');
  const fallbackForm = document.querySelector('.dash-search');
  let controller;
  let timer;
  let version = 0;
  let previousFocus;
  const normalize = value => value.trim().toLocaleLowerCase();
  function safeURL(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value, location.origin);
      return ['http:', 'https:'].includes(url.protocol) && url.origin === location.origin && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  const destinations = [...document.querySelectorAll('.dash-navigation a')].map(link => ({
    title: link.textContent.trim(), detail: 'Jump to this page', kind: 'page', url: link.href,
  }));
  const filteredDestinations = query => destinations.filter(item => !query || normalize(item.title).includes(query));
  function row(item) {
    const url = typeof item.url === 'string' && safeURL(item.url);
    if (!url || typeof item.title !== 'string') return null;
    const li = document.createElement('li');
    const a = document.createElement('a'); a.href = url; a.className = 'quick-find-result';
    const icon = document.createElement('span'); icon.className = 'quick-find-result-icon'; icon.setAttribute('aria-hidden', 'true');
    const types = {document: ['▤', 'Document'], paper: ['▥', 'Paper'], question: ['?', 'Question'], page: ['↗', 'Page'], search: ['⌕', 'Search']};
    const [symbol, label] = types[item.kind] || ['↗', 'Result']; icon.textContent = symbol;
    const copy = document.createElement('span'); copy.className = 'quick-find-result-copy';
    const title = document.createElement('strong'); title.textContent = item.title.slice(0, 200);
    const detail = document.createElement('small'); detail.textContent = typeof item.detail === 'string' ? item.detail.slice(0, 240) : '';
    copy.append(title, detail);
    const type = document.createElement('span'); type.className = 'quick-find-result-type'; type.textContent = label;
    const arrow = document.createElement('span'); arrow.className = 'quick-find-result-arrow'; arrow.textContent = '→'; arrow.setAttribute('aria-hidden', 'true');
    a.append(icon, copy, type, arrow); li.append(a); return li;
  }
  function show(items, message) {
    list.replaceChildren();
    const used = new Set();
    for (const item of items) {
      if (!item || used.has(item.url)) continue;
      const element = row(item);
      if (element) { used.add(item.url); list.append(element); }
    }
    window.KeelMath?.render(list);
    status.textContent = message;
    empty.hidden = list.childElementCount !== 0;
    list.removeAttribute('aria-busy');
  }
  function fallback(query) {
    const target = safeURL(fallbackForm?.action);
    if (!target) return [];
    const url = new URL(target); url.searchParams.set('q', query);
    return [{title: 'Search in Practice', detail: `Look for “${query}” in your question bank`, kind: 'search', url: url.href}];
  }
  async function load(query, currentVersion) {
    const abort = new AbortController(); controller = abort;
    try {
      const target = safeURL(dialog.dataset.searchUrl);
      if (!target) throw new Error('Search unavailable');
      const url = new URL(target); url.searchParams.set('q', query);
      const response = await fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}, signal: abort.signal});
      if (currentVersion !== version || !dialog.open) return;
      if (response.redirected || response.status === 401 || response.status === 403) {
        show(filteredDestinations(normalize(query)), 'Your session expired. Close search and sign in again.'); return;
      }
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Search unavailable');
      const data = await response.json();
      if (currentVersion !== version || !dialog.open) return;
      const matches = Array.isArray(data.results) ? data.results.slice(0, 12) : [];
      const pages = filteredDestinations(normalize(query));
      const count = matches.length + pages.length;
      show([...pages, ...matches], count ? `${count} match${count === 1 ? '' : 'es'} · choose a result to open it.` : 'No matches in your workspace. Try a different word.');
    } catch (error) {
      if (error.name === 'AbortError' || currentVersion !== version || !dialog.open) return;
      show([...filteredDestinations(normalize(query)), ...fallback(query)], 'Live search is unavailable. You can still open a page or search in Practice.');
    }
  }
  function search() {
    clearTimeout(timer); controller?.abort(); version += 1;
    const query = input.value.trim().slice(0, 200);
    const pages = filteredDestinations(normalize(query));
    if (query.length < 2) {
      show(pages, query ? 'Type one more character to search your study material.' : 'Jump to a page, or search your own study material.'); return;
    }
    show(pages, 'Searching your documents, papers, and questions…');
    list.setAttribute('aria-busy', 'true');
    empty.hidden = true;
    const current = version;
    timer = setTimeout(() => load(query, current), 180);
  }
  function open(query = '') {
    if (dialog.open) { input.focus(); return; }
    previousFocus = document.activeElement;
    dialog.showModal(); document.body.classList.add('quick-find-open');
    input.value = typeof query === 'string' ? query.slice(0, 200) : '';
    search(); input.focus(); input.select();
  }
  window.addEventListener('keel:quick-find', event => {
    event.preventDefault(); open(event.detail?.query || '');
  });
  input.addEventListener('input', search);
  input.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      const links = list.querySelectorAll('a');
      if (links.length) { event.preventDefault(); links[event.key === 'ArrowDown' ? 0 : links.length - 1].focus(); }
    } else if (event.key === 'Enter') {
      const first = list.querySelector('a');
      if (first) { event.preventDefault(); first.click(); }
    }
  });
  list.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    const links = [...list.querySelectorAll('a')]; const index = links.indexOf(document.activeElement);
    if (index === -1) return;
    event.preventDefault();
    const next = index + (event.key === 'ArrowDown' ? 1 : -1);
    if (next < 0 || next >= links.length) input.focus(); else links[next].focus();
  });
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    clearTimeout(timer); controller?.abort(); version += 1;
    document.body.classList.remove('quick-find-open');
    if (previousFocus?.isConnected) previousFocus.focus({preventScroll: true});
  });
})();
