/* Typeset math from text nodes only. Document/AI text never becomes raw HTML. */
(() => {
  'use strict';
  if (!window.katex) return;
  const ignored = 'script,style,textarea,input,select,option,pre,code,annotation,math,.katex,[data-keel-math],[data-no-math]';
  const delimiters = [
    {open: '$$', close: '$$', display: true},
    {open: '\\[', close: '\\]', display: true},
    {open: '\\(', close: '\\)', display: false},
    {open: '$', close: '$', display: false, legacy: true},
  ];
  const escaped = (text, index) => {
    let slashes = 0;
    while (index > 0 && text[--index] === '\\') slashes += 1;
    return slashes % 2 === 1;
  };
  function legacyMath(text) {
    // Never mistake ordinary prices such as "$5 and $10" for mathematics.
    // Explicit TeX delimiters remain available for any ambiguous expression.
    return text.trim() && !/^\s|\s$/.test(text) &&
      /[=^_{}\\+*/<>]|[α-ωΑ-Ω∑∫√∆Δ∇]/u.test(text) &&
      !/\b(?:and|or|dollars?|USD|PHP|costs?|price)\b/i.test(text);
  }
  function pieces(text) {
    const matches = [];
    for (let index = 0; index < text.length; index += 1) {
      const delimiter = delimiters.find(item => text.startsWith(item.open, index) && !escaped(text, index));
      if (!delimiter) continue;
      let end = index + delimiter.open.length;
      let depth = 0;
      for (; end < text.length; end += 1) {
        if (text[end] === '{' && !escaped(text, end)) depth += 1;
        if (text[end] === '}' && !escaped(text, end)) depth = Math.max(0, depth - 1);
        if (!depth && text.startsWith(delimiter.close, end) && !escaped(text, end)) break;
      }
      if (end >= text.length) continue;
      const expression = text.slice(index + delimiter.open.length, end);
      const stop = end + delimiter.close.length;
      if (expression.trim() && (!delimiter.legacy || legacyMath(expression))) {
        matches.push({start: index, end: stop, expression, display: delimiter.display});
      }
      index = stop - 1;
    }
    return matches;
  }
  function renderText(node) {
    if (!node.parentElement || node.parentElement.closest(ignored)) return;
    const text = node.nodeValue;
    if (!text || !/[\\$]/.test(text)) return;
    const matches = pieces(text);
    if (!matches.length) return;
    const fragment = document.createDocumentFragment();
    let offset = 0;
    let rendered = false;
    for (const match of matches) {
      fragment.append(document.createTextNode(text.slice(offset, match.start)));
      const span = document.createElement('span');
      span.className = match.display ? 'keel-math keel-math-display' : 'keel-math';
      span.dataset.keelMath = '';
      try {
        if (match.expression.length > 8000) throw new Error('Equation exceeds the display limit');
        window.katex.render(match.expression, span, {
          displayMode: match.display, output: 'htmlAndMathml',
          throwOnError: true, trust: false, strict: 'ignore',
          maxExpand: 300, maxSize: 20, globalGroup: false,
        });
        fragment.append(span);
        rendered = true;
      } catch (_) {
        // Unsupported or incomplete TeX stays exactly as written and readable.
        fragment.append(document.createTextNode(text.slice(match.start, match.end)));
      }
      offset = match.end;
    }
    fragment.append(document.createTextNode(text.slice(offset)));
    if (rendered) node.replaceWith(fragment);
  }
  function render(root) {
    if (!root?.isConnected) return;
    if (root.nodeType === Node.TEXT_NODE) { renderText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE || root.closest(ignored)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement.closest(ignored) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(renderText);
  }
  // Observe only study content, not the navigation, form fields, or the entire UI.
  for (const root of document.querySelectorAll('#workspace-content, .assistant-drawer')) {
    render(root);
    new MutationObserver(records => {
      const changed = new Set();
      for (const record of records) {
        if (record.type === 'characterData') changed.add(record.target);
        else for (const node of record.addedNodes) changed.add(node);
      }
      changed.forEach(render);
    }).observe(root, {subtree: true, childList: true, characterData: true});
  }
  // Useful for a manually refreshed preview; reruns leave existing equations intact.
  window.KeelMath = Object.freeze({render});
})();
