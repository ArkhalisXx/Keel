(() => {
  'use strict';
  const builder = document.querySelector('[data-paper-builder]');
  if (builder) {
    const field = name => builder.elements.namedItem(name);
    const source = field('document');
    const mode = field('kind');
    const count = field('question_count');
    const focus = field('focus');
    const from = field('page_start');
    const to = field('page_end');
    const chapter = builder.querySelector('[data-chapter-select]');
    const chapterField = builder.querySelector('[data-chapter-field]');
    const sourceStatus = builder.querySelector('[data-source-status]');
    const summary = builder.querySelector('[data-build-summary]');
    const modeDescription = builder.querySelector('[data-mode-description]');
    const create = builder.querySelector('[data-create-button]');
    const controls = builder.querySelectorAll('[data-reviewer-controls]');
    const presets = builder.querySelectorAll('[data-question-count]');
    let currentRequest = null;
    let submitPending = false;

    function update() {
      const isExam = mode.value === 'exam';
      controls.forEach(control => { control.hidden = isExam; });
      // Hidden AI fields must not block a printed-paper import's browser validation.
      [count, focus, from, to].forEach(input => { input.disabled = isExam; });
      if (chapter) chapter.disabled = isExam;
      presets.forEach(button => {
        button.setAttribute('aria-pressed', String(Number(button.dataset.questionCount) === Number(count.value)));
      });
      const chosen = source.selectedOptions[0];
      const sourceName = source.value && chosen ? chosen.textContent.trim() : 'your document';
      const hasScope = Boolean(focus.value.trim() || from.value || to.value);
      if (isExam) {
        modeDescription.textContent = 'Extract printed A–D questions and use the paper’s answer key. Questions without keys remain drafts.';
        summary.textContent = `Import printed questions and answers from ${sourceName}.`;
      } else {
        modeDescription.textContent = mode.value === 'reviewer'
          ? 'Create new questions and source-grounded explanations with your configured AI provider.'
          : 'Automatic imports printed questions when available. A topic or page selection creates a focused AI reviewer.';
        const scope = from.value || to.value ? `pages ${from.value || 'start'}–${to.value || 'end'}` : 'all readable pages';
        const topic = focus.value.trim() ? ` · focused on “${focus.value.trim()}”` : '';
        const requestCount = Number(count.value) || 20;
        summary.textContent = mode.value === 'auto' && !hasScope
          ? `Import ${sourceName}, or generate ${requestCount} reviewer questions if no exam questions are found.`
          : `${requestCount} reviewer questions from ${sourceName} · ${scope}${topic}.`;
      }
    }

    function resetChapters() {
      chapter.replaceChildren(new Option('All readable pages', ''));
      chapterField.hidden = true;
    }

    async function loadSource() {
      if (currentRequest) currentRequest.abort();
      resetChapters();
      [from, to].forEach(input => { input.removeAttribute('max'); input.min = '1'; });
      if (!source.value) {
        sourceStatus.textContent = 'Select a document to see its available chapters and pages.';
        update();
        return;
      }
      const selectedSource = source.value;
      currentRequest = new AbortController();
      sourceStatus.textContent = 'Reading the available chapters and page range…';
      try {
        const url = builder.dataset.sourceUrlTemplate.replace('/0/', `/${encodeURIComponent(selectedSource)}/`);
        const response = await fetch(url, {headers: {Accept: 'application/json'}, signal: currentRequest.signal});
        if (!response.ok || response.redirected) throw new Error('source_unavailable');
        const data = await response.json();
        if (source.value !== selectedSource) return;
        if (Number.isInteger(data.page_min) && Number.isInteger(data.page_max)) {
          from.min = to.min = String(data.page_min);
          from.max = to.max = String(data.page_max);
          from.placeholder = String(data.page_min);
          to.placeholder = String(data.page_max);
          sourceStatus.textContent = `Ready to use · physical pages ${data.page_min}–${data.page_max}.`;
        } else {
          sourceStatus.textContent = 'Ready to use. Enter a topic or page range to narrow your reviewer.';
        }
        const chapters = Array.isArray(data.chapters) ? data.chapters : [];
        for (const item of chapters) {
          if (typeof item.label !== 'string' || !Number.isInteger(item.start) || !Number.isInteger(item.end)) continue;
          const option = new Option(`${item.label} · pp. ${item.start}–${item.end}`, String(chapter.options.length));
          option.dataset.start = String(item.start);
          option.dataset.end = String(item.end);
          chapter.append(option);
        }
        chapterField.hidden = chapter.options.length <= 1;
        if (chapter.options.length <= 1) sourceStatus.textContent += ' No clear chapter headings were detected; use keywords or enter pages below.';
      } catch (error) {
        if (error.name === 'AbortError') return;
        if (source.value === selectedSource) sourceStatus.textContent = 'Chapter suggestions are unavailable. You can still enter a topic or page range manually.';
      }
      update();
    }

    presets.forEach(button => {
      button.addEventListener('click', () => {
        count.value = button.dataset.questionCount;
        update();
      });
    });
    chapter.addEventListener('change', () => {
      const option = chapter.selectedOptions[0];
      from.value = option.dataset.start || '';
      to.value = option.dataset.end || '';
      update();
    });
    [from, to].forEach(input => {
      input.addEventListener('input', () => { chapter.value = ''; });
    });
    source.addEventListener('change', () => {
      from.value = '';
      to.value = '';
      loadSource();
    });
    builder.addEventListener('input', update);
    builder.addEventListener('change', update);
    builder.addEventListener('submit', event => {
      if (submitPending) { event.preventDefault(); return; }
      submitPending = true;
      create.disabled = true;
      create.textContent = 'Creating your question set…';
    });
    window.addEventListener('pageshow', () => {
      submitPending = false;
      create.disabled = false;
      create.textContent = 'Create question set →';
      update();
    });
    update();
    if (source.value) loadSource();
  }

  const answerToggle = document.querySelector('[data-toggle-answers]');
  const answers = document.querySelectorAll('.paper-question-list .reviewer-answer');
  if (answerToggle && answers.length) {
    answerToggle.hidden = false;
    function syncToggle() {
      answerToggle.textContent = Array.from(answers).some(answer => answer.open) ? 'Hide all answers' : 'Show all answers';
    }
    answerToggle.addEventListener('click', () => {
      const shouldOpen = !Array.from(answers).some(answer => answer.open);
      answers.forEach(answer => { answer.open = shouldOpen; });
      syncToggle();
    });
    answers.forEach(answer => { answer.addEventListener('toggle', syncToggle); });
    syncToggle();
  }
})();
