"""Conservative extraction of numbered A–D questions, without guessing answer keys."""
import re
from django.db import transaction
from django.db.models import Q

from .models import Document, ExamPaper, Question

MAX_QUESTIONS = 1000
MAX_IMPORT_CHARACTERS = 2_000_000
QUESTION = re.compile(r'^\s*(?:Q(?:uestion)?\s*)?(\d{1,4})\s*[.)]\s+(.+)$', re.I)
OPTION = re.compile(r'^\s*\(?([A-D])\s*[.)]\s*(.*)$', re.I)
INLINE_OPTION = re.compile(r'(?<!\w)\(?([A-D])\s*[.)]\s+', re.I)
KEY_HEADING = re.compile(r'^\s*(?:answer\s*key|answers|correct\s*answers)\s*[:\-]?\s*$', re.I)
KEY_ENTRY = re.compile(r'(?<!\d)(\d{1,4})\s*(?:[.)\-:=]\s*|\s+)\(?([A-D])\)?\b', re.I)
LOCAL_ANSWER = re.compile(r'^\s*(?:correct\s+)?answer\s*[:=\-]\s*\(?([A-D])\)?\s*[.]?\s*$', re.I)


class PaperImportError(ValueError):
    pass


def visible_questions(user):
    return Question.objects.filter(reviewed=True, correct_index__isnull=False).filter(Q(owner__isnull=True) | Q(owner=user))


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
    explanation_started = False

    def finish():
        nonlocal current, choice, explanation_started
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
        current, choice, explanation_started = None, None, False

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
                    normalized = str(int(number))
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
            if match:
                finish()
                current = {'source_number': str(int(match.group(1))), 'source_page': page_number,
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
                inline = list(INLINE_OPTION.finditer(line))
                if len(inline) > 1 and inline[0].start() == 0:
                    for i, option in enumerate(inline):
                        end = inline[i+1].start() if i+1 < len(inline) else len(line)
                        append_option_line(line[option.start():end])
                elif append_option_line(line):
                    pass
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
        existing = ExamPaper.objects.filter(owner=owner, document=document).first()
        if existing:
            return existing, False
        questions, warnings = parse_paper(document.pages)
        paper = ExamPaper.objects.create(owner=owner, document=document, title=title, topic=topic, warnings=warnings)
        Question.objects.bulk_create([
            Question(owner=owner, paper=paper, source_document=document, topic=topic,
                     reference=f'{document.title}, page/section {question["source_page"]}, question {question["source_number"]}'[:300],
                     reviewed=False, **question)
            for question in questions
        ])
        return paper, True
