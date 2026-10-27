from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

from .exam_views import import_paper, paper_detail, review_question
from .exams import PaperImportError, import_document, parse_paper, visible_questions
from .models import Document, ExamPaper, Question

urlpatterns = [path('import/', import_paper, name='import_paper'),
               path('papers/<int:pk>/', paper_detail, name='paper_detail'),
               path('review/<int:pk>/', review_question, name='review_question')]

PAGES = [
    {'page': 3, 'text': 'Mock practice paper\n1. What is the SI unit of force?\nA. Newton\nB. Kilogram\nC. Metre\nD. Second\n'},
    {'page': 4, 'text': '2) What supports a floating ship?\nA) Its engines\nB) Buoyancy\nC) Its anchor\nD) Atmospheric pressure\nAnswer key\n1. A 2. B'}
]


class PaperParserTests(TestCase):
    def test_numbered_questions_and_answer_keys_preserve_source(self):
        questions, warnings = parse_paper(PAGES)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]['choices'], ['Newton', 'Kilogram', 'Metre', 'Second'])
        self.assertEqual(questions[1]['correct_index'], 1)
        self.assertEqual([q['source_page'] for q in questions], [3, 4])
        self.assertEqual([q['position'] for q in questions], [1, 2])
        self.assertTrue(any('drafts' in message for message in warnings))

    def test_multiline_choices_inline_options_and_missing_key(self):
        questions, warnings = parse_paper([{'page': 1, 'text': '1. Select the best answer.\nA. First\ncontinued text\nB. Second\nC. Third\nD. Fourth\n2. Another question A) One B) Two C) Three D) Four'}])
        self.assertEqual(questions[0]['choices'][0], 'First continued text')
        self.assertEqual(questions[1]['prompt'], 'Another question')
        self.assertEqual(questions[1]['choices'], ['One', 'Two', 'Three', 'Four'])
        self.assertTrue(all(q['correct_index'] is None for q in questions))
        self.assertTrue(any('no unambiguous answer' in message for message in warnings))

    def test_ambiguous_or_incomplete_questions_are_not_silently_published(self):
        pages = [{'page': 1, 'text': '1. Incomplete?\nA. One\nB. Two\n2. Complete?\nA. One\nB. Two\nC. Three\nD. Four\nAnswer: A\nAnswer key\n2. B'}]
        questions, warnings = parse_paper(pages)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]['source_number'], '2')
        self.assertIsNone(questions[0]['correct_index'])
        self.assertTrue(any('skipped' in message for message in warnings))
        self.assertTrue(any('disagree' in message for message in warnings))

    def test_inline_answer_key_and_printed_explanation(self):
        pages = [{'page': 1, 'text': '1. Force unit?\nA. Newton\nB. Metre\nC. Second\nD. Kilogram\nExplanation: Force is measured in newtons.\nOne newton is one kg m/s squared.\nAnswer key: 1 A'}]
        questions, _ = parse_paper(pages)
        self.assertEqual(questions[0]['correct_index'], 0)
        self.assertEqual(questions[0]['choices'][3], 'Kilogram')
        self.assertIn('kg m/s', questions[0]['explanation'])

    def test_import_limits_fail_explicitly(self):
        with self.assertRaisesRegex(PaperImportError, 'No complete'):
            parse_paper([{'page': 1, 'text': 'A scanned drawing with no questions'}])
        with patch('learning.exams.MAX_IMPORT_CHARACTERS', 10):
            with self.assertRaisesRegex(PaperImportError, 'too long'):
                parse_paper(PAGES)
        with patch('learning.exams.MAX_QUESTIONS', 1):
            with self.assertRaisesRegex(PaperImportError, 'up to 1'):
                parse_paper(PAGES)


@override_settings(ROOT_URLCONF='learning.test_exams')
class PrivateExamWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('exam-owner')
        self.other = User.objects.create_user('other-owner')
        self.doc = Document.objects.create(owner=self.owner, title='My mock paper', file='mock.pdf',
                                          status=Document.Status.READY, pages=PAGES)
        self.client.force_login(self.owner)

    def import_paper(self):
        return import_document(self.owner, self.doc, 'Mock paper', 'Hydrostatics')[0]

    def review_data(self, action='publish'):
        return {'topic': 'Hydrostatics', 'prompt': 'What is the SI unit of force?', 'choice_a': 'Newton',
                'choice_b': 'Kilogram', 'choice_c': 'Metre', 'choice_d': 'Second', 'correct_index': '0',
                'explanation': 'The newton is the SI unit of force.', 'reference': 'Verified reference', 'action': action}

    def test_import_creates_owned_drafts_and_is_idempotent(self):
        paper = self.import_paper()
        again, created = import_document(self.owner, self.doc, 'Duplicate name', 'Other')
        self.assertFalse(created)
        self.assertEqual(again.pk, paper.pk)
        self.assertEqual(paper.questions.count(), 2)
        self.assertTrue(all(q.owner_id == self.owner.pk for q in paper.questions.all()))
        self.assertFalse(visible_questions(self.owner).exists())
        first = paper.questions.order_by('position').first()
        self.assertEqual(first.source_page, 3)
        self.assertEqual(first.source_document_id, self.doc.pk)
        self.assertIn('question 1', first.reference)

    def test_non_owner_cannot_import_or_review_private_papers(self):
        paper = self.import_paper()
        question = paper.questions.first()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/papers/{paper.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/review/{question.pk}/', self.review_data()).status_code, 404)
        with self.assertRaises(PaperImportError):
            import_document(self.other, self.doc, 'Forbidden', 'Topic')
        with patch('learning.exam_views.render', return_value=HttpResponse()) as render:
            self.client.post('/import/', {'title': 'Forbidden', 'topic': 'Topic', 'document': self.doc.pk})
            form = render.call_args.args[2]['form']
            self.assertIn('document', form.errors)
        self.assertEqual(ExamPaper.objects.filter(owner=self.other).count(), 0)

    def test_review_publish_is_private_and_draft_save_removes_from_practice(self):
        paper = self.import_paper(); question = paper.questions.order_by('position').first()
        response = self.client.post(f'/review/{question.pk}/', self.review_data())
        self.assertRedirects(response, f'/papers/{paper.pk}/', fetch_redirect_response=False)
        question.refresh_from_db(); self.assertTrue(question.reviewed)
        self.assertIn(question, visible_questions(self.owner))
        self.assertNotIn(question, visible_questions(self.other))
        self.client.post(f'/review/{question.pk}/', self.review_data(action='draft'))
        question.refresh_from_db(); self.assertFalse(question.reviewed)
        self.assertNotIn(question, visible_questions(self.owner))

    def test_publish_requires_verified_answer_and_explanation(self):
        question = self.import_paper().questions.first()
        data = self.review_data(); data['correct_index'] = ''; data['explanation'] = ''
        with patch('learning.exam_views.render', return_value=HttpResponse()) as render:
            self.client.post(f'/review/{question.pk}/', data)
            errors = render.call_args.args[2]['form'].errors
            self.assertIn('correct_index', errors); self.assertIn('explanation', errors)
        question.refresh_from_db(); self.assertFalse(question.reviewed)
        question.reviewed = True; question.correct_index = None
        with self.assertRaises(ValidationError): question.full_clean()

    def test_review_form_persists_edited_choices(self):
        question = self.import_paper().questions.first()
        data = self.review_data(); data['choice_b'] = 'Updated option'
        self.client.post(f'/review/{question.pk}/', data)
        question.refresh_from_db()
        self.assertEqual(question.choices[1], 'Updated option')

    def test_shared_reviewed_questions_remain_visible(self):
        shared = Question.objects.create(topic='Shared', prompt='Shared question?', choices=['a','b','c','d'],
                                         correct_index=0, explanation='Explanation', reviewed=True)
        self.assertIn(shared, visible_questions(self.owner))
        self.assertIn(shared, visible_questions(self.other))

    def test_sort_parameter_is_whitelisted_and_unready_document_rejected(self):
        paper = self.import_paper()
        with patch('learning.exam_views.render', return_value=HttpResponse()) as render:
            self.client.get(f'/papers/{paper.pk}/?sort=owner__password')
            context = render.call_args.args[2]
            self.assertEqual(context['sort'], 'original')
        self.doc.status = Document.Status.PROCESSING; self.doc.save(update_fields=['status'])
        with self.assertRaisesRegex(PaperImportError, 'ready'):
            import_document(self.owner, self.doc, 'Not ready', 'Topic')
