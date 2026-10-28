(() => {
  'use strict';
  if (!document.body.classList.contains('workspace-page')) return;
  const body = document.body;
  const menu = document.querySelector('.dash-menu-toggle');
  const sidebar = document.querySelector('.dash-sidebar');
  const backdrop = document.querySelector('.dash-sidebar-backdrop');
  const search = document.getElementById('dashboard-search');
  const mobile = window.matchMedia('(max-width: 760px)');
  let previousFocus;
  function closeMenu(restoreFocus = true) {
    body.classList.remove('dash-nav-open');
    menu?.setAttribute('aria-expanded', 'false');
    if (backdrop) backdrop.hidden = true;
    sidebar.inert = mobile.matches;
    sidebar.setAttribute('aria-hidden', String(mobile.matches));
    if (restoreFocus && previousFocus) previousFocus.focus();
  }
  function openMenu() {
    previousFocus = document.activeElement;
    body.classList.add('dash-nav-open');
    sidebar.inert = false;
    sidebar.setAttribute('aria-hidden', 'false');
    menu?.setAttribute('aria-expanded', 'true');
    if (backdrop) backdrop.hidden = false;
    sidebar?.querySelector('a')?.focus();
  }
  menu?.addEventListener('click', () => body.classList.contains('dash-nav-open') ? closeMenu() : openMenu());
  document.querySelectorAll('[data-sidebar-close]').forEach(button => button.addEventListener('click', () => closeMenu()));
  document.querySelectorAll('[data-sidebar-link]').forEach(link => link.addEventListener('click', () => closeMenu(false)));
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      search?.focus();
    }
    if (event.key === 'Escape') {
      if (body.classList.contains('dash-nav-open')) closeMenu();
      document.querySelectorAll('.dash-account-tools details[open]').forEach(detail => { detail.open = false; });
    }
    if (event.key === 'Tab' && body.classList.contains('dash-nav-open')) {
      const focusable = Array.from(sidebar.querySelectorAll('a,button:not([disabled])')).filter(element => element.getClientRects().length);
      const first = focusable[0]; const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  document.querySelector('.dash-search-shortcut')?.addEventListener('click', () => search?.focus());
  mobile.addEventListener('change', () => closeMenu(false));
  closeMenu(false);
  const popovers = document.querySelectorAll('.dash-account-tools details');
  popovers.forEach(detail => {
    detail.addEventListener('toggle', () => {
      if (detail.open) popovers.forEach(other => { if (other !== detail) other.open = false; });
    });
  });
  document.addEventListener('click', event => {
    popovers.forEach(detail => { if (!detail.contains(event.target)) detail.open = false; });
  });
  function askKeel(question = '', submit = false) {
    if (!document.querySelector('.assistant-panel')) return false;
    return window.dispatchEvent(new CustomEvent('keel:ask', {cancelable: true, detail: {question, submit}}));
  }
  document.querySelectorAll('[data-ask-keel]').forEach(link => {
    link.addEventListener('click', event => { event.preventDefault(); askKeel(); });
  });
  document.querySelectorAll('[data-keel-prompt]').forEach(button => {
    button.addEventListener('click', () => askKeel(button.dataset.keelPrompt, true));
  });
  const askForm = document.querySelector('.dash-ask-form');
  const launcher = document.querySelector('.assistant-launcher');
  const askCard = document.querySelector('.dash-ask');
  if (launcher && askCard && 'IntersectionObserver' in window) {
    // The dashboard's own composer is an entry point; avoid covering its Send
    // button with the floating entry point when both would occupy the same area.
    new IntersectionObserver(entries => {
      launcher.hidden = entries[0].isIntersecting;
    }, {threshold: 0.1}).observe(askCard);
  }
  askForm?.addEventListener('submit', event => {
    const input = askForm.elements.question;
    const question = input.value.trim();
    if (!question) { event.preventDefault(); input.focus(); return; }
    event.preventDefault();
    if (askKeel(question, true)) input.value = '';
  });
})();
