import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from .activity_sources import activity_questions
from .assistant import AIError
from .exams import PaperImportError
from .models import Document, ExamPaper
from .reviewers import create_reviewer, generate_items, generate_reviewer, queue_reviewer


class ActivityReviewerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('activities')
        self.doc = Document.objects.create(owner=self.user, title='Textbook', file='book.pdf', status='ready', pages=[{
            'page': 1, 'text': 'Chapter 1: Stability\nReview Questions\n1. What supports a vessel?\nA. Buoyancy\nB. Drag\nC. Thrust\nD. Friction\n'
            '2. What resists motion?\nA. Buoyancy\nB. Drag\nC. Thrust\nD. Gravity\nAnswer Key\n1. A 2. B'}])

    def test_creation_caps_to_existing_questions_and_persists_source(self):
        with patch('learning.reviewers.generate_reviewer.delay'), self.captureOnCommitCallbacks(execute=True):
            paper, created = create_reviewer(self.user, self.doc, 'Activities', 'Stability', 50, source_mode='activities')
        self.assertTrue(created)
        self.assertEqual(paper.generation_source, 'activities')
        self.assertEqual(paper.requested_count, 2)
        self.assertTrue(any('limited' in warning for warning in paper.warnings))

    def test_no_assessment_or_wrong_owner_is_rejected_before_queue_or_api(self):
        self.doc.pages = [{'page': 1, 'text': 'Ordinary stability notes.'}]
        self.doc.save()
        with patch('learning.reviewers.answer_question') as answer, patch('learning.reviewers.generate_reviewer.delay') as queue:
            with self.assertRaises(PaperImportError):
                create_reviewer(self.user, self.doc, 'Activities', 'Stability', 1, source_mode='activities')
            other = get_user_model().objects.create_user('other-activities')
            with self.assertRaises(PaperImportError):
                create_reviewer(other, self.doc, 'Activities', 'Stability', 1, source_mode='activities')
        answer.assert_not_called(); queue.assert_not_called()
        self.assertFalse(ExamPaper.objects.exists())

    def test_complete_printed_keys_are_reused_without_ai_and_original_question_is_kept(self):
        with patch('learning.reviewers.answer_question') as answer:
            items = generate_items(self.doc, 2, source_mode='activities')
        answer.assert_not_called()
        self.assertEqual([item['correct_index'] for item in items], [0, 1])
        self.assertEqual(items[0]['prompt'], 'What supports a vessel?')
        self.assertEqual(items[0]['source_number'], '1')
        self.assertIn('printed answer key', items[0]['explanation'])

    def test_batches_save_provenance_and_do_not_rephrase_or_repeat_a_saved_activity(self):
        with patch('learning.reviewers.generate_reviewer.delay'):
            paper, _ = create_reviewer(self.user, self.doc, 'Activities', 'Stability', 2, source_mode='activities')
        paper.refresh_from_db()
        with patch('learning.reviewers.answer_question') as answer:
            generate_reviewer.run(paper.pk, str(paper.generation_token))
        answer.assert_not_called()
        paper.refresh_from_db()
        self.assertEqual(paper.generation_status, 'ready')
        questions = list(paper.questions.order_by('position'))
        self.assertEqual([question.source_number for question in questions], ['1', '2'])
        self.assertTrue(all(question.activity_source_id.startswith('act-') for question in questions))
        self.assertIn('Review Questions', questions[0].reference)
        self.assertIn('printed key', questions[0].reference)
        remaining = generate_items(self.doc, 1, source_mode='activities', previous_source_ids=[questions[0].activity_source_id])
        self.assertEqual(remaining[0]['activity_source_id'], questions[1].activity_source_id)
        with self.assertRaises(AIError):
            generate_items(self.doc, 1, source_mode='activities', previous_source_ids=[question.activity_source_id for question in questions])

    def test_missing_answer_cannot_change_the_original_prompt_or_choices(self):
        self.doc.pages[0]['text'] = self.doc.pages[0]['text'].split('Answer Key')[0]
        record = activity_questions(self.doc)[0]
        payload = {'source_id': record['source_id'], 'prompt': 'An unrelated model-written question',
                   'choices': record['choices'], 'correct_index': 0, 'explanation': 'The question concerns buoyant support.'}
        with patch('learning.reviewers.answer_question', return_value={'answer': json.dumps([payload])}) as answer:
            items = generate_items(self.doc, 1, source_mode='activities')
        self.assertEqual(items[0]['prompt'], 'What supports a vessel?')
        self.assertEqual(items[0]['choices'], record['choices'])
        self.assertIn(record['source_id'], answer.call_args.kwargs['document_sources'][0]['text'])
        self.assertIn('Do not write new questions', answer.call_args.args[0])

    def test_reordered_choices_or_unknown_activity_identity_are_rejected(self):
        self.doc.pages[0]['text'] = self.doc.pages[0]['text'].split('Answer Key')[0]
        record = activity_questions(self.doc)[0]
        payload = {'source_id': record['source_id'], 'choices': record['choices'], 'correct_index': 0, 'explanation': 'Explanation.'}
        variants = [dict(payload, choices=list(reversed(record['choices']))), dict(payload, source_id='invented'), dict(payload, source_id=['invalid'])]
        for variant in variants:
            with self.subTest(variant=variant), patch('learning.reviewers.answer_question', return_value={'answer': json.dumps([variant])}):
                with self.assertRaises(AIError):
                    generate_items(self.doc, 1, source_mode='activities')

    def test_notes_remain_the_default_and_invalid_mode_is_rejected(self):
        with patch('learning.reviewers.generate_reviewer.delay'):
            paper, _ = create_reviewer(self.user, self.doc, 'Notes reviewer', 'Stability', 1)
        self.assertEqual(paper.generation_source, 'notes')
        with self.assertRaises(PaperImportError):
            create_reviewer(self.user, self.doc, 'Invalid', 'Stability', 1, source_mode='unsupported')
