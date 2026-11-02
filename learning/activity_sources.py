"""Conservative extraction of existing textbook activities, never invented questions.

Only explicit assessment headings activate extraction. Question identities are
stable across retries and preserve original page/wording/choices. Ordinary
chapter prose and worked examples are deliberately not fallback sources.
"""
import hashlib
import re

from .assistant import AIError
from .reviewer_sources import _HEADING, _STOP_WORDS, _normal_text, _pages, document_outline, validate_scope

_NAMES = (r'(?:review\s+(?:questions(?:\s+and\s+problems)?|exercises|problems)|'
          r'(?:practice|discussion|study|revision)\s+(?:questions|problems|exercises)|'
          r'(?:learning\s+)?activit(?:y|ies)|(?:quick\s+)?quiz(?:zes)?|'
          r'exercises?(?:\s+and\s+problems)?|problems(?:\s+and\s+exercises)?|questions|'
          r'self[ -](?:test|assessment)|(?:chapter\s+)?test|'
          r'(?:check|test)\s+(?:your\s+)?understanding|knowledge\s+check|chapter\s+review)')
_ACTIVITY = re.compile(r'^(?:(?:end[ -]of[ -](?:chapter|unit))\s+)?' + _NAMES +
                       r'(?:\s+(?:[A-Z]|\d+(?:\.\d+)*))?'
                       r'(?:\s+for\s+(?:chapter|unit)\s+\d+)?'
                       r'(?:\s*[:\-–—]\s*[^.!?]{1,80})?'
                       r'(?:\s*\(continued\))?\s*:?$', re.I)
_STOP = re.compile(r'^(?:(?:worked\s+)?examples?|worked\s+solutions?|summary|chapter\s+summary|'
                   r'references|bibliography|further\s+reading|learning\s+(?:outcomes|objectives)|'
                   r'key\s+(?:terms|concepts))\b', re.I)
_NUMBERED_SECTION = re.compile(r'^\d+(?:\.\d+)+\s+\S')
_QUESTION = re.compile(r'^(?:(?:q(?:uestion)?\.?\s*)?(\d{1,4})\s*[.)\-:]\s*|'
                       r'q(?:uestion)?\s+(\d{1,4})\s+)(.*)$', re.I)
_BARE_NUMBER = re.compile(r'^(\d{1,4})[.)]?$')
_ASK = re.compile(r'^(?:what|why|how|which|when|where|calculate|determine|find|explain|'
                  r'describe|define|compare|state|list|show|derive|identify|name)\b', re.I)
_OPTION = re.compile(r'(?:^|\s+)([A-Ea-e])[.)]\s+')
_ANSWER_HEADING = re.compile(r'^(?:answer\s*key|answers(?:\s+to\s+(?:review\s+)?(?:questions|exercises|activities))?|'
                            r'correct\s+answers)\s*:?$', re.I)
_LOCAL_ANSWER = re.compile(r'^(?:correct\s+)?(?:answer|ans\.?)\s*[:\-]\s*(.+)$', re.I)
_KEY = re.compile(r'(?<!\d)(\d{1,4})\s*[.)\-:]?\s+([A-D])\b', re.I)
_CONTENTS = re.compile(r'^(?:table\s+of\s+)?contents$', re.I)
MAX_ACTIVITY_TEXT = 10000


def _body(lines):
    """Separate original prompt/options and a local printed answer."""
    prompt, options = [], {}
    choice, answer, explanation = None, '', []
    for line in lines:
        local = _LOCAL_ANSWER.match(line)
        if local:
            answer = local.group(1).strip()
            choice = None
            continue
        explained = re.match(r'^explanation\s*[:\-]\s*(.*)$', line, re.I)
        if explained:
            explanation.append(explained.group(1))
            choice = None
            continue
        if explanation:
            explanation.append(line)
            continue
        labels = list(_OPTION.finditer(line))
        if (labels and labels[0].start() > 0
                and [label.group(1).upper() for label in labels[:2]] == list('AB')):
            prompt.append(line[:labels[0].start()].strip())
            line = line[labels[0].start():].lstrip()
            labels = list(_OPTION.finditer(line))
        if labels and labels[0].start() == 0:
            for index, label in enumerate(labels):
                value = line[label.end():labels[index + 1].start() if index + 1 < len(labels) else len(line)].strip()
                letter = label.group(1).upper()
                if letter in options:
                    options['ambiguous'] = ''
                options[letter] = value
                choice = letter
        elif choice:
            options[choice] += ' ' + line
        else:
            prompt.append(line)
    choices = [options[letter].strip() for letter in 'ABCD'] if set(options) == set('ABCD') else []
    return ' '.join(prompt).strip(), choices, answer, ' '.join(explanation).strip(), bool(options and not choices)


def activity_questions(document, *, focus='', page_start=None, page_end=None):
    """Return unique, bounded existing questions inside the selected scope.

    Records contain source_id, section, page/end_page, number, prompt, choices,
    printed_answer, correct_index, explanation, and source text. No match raises
    AIError so callers never silently generate unrelated notes-based questions.
    """
    focus, start, end = validate_scope(document, focus=focus, page_start=page_start, page_end=page_end)
    chapter = None
    normalized = _normal_text(focus)
    if normalized:
        titled = []
        for item in document_outline(document):
            label = _normal_text(item['label'])
            if normalized == label or (label.startswith(normalized + ' ') and _HEADING.match(focus)):
                chapter = item
                break
            title = re.sub(r'^(?:chapter|unit|part|section|module|lesson)\s+\S+\s*', '', item['label'], flags=re.I)
            title = _normal_text(title)
            if normalized == title:
                titled.append(item)
        if chapter is None and len(titled) == 1:
            chapter = titled[0]
        if chapter:
            start, end = max(start, chapter['start']), min(end, chapter['end'])
    records, sections, current = [], [], None
    active = None
    latest = None
    key_mode = False
    chapter_key = None
    unnamed = 0

    def finish():
        nonlocal current
        if current:
            prompt, choices, answer, explanation, incomplete_options = _body(current.pop('lines'))
            if (prompt and not incomplete_options and len(prompt) <= 5000
                    and all(len(value) <= 2000 for value in choices)):
                current.update(prompt=prompt, choices=choices, printed_answer=answer,
                               correct_index=None, explanation=explanation)
                records.append(current)
        current = None

    for page in _pages(document, readable=True):
        lines = [line.strip() for line in page['text'].splitlines()]
        meaningful = [line for line in lines if line]
        if any(_CONTENTS.fullmatch(line) for line in meaningful[:8]):
            finish(); active = None; key_mode = False
            continue
        blank = True
        for line in lines:
            if not line:
                blank = True
                continue
            if line.isdecimal() or re.fullmatch(r'(?:page\s+)?\d+\s*(?:of\s+\d+)?', line, re.I):
                continue
            heading = _HEADING.fullmatch(line)
            if heading:
                new_key = (heading.group(1).casefold(), heading.group(2).casefold())
                # Repeated running chapter headers do not discard continuations.
                if new_key != chapter_key:
                    finish(); active = None; key_mode = False; latest = None
                    chapter_key = new_key
                blank = True
                continue
            if _NUMBERED_SECTION.match(line):
                finish(); active = None; key_mode = False; latest = None
                blank = True
                continue
            if len(line) <= 140 and not re.search(r'\.{3,}', line) and _ACTIVITY.fullmatch(line):
                finish()
                # A repeated assessment header on a continuation page is one section.
                if latest is None or _normal_text(latest['label']) != _normal_text(line):
                    latest = {'label': line.rstrip(':'), 'index': len(sections), 'answers': {}, 'chapter': chapter_key}
                    sections.append(latest)
                active = latest; key_mode = False; blank = True
                continue
            if _ANSWER_HEADING.fullmatch(line):
                finish(); active = None; key_mode = latest is not None
                blank = True
                continue
            if _STOP.match(line):
                finish(); active = None; key_mode = False; latest = None
                blank = True
                continue
            if key_mode:
                keys = list(_KEY.finditer(line))
                if keys and latest:
                    for key in keys:
                        number, answer = str(int(key.group(1))), key.group(2).upper()
                        previous = latest['answers'].get(number)
                        latest['answers'][number] = answer if previous in (None, answer) else ''
                else:
                    key = _QUESTION.match(line)
                    if key and latest:
                        latest['answers'][str(int(key.group(1) or key.group(2)))] = key.group(3).strip()
                continue
            if active is None:
                continue
            question = _QUESTION.match(line)
            bare = _BARE_NUMBER.fullmatch(line)
            if question or bare:
                finish()
                number = str(int((question.group(1) or question.group(2)) if question else bare.group(1)))
                text = question.group(3).strip() if question else ''
                current = {'section': active['label'], '_section': active, 'number': number,
                           'page': page['page'], 'end_page': page['page'], 'lines': [text] if text else []}
            elif (current is None or blank) and _ASK.match(line) and (line.endswith('?') or current is None):
                finish(); unnamed += 1
                current = {'section': active['label'], '_section': active, 'number': f'u{unnamed}',
                           'page': page['page'], 'end_page': page['page'], 'lines': [line]}
            elif current:
                current['lines'].append(line)
                current['end_page'] = page['page']
                if sum(len(value) for value in current['lines']) > MAX_ACTIVITY_TEXT:
                    # Oversized blocks must not become truncated or fabricated questions.
                    current = None
            blank = False
    finish()

    selected, seen = [], set()
    tokens = set(re.findall(r'\b[^\W_]{2,}\b', normalized, re.UNICODE)) - _STOP_WORDS
    tokens = {token for token in tokens if not token.isdecimal()}
    for record in records:
        section = record.pop('_section')
        if not start <= record['page'] <= record['end_page'] <= end:
            continue
        text = record['prompt'] + ' ' + ' '.join(record['choices'])
        if focus and not chapter:
            words = set(_normal_text(record['section'] + ' ' + text).split())
            if not (tokens & words if tokens else normalized in _normal_text(text)):
                continue
        identity = _normal_text(text)
        if identity in seen:
            continue
        seen.add(identity)
        printed = record['printed_answer'] or section['answers'].get(record['number'], '')
        record['printed_answer'] = printed
        letter = re.fullmatch(r'([A-D])(?:[.)]\s*.*|\s*[:\-]\s*.*|\s+.+)?', printed, re.I) if printed else None
        if record['choices'] and letter:
            record['correct_index'] = ord(letter.group(1).upper()) - ord('A')
        elif record['choices'] and printed:
            exact = [index for index, choice in enumerate(record['choices'])
                     if _normal_text(choice) == _normal_text(printed)]
            if len(exact) == 1:
                record['correct_index'] = exact[0]
        record['source_id'] = 'act-' + hashlib.sha256(
            f'{record["page"]}:{record["section"]}:{record["number"]}:{identity}'.encode()).hexdigest()[:16]
        record['text'] = (f'EXISTING TEXTBOOK QUESTION ID: {record["source_id"]}\n'
                          f'Section: {record["section"]}; original question {record["number"]}\n'
                          f'{record["prompt"]}' +
                          ''.join(f'\n{letter}. {value}' for letter, value in zip('ABCD', record['choices'])) +
                          (f'\nPrinted answer: {printed}' if printed else '') +
                          (f'\nPrinted explanation: {record["explanation"]}' if record['explanation'] else ''))
        selected.append(record)
    if not selected:
        raise AIError('No complete activity, exercise, or quiz questions were found in the selected topic/pages. Try another chapter or page range, or choose questions generated from notes.', 'no_activities')
    return selected
