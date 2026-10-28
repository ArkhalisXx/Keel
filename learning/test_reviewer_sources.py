from types import SimpleNamespace

from django.test import SimpleTestCase

from .assistant import AIError
from .reviewer_sources import available_page_bounds, document_outline, select_sources, validate_scope


def document(pages):
    return SimpleNamespace(pk=7, title='Marine engineering notes', pages=pages)


class ReviewerSourceTests(SimpleTestCase):
    def test_explicit_outline_uses_physical_pages_and_chapter_hierarchy(self):
        doc = document([
            {'page': 1, 'text': 'Contents\nChapter 1 ... 4\nChapter 2 ... 8'},
            {'page': 4, 'text': 'CHAPTER 1\nHydrostatics\nBuoyancy supports ships.'},
            {'page': 5, 'text': 'Section 1.1: Water pressure\nPressure varies with depth.'},
            {'page': 6, 'text': 'CHAPTER 1\nMore buoyancy notes.'},
            {'page': 8, 'text': 'Chapter 2: Propulsion\nEngines supply power.'},
            {'page': 10, 'text': 'More propulsion.'},
        ])
        self.assertEqual(available_page_bounds(doc), (1, 10))
        self.assertEqual(document_outline(doc), [
            {'label': 'CHAPTER 1 — Hydrostatics', 'start': 4, 'end': 7},
            {'label': 'Section 1.1: Water pressure', 'start': 5, 'end': 7},
            {'label': 'Chapter 2: Propulsion', 'start': 8, 'end': 10},
        ])

    def test_selected_pages_are_a_hard_boundary(self):
        doc = document([{'page': i, 'text': f'Page {i} about buoyancy'} for i in range(1, 21)])
        for offset in range(3):
            sources = select_sources(doc, page_start=5, page_end=8, offset=offset)
            self.assertEqual({item['page'] for item in sources}, {5, 6, 7, 8})
            self.assertTrue(all(len(item['text']) <= 2400 for item in sources))

    def test_topic_selects_matching_excerpts_even_late_on_a_page(self):
        doc = document([
            {'page': 1, 'text': 'Unrelated material. ' * 180 + '\nBuoyancy supports a floating ship.'},
            {'page': 2, 'text': 'Combustion engines and fuel consumption.'},
        ])
        sources = select_sources(doc, focus='buoyancy')
        self.assertTrue(sources)
        self.assertTrue(all(item['page'] == 1 and 'Buoyancy' in item['text'] for item in sources))

    def test_no_topic_match_never_uses_unrelated_pages(self):
        doc = document([{'page': 1, 'text': 'Buoyancy notes'}, {'page': 2, 'text': 'Propulsion notes'}])
        with self.assertRaisesRegex(AIError, 'No matching passages'):
            select_sources(doc, focus='propulsion', page_start=1, page_end=1)
        with self.assertRaisesRegex(AIError, 'No matching passages'):
            select_sources(doc, focus='thermodynamics')

    def test_focus_at_an_excerpt_boundary_is_still_found(self):
        doc = document([{'page': 1, 'text': 'x ' * 1198 + 'thermodynamics is useful.'}])
        sources = select_sources(doc, focus='thermodynamics')
        self.assertTrue(any('thermodynamics' in source['text'] for source in sources))

    def test_selected_chapter_includes_continuations_but_not_next_chapter(self):
        doc = document([
            {'page': 1, 'text': 'Chapter 1: Hydrostatics\nBuoyancy.'},
            {'page': 2, 'text': 'A continuation without the chapter heading.'},
            {'page': 3, 'text': 'Chapter 2: Engines\nCombustion.'},
        ])
        sources = select_sources(doc, focus='Chapter 1: Hydrostatics')
        self.assertEqual({item['page'] for item in sources}, {1, 2})
        sources = select_sources(doc, focus='Chapter 1', page_start=2, page_end=3)
        self.assertEqual({item['page'] for item in sources}, {2})

    def test_invalid_ranges_and_blank_pages_are_rejected(self):
        doc = document([{'page': 1, 'text': 'Readable'}, {'page': 2, 'text': ''}, {'page': 3, 'text': 'Readable'}])
        for start, end in [(0, 1), (1, 4), (3, 1), (True, 3), ('1', 3)]:
            with self.assertRaises(AIError):
                validate_scope(doc, page_start=start, page_end=end)
        with self.assertRaisesRegex(AIError, 'no readable text'):
            select_sources(doc, page_start=2, page_end=2)
        with self.assertRaises(AIError):
            validate_scope(doc, focus='x' * 241)
        self.assertEqual(validate_scope(doc), ('', 1, 3))

    def test_plain_text_without_headings_has_no_fabricated_chapters(self):
        doc = document([{'page': 1, 'text': 'A textbook with ordinary paragraphs.\nBuoyancy and propulsion.'}])
        self.assertEqual(document_outline(doc), [])
        self.assertEqual(available_page_bounds(document([])), (0, 0))

    def test_batches_rotate_across_longer_selected_material(self):
        doc = document([{'page': i, 'text': f'Notes {i}.'} for i in range(1, 31)])
        first = select_sources(doc, offset=0)
        later = select_sources(doc, offset=1)
        self.assertEqual(len(first), 6)
        self.assertNotEqual([p['page'] for p in first], [p['page'] for p in later])
        batch = select_sources(doc, offset=5)
        self.assertNotEqual([p['page'] for p in first], [p['page'] for p in batch])
        self.assertTrue(all(1 <= p['page'] <= 30 for p in first + later))
