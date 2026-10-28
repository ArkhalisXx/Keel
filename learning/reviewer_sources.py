"""Bounded, page-aware source selection for generated reviewers.

Chapter suggestions are extracted from visible headings. They are conveniences,
not an inferred table of contents; the caller can always choose physical pages.
"""
import re

from .assistant import AIError


MAX_EXCERPT = 2400
MAX_SOURCES = 6
_HEADING = re.compile(
    r'^(chapter|unit|part|section|module|lesson)\s+'
    r'([0-9]+(?:\.[0-9]+)*|[ivxlcdm]+|[a-z])'
    r'(?:\s*[:.\-–—]\s*|\s+|$)(.*)$', re.I)
_LEVEL = {'part': 0, 'chapter': 1, 'unit': 1, 'module': 1,
          'section': 2, 'lesson': 2}
_STOP_WORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'chapter', 'chapters',
    'for', 'from', 'in', 'is', 'it', 'lesson', 'module', 'of', 'on', 'or',
    'part', 'questions', 'section', 'the', 'this', 'to', 'topic', 'unit',
    'with', 'about', 'review', 'reviewer',
}


def _pages(document, *, readable=False):
    pages = []
    for page in document.pages if isinstance(document.pages, list) else []:
        if not isinstance(page, dict) or type(page.get('page')) is not int or page['page'] < 1:
            continue
        text = page.get('text', '')
        if not isinstance(text, str):
            text = ''
        if readable and not text.strip():
            continue
        pages.append({**page, 'text': text})
    return sorted(pages, key=lambda page: page['page'])


def available_page_bounds(document):
    pages = _pages(document)
    return (pages[0]['page'], pages[-1]['page']) if pages else (0, 0)


def document_outline(document):
    """Return conservative chapter/section suggestions using physical pages."""
    pages = _pages(document, readable=True)
    if not pages:
        return []
    headings, seen = [], set()
    for page in pages:
        lines = [line.strip() for line in page['text'].splitlines() if line.strip()]
        # A contents page is not the beginning of each chapter it lists.
        if any(re.fullmatch(r'(?:table\s+of\s+)?contents', line, re.I) for line in lines[:8]):
            continue
        matches = [(index, _HEADING.fullmatch(line)) for index, line in enumerate(lines)
                   if len(line) <= 180 and not re.search(r'\.{3,}', line)]
        matches = [(index, match) for index, match in matches if match]
        if len(matches) > 3:
            continue
        for index, match in matches:
            kind, number, title = match.groups()
            key = (kind.casefold(), number.casefold())
            if key in seen:
                continue
            seen.add(key)
            label = lines[index]
            if not title.strip() and index + 1 < len(lines):
                following = lines[index + 1]
                if (len(following) <= 100 and not _HEADING.fullmatch(following)
                        and not following[-1:] in '.?!' and not following.isdigit()):
                    label += ' — ' + following
            headings.append({'label': label[:240], 'start': page['page'],
                             '_level': _LEVEL[kind.casefold()]})
    maximum = available_page_bounds(document)[1]
    result = []
    for index, heading in enumerate(headings):
        end = maximum
        for following in headings[index + 1:]:
            if following['_level'] <= heading['_level']:
                end = max(heading['start'], following['start'] - 1)
                break
        result.append({'label': heading['label'], 'start': heading['start'], 'end': end})
    return result[:100]


def validate_scope(document, *, focus='', page_start=None, page_end=None):
    if not isinstance(focus, str):
        raise AIError('Choose a topic or chapter using text.', 'reviewer_scope')
    focus = ' '.join(focus.split())
    if len(focus) > 240:
        raise AIError('Keep the topic or chapter focus within 240 characters.', 'reviewer_scope')
    minimum, maximum = available_page_bounds(document)
    if not minimum:
        raise AIError('No readable source text is available. Process the document again.', 'no_text')
    start = minimum if page_start is None else page_start
    end = maximum if page_end is None else page_end
    if (type(start) is not int or type(end) is not int or start < minimum
            or end > maximum or end < minimum or start > maximum or start > end):
        raise AIError(f'Choose a valid physical page range between {minimum} and {maximum}.', 'reviewer_scope')
    if not any(start <= page['page'] <= end for page in _pages(document, readable=True)):
        raise AIError('The selected pages have no readable text. Choose another range or process the document again.', 'no_text')
    return focus, start, end


def _normal_text(text):
    return re.sub(r'\W+', ' ', text.casefold()).strip()


def _windows(text):
    """Keep the full document searchable instead of only a page's first lines."""
    text = text.strip()
    # Overlap keeps a term or short passage crossing a window boundary usable.
    for start in range(0, len(text), MAX_EXCERPT - 240):
        yield text[start:start + MAX_EXCERPT]


def select_sources(document, *, focus='', page_start=None, page_end=None, offset=0):
    focus, start, end = validate_scope(document, focus=focus, page_start=page_start, page_end=page_end)
    normalized_focus = _normal_text(focus)
    chapter = None
    if normalized_focus:
        for item in document_outline(document):
            label = _normal_text(item['label'])
            if normalized_focus == label or (label.startswith(normalized_focus + ' ')
                    and _HEADING.match(focus)):
                chapter = item
                start, end = max(start, item['start']), min(end, item['end'])
                break
    tokens = set(re.findall(r'\b[^\W_]{2,}\b', normalized_focus, re.UNICODE)) - _STOP_WORDS
    tokens = {token for token in tokens if not token.isdecimal()}
    candidates = []
    for page in _pages(document, readable=True):
        if not start <= page['page'] <= end:
            continue
        for excerpt in _windows(page['text']):
            if focus and not chapter:
                normalized_excerpt = _normal_text(excerpt)
                words = set(normalized_excerpt.split())
                score = len(tokens & words) if tokens else int(normalized_focus in normalized_excerpt)
                if not score:
                    continue
            else:
                score = 0
            candidates.append((score, {'title': document.title, 'id': document.pk,
                                      'page': page['page'], 'text': excerpt,
                                      'source': page.get('source', 'text')}))
    if not candidates:
        if focus:
            raise AIError('No matching passages were found for that topic or chapter in the selected pages. Try a term used in the document or adjust the page range.', 'reviewer_scope')
        raise AIError('No readable source text is available in the selected pages.', 'no_text')
    # Discard weaker matches when stronger ones are available. Still rotate
    # through matching excerpts between batches to cover more of the source.
    if focus and not chapter:
        strongest = max(score for score, _ in candidates)
        candidates = [(score, item) for score, item in candidates if score == strongest]
    count = min(MAX_SOURCES, len(candidates))
    # offset is the number of cards already generated (normally increments of
    # five), so multiplying it by six would repeat the same sample every batch
    # for common document sizes such as 30 pages.
    shift = max(0, int(offset))
    indices = sorted({(round(i * (len(candidates) - 1) / max(1, count - 1)) + shift)
                      % len(candidates) for i in range(count)})
    return [candidates[index][1] for index in indices]
