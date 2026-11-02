from types import SimpleNamespace
from django.test import SimpleTestCase
from .activity_sources import activity_questions
from .assistant import AIError
from .reviewer_sources import select_sources


def book(pages):
    return SimpleNamespace(pk=9, title='Marine textbook', pages=[{'page': page, 'text': text} for page, text in pages])


class ActivitySourceTests(SimpleTestCase):
    def test_real_sections_skip_contents_mentions_worked_examples_and_next_chapter(self):
        doc = book([
            (1, 'Contents\nReview Questions ... 7\n1. An item in contents.'),
            (2, 'Chapter 1: Buoyancy\nThe exercises in this chapter reinforce concepts.\n1. This is ordinary numbered prose.'),
            (3, 'Worked Example 1\n1. Calculate an example answer.\nAnswer: 3'),
            (4, 'Review Questions\n1. Define buoyancy.\n2. Explain metacentric height.'),
            (5, 'Chapter 2: Engines\n1. Ordinary numbered engine notes.'),
        ])
        result = activity_questions(doc)
        self.assertEqual([record['prompt'] for record in result], ['Define buoyancy.', 'Explain metacentric height.'])
        self.assertEqual([record['page'] for record in result], [4, 4])

    def test_cross_page_continuation_and_footer_are_preserved_with_hard_page_bounds(self):
        doc = book([
            (2, 'Chapter 1: Pressure\nExercises\n1. Calculate pressure at depth 2 m.\n2'),
            (3, 'Chapter 1: Pressure\nUse density 1000 kg/m3 and g=9.81 m/s2.\n2. Define pressure.\n3'),
            (4, 'Chapter 2: Engines\nNew material.'),
        ])
        result = activity_questions(doc)
        self.assertEqual(len(result), 2)
        self.assertIn('density 1000', result[0]['prompt'])
        self.assertEqual((result[0]['page'], result[0]['end_page']), (2, 3))
        self.assertEqual([record['number'] for record in result], ['1', '2'])
        self.assertEqual([record['number'] for record in activity_questions(doc, page_start=3, page_end=3)], ['2'])
        with self.assertRaises(AIError):
            activity_questions(doc, page_start=2, page_end=2)

    def test_chapter_and_title_scope_include_implicit_questions_but_not_other_chapters(self):
        doc = book([
            (1, 'Chapter 1: Hydrostatics\nReview Questions\n1. Calculate water pressure.\n2. Explain transverse stability.'),
            (2, 'Chapter 2: Engines\nQuiz\n1. Define combustion.'),
        ])
        for focus in ['Chapter 1', 'Hydrostatics']:
            result = activity_questions(doc, focus=focus)
            self.assertEqual([record['page'] for record in result], [1, 1])
        self.assertEqual(activity_questions(doc, focus='combustion')[0]['page'], 2)
        with self.assertRaises(AIError):
            activity_questions(doc, focus='combustion', page_start=1, page_end=1)

    def test_choices_and_printed_keys_are_scoped_to_the_assessment(self):
        doc = book([(1, 'Quiz 1\n1. What supports a ship?\nA. Buoyancy\nB. Drag\nC. Thrust\nD. Friction\nAnswer Key\n1. A\n'
                           'Quiz 2\n1. What resists motion?\nA. Thrust\nB. Buoyancy\nC. Drag\nD. Gravity\nAnswers\n1. C')])
        result = activity_questions(doc)
        self.assertEqual([record['correct_index'] for record in result], [0, 2])
        self.assertNotEqual(result[0]['source_id'], result[1]['source_id'])
        self.assertEqual(result[0]['choices'], ['Buoyancy', 'Drag', 'Thrust', 'Friction'])

    def test_short_answer_starting_with_a_letter_is_not_a_letter_key(self):
        doc = book([(1, 'Activities\n1. What supports a vessel?\nA. Buoyancy\nB. Drag\nC. Thrust\nD. Friction\nAnswer: Buoyancy')])
        self.assertEqual(activity_questions(doc)[0]['correct_index'], 0)
        doc.pages[0]['text'] = doc.pages[0]['text'].replace('Answer: Buoyancy', 'Answer: Buoyant force')
        self.assertIsNone(activity_questions(doc)[0]['correct_index'])

    def test_inline_options_unnumbered_questions_and_duplicate_questions(self):
        doc = book([(1, 'Self-Test\n1. What supports a ship? A. Buoyancy B. Drag C. Thrust D. Friction\n'
                           '2. What supports a ship? A. Buoyancy B. Drag C. Thrust D. Friction\n'
                           'Knowledge Check\nWhy does a vessel float?')])
        result = activity_questions(doc)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['prompt'], 'What supports a ship?')
        self.assertEqual(len(result[0]['choices']), 4)
        self.assertEqual(result[1]['prompt'], 'Why does a vessel float?')

    def test_numbered_subsection_ends_assessment_instead_of_becoming_a_question(self):
        doc = book([(1, 'Exercises 1.1\n1. Define pressure.\n1.2 Hydrostatic forces\n1. Ordinary numbered notes.')])
        self.assertEqual([record['prompt'] for record in activity_questions(doc)], ['Define pressure.'])

    def test_no_questions_never_falls_back_to_notes_and_source_selection_never_wraps(self):
        doc = book([(1, 'Chapter 1: Notes\nBuoyancy is discussed in the exercises later.')])
        with self.assertRaisesRegex(AIError, 'No complete activity'):
            activity_questions(doc)
        doc = book([(1, 'Quiz\n1. Define buoyancy.\n2. Define pressure.')])
        first = select_sources(doc, source_mode='activities', offset=0)
        self.assertEqual(len(first), 2)
        self.assertEqual(select_sources(doc, source_mode='activities', offset=2), [])
        self.assertTrue(all(record['source'] == 'activity' for record in first))

    def test_incomplete_or_five_choice_blocks_are_not_silently_changed_into_open_questions(self):
        for text in [
            'Quiz\n1. Choose the answer.\nA. First\nB. Second',
            'Quiz\n1. Choose the answer. A. First B. Second C. Third D. Fourth E. Fifth',
        ]:
            with self.subTest(text=text), self.assertRaises(AIError):
                activity_questions(book([(1, text)]))
