"""Conservative extraction of numbered A–D questions, without guessing answer keys."""
import re
from collections import Counter
from pathlib import Path
from django.db import transaction
from django.db.models import Q

from .models import Document, ExamPaper, Question

MAX_QUESTIONS = 1000
MAX_IMPORT_CHARACTERS = 2_000_000
QUESTION = re.compile(r'^\s*(?:Q(?:uestion)?\s*)?(\d{1,4})(?:\s*[.)]\s+|\s+)(\S.*)$', re.I)
OPTION = re.compile(r'^\s*\(?([A-D])\s*[.)]\s*(.*)$', re.I)
INLINE_OPTION = re.compile(r'(?<!\w)\(?([A-D])\s*[.)](?:\s+|$)', re.I)


def inline_options(line):
    # Match the first label's punctuation consistently: a formula '(w x d)'
    # cannot introduce another D choice in a row using a./b./c./d. labels.
    first = OPTION.match(line)
    if first:
        label = re.match(r'^\s*\(?[A-D]\s*([.)])', line, re.I)
        punctuation = re.escape(label.group(1))
        return list(re.finditer(r'(?<!\w)\(?([A-D])\s*' + punctuation + r'(?:\s+|$)', line, re.I))
    return list(INLINE_OPTION.finditer(line))

KEY_HEADING = re.compile(r'^\s*(?:answer\s*key|answers|correct\s*answers)\s*[:\-]?\s*$', re.I)
KEY_ENTRY = re.compile(r'(?<!\d)(\d{1,4})\s*(?:[.)\-:=]\s*|\s+)\(?([A-D])\)?\b', re.I)
LOCAL_ANSWER = re.compile(r'^\s*(?:correct\s+)?answer\s*[:=\-]\s*\(?([A-D])\)?\s*[.]?\s*$', re.I)


class PaperImportError(ValueError):
    pass


def visible_questions(user):
    return Question.objects.filter(reviewed=True, correct_index__isnull=False).filter(Q(owner__isnull=True) | Q(owner=user))


def exam_pdf_pages(document):
    """Preserve fixed-width choice cells and read detected question columns in order.

    Re-extract only text-based PDF pages; OCR pages retain their existing text.
    Documents are never rewritten, and physical page numbers stay unchanged.
    """
    if Path(document.file.name).suffix.lower() != '.pdf':
        return document.pages
    from pypdf import PdfReader
    try:
        file = document.file.open('rb')
    except OSError:
        return document.pages
    pages = []
    total = 0
    with file:
        reader = PdfReader(file)
        stored = {page['page']: page for page in document.pages}
        for index, pdf_page in enumerate(reader.pages, 1):
            original = stored.get(index, {'page': index, 'text': ''})
            if original.get('source') == 'ocr':
                text = original['text']
            else:
                text = pdf_page.extract_text(extraction_mode='layout') or original['text']
            total += len(text)
            if total > MAX_IMPORT_CHARACTERS:
                raise PaperImportError('This paper is too long to import at once. Split it into smaller papers.')
            lines = text.splitlines()
            # A recurring second question-number position signals a second column.
            starts = Counter(m.start() for line in lines for m in re.finditer(r'(?<!\S)\d{1,4}\s{2,}(?=[A-Za-z])', line) if m.start() > 40)
            if not starts:
                starts = Counter(m.start() for line in lines for m in re.finditer(r'(?<!\S)\d{1,4}\s+(?=[A-Z][a-zA-Z]+\b)', line) if m.start() > 40 and re.match(r'^\s{0,2}\d+\s+', line))
            split = starts.most_common(1)[0][0] if starts and starts.most_common(1)[0][1] >= 3 else None
            key_headings = [list(re.finditer(r'Answer\s*Key\s+SET\s+[A-Z0-9]+', line, re.I)) for line in lines]
            if any(key_headings):
                pairs = [headings for headings in key_headings if len(headings) > 1]
                split = pairs[0][1].start() if pairs else None
            # Remove repeated running furniture, never question/answer text.
            clean = []
            skip_header = False
            for line in lines:
                if re.match(r'^\s*Property of Engr\.', line, re.I):
                    skip_header = True
                    continue
                if skip_header:
                    if not line.strip():
                        skip_header = False
                    continue
                if re.match(r'^\s*\d+\s*\|\s*P\s*a\s*g\s*e\s*$', line, re.I):
                    continue
                clean.append(line)
            if split:
                ordered = [line[:split].rstrip() for line in clean] + [line[split:].rstrip() for line in clean]
            else:
                ordered = clean
            pages.append({'page': index, 'text': '\n'.join(ordered)})
    return pages


def parse_paper(pages):
    """Return (questions, warnings). Preserve page provenance and report every skipped block."""
    if sum(len(page.get('text', '')) for page in pages) > MAX_IMPORT_CHARACTERS:
        raise PaperImportError('This paper is too long to import at once. Split it into files with up to 2 million extracted characters.')
    warnings = []
    results = []
    answer_key = {}
    seen = set()
    current = None
    key_section = False
    choice = None
    option_columns = []
    section = ''
    explanation_started = False

    def finish():
        nonlocal current, choice, explanation_started, option_columns
        if current is None:
            return
        number = current['source_number']
        if number in seen:
            warnings.append(f'Question {number}: repeated numbering; this repeated block was skipped.')
        elif set(current['options']) != set('ABCD') or not all(current['options'][x].strip() for x in current['options']):
            warnings.append(f'Question {number}: could not identify four complete A–D choices; skipped. Check the original layout or OCR.')
        else:
            seen.add(number)
            current['choices'] = [current['options'][letter].strip() for letter in 'ABCD']
            current.pop('options')
            current['prompt'] = current['prompt'].strip()
            current['position'] = len(results) + 1
            results.append(current)
            if len(results) > MAX_QUESTIONS:
                raise PaperImportError(f'Import supports up to {MAX_QUESTIONS} questions per paper. Split this paper into smaller files.')
        current, choice, explanation_started, option_columns = None, None, False, []

    def append_option_line(line):
        nonlocal choice
        match = OPTION.match(line)
        if match:
            choice = match.group(1).upper()
            if choice in current['options']:
                # Duplicate option labels are ambiguous; make the block invalid.
                current['options']['duplicate'] = 'duplicate'
            current['options'][choice] = match.group(2).strip()
            return True
        return False

    for page in pages:
        page_number = page.get('page', 1)
        for raw_line in page.get('text', '').splitlines():
            line = raw_line.strip()
            heading = re.match(r'^(Answer\s*Key\s*)?SET\s+([A-Z0-9]+)\s*$', line, re.I)
            if heading:
                finish()
                section = heading.group(2).upper()
                key_section = bool(heading.group(1))
                continue
            if not line:
                continue
            inline_key = re.match(r'^\s*(?:answer\s*key|answers|correct\s*answers)\s*[:\-]\s*(.+)$', line, re.I)
            if KEY_HEADING.match(line) or inline_key:
                finish()
                key_section = True
                if not inline_key:
                    continue
                line = inline_key.group(1)
            if key_section:
                for number, letter in KEY_ENTRY.findall(line):
                    normalized = (section + '-' if section else '') + str(int(number))
                    answer = ord(letter.upper()) - ord('A')
                    if normalized in answer_key and answer_key[normalized] != answer:
                        answer_key[normalized] = None
                        warnings.append(f'Question {normalized}: conflicting answer key entries; select the answer manually.')
                    else:
                        answer_key[normalized] = answer
                continue
            local_key = LOCAL_ANSWER.match(line)
            if local_key and current:
                current['correct_index'] = ord(local_key.group(1).upper()) - ord('A')
                continue
            match = QUESTION.match(line)
            # Bare numbers are question labels only at the column margin.
            # An indented continuation such as '2 lower hold' belongs to the stem.
            if match and re.match(r'^\d+\s+', line) and len(raw_line) - len(raw_line.lstrip()) > 2:
                previous = int(current['source_number'].split('-')[-1]) if current else None
                if previous is None or int(match.group(1)) != previous + 1:
                    match = None
            if match:
                finish()
                current = {'source_number': (section + '-' if section else '') + str(int(match.group(1))), 'source_page': page_number,
                           'prompt': match.group(2), 'options': {}, 'correct_index': None,
                           'explanation': ''}
                choice = None
                # Support questions and options on the same line when labels are unambiguous.
                inline = list(INLINE_OPTION.finditer(current['prompt']))
                if inline and [m.group(1).upper() for m in inline] == list('ABCD'):
                    prompt = current['prompt']
                    current['prompt'] = prompt[:inline[0].start()].strip()
                    for i, option in enumerate(inline):
                        end = inline[i+1].start() if i+1 < len(inline) else len(prompt)
                        current['options'][option.group(1).upper()] = prompt[option.end():end].strip()
                    choice = 'D'
                continue
            if current:
                explanation = re.match(r'^(?:explanation|solution|rationale)\s*:\s*(.*)$', line, re.I)
                if explanation:
                    current['explanation'] = explanation.group(1).strip()
                    explanation_started = True
                    continue
                if explanation_started:
                    current['explanation'] += ' ' + line
                    continue
                # Separate A/B/C/D labels appearing together on one extracted line.
                inline = inline_options(line)
                if len(inline) > 1 and inline[0].start() == 0:
                    if [m.group(1).upper() for m in inline] == list('ABCD') and current['options']:
                        # Lettered subparts of a stem precede the actual full answer row.
                        current['prompt'] += ' ' + ' '.join(f'({k.lower()}) {v}' for k, v in current['options'].items() if k in 'ABCD')
                        current['options'] = {}
                    # Keep fixed-width positions for continuation cells on the next row.
                    raw_options = inline_options(raw_line)
                    option_columns = [(m.start(), m.group(1).upper()) for m in raw_options]
                    for i, option in enumerate(inline):
                        end = inline[i+1].start() if i+1 < len(inline) else len(line)
                        append_option_line(line[option.start():end])
                elif append_option_line(line):
                    option_columns = []
                elif option_columns and len(option_columns) == 4:
                    # Each wrapped cell stays with its option, rather than all going to D.
                    for index, (start, letter) in enumerate(option_columns):
                        end = option_columns[index+1][0] if index+1 < len(option_columns) else len(raw_line)
                        continuation = raw_line[start:end].strip()
                        if continuation:
                            current['options'][letter] += ' ' + continuation
                elif choice:
                    current['options'][choice] += ' ' + line
                else:
                    current['prompt'] += ' ' + line
    finish()
    for question in results:
        number = question['source_number']
        if number in answer_key:
            local = question['correct_index']
            keyed = answer_key[number]
            if local is not None and keyed != local:
                question['correct_index'] = None
                warnings.append(f'Question {number}: printed answer and answer key disagree; select the answer manually.')
            else:
                question['correct_index'] = keyed
    if not results:
        raise PaperImportError('No complete numbered A–D questions were found. Use a text-based or OCR-ready paper with one question block at a time. Two-column layouts may need reformatting.')
    missing = sum(q['correct_index'] is None for q in results)
    if missing:
        warnings.append(f'{missing} question(s) have no unambiguous answer key. Add their correct answers during review.')
    warnings.append('All imported questions are drafts. Verify numbering, choices, answers and formulas against the original before publishing.')
    return results, warnings


def import_document(owner, document, title, topic):
    """Import one private ready document once. Repeated submission returns its existing paper."""
    if document.owner_id != owner.pk or document.status != Document.Status.READY:
        raise PaperImportError('Choose a ready document from your own library.')
    # Lock the document to prevent two concurrent imports from producing duplicate questions.
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk, owner=owner)
        if document.status != Document.Status.READY:
            raise PaperImportError('Wait for document processing to finish before importing.')
        existing = ExamPaper.objects.filter(owner=owner, document=document, kind='exam').first()
        if existing:
            return existing, False
        questions, warnings = parse_paper(exam_pdf_pages(document))
        paper = ExamPaper.objects.create(owner=owner, document=document, title=title, topic=topic, warnings=warnings)
        Question.objects.bulk_create([
            Question(owner=owner, paper=paper, source_document=document, topic=topic,
                     reference=f'{document.title}, page/section {question["source_page"]}, question {question["source_number"]}'[:300],
                     reviewed=False, **question)
            for question in questions
        ])
        return paper, True
