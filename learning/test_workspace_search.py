from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Document, ExamPaper, Question


class WorkspaceSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('finder-reader')
        self.other = get_user_model().objects.create_user('private-reader')
        self.client.force_login(self.user)
        self.url = reverse('workspace_search')
        self.document = Document.objects.create(owner=self.user, title='Ship stability', file='notes.txt', status='ready')
        self.paper = ExamPaper.objects.create(owner=self.user, document=self.document, title='Ship review', topic='Stability')

    def question(self, **changes):
        fields = dict(owner=self.user, paper=self.paper, source_document=self.document,
                      prompt='How does ship stability work?', topic='Stability', choices=['a','b','c','d'], correct_index=0, reviewed=True)
        fields.update(changes)
        return Question.objects.create(**fields)

    def test_results_link_to_real_sections_and_do_not_return_source_contents(self):
        question = self.question()
        self.document.pages = [{'page': 1, 'text': 'Private extracted source content'}]
        self.document.save()
        response = self.client.get(self.url, {'q': 'ship'})
        results = response.json()['results']
        self.assertEqual({item['kind'] for item in results}, {'document', 'paper', 'question'})
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertNotIn('Private extracted', response.content.decode())
        self.assertIn(reverse('question', args=[question.pk]), [item['url'] for item in results])
        self.assertTrue(all(item['url'].startswith('/') for item in results))

    def test_other_accounts_drafts_and_malformed_shared_metadata_are_excluded(self):
        private_doc = Document.objects.create(owner=self.other, title='Ship private notes', file='secret.txt')
        private_paper = ExamPaper.objects.create(owner=self.other, document=private_doc, title='Ship private paper', topic='Ship')
        self.question(owner=self.other, paper=private_paper, source_document=private_doc, prompt='Ship private question')
        self.question(reviewed=False, prompt='Ship draft question')
        self.question(owner=None, paper=private_paper, source_document=private_doc, prompt='Ship malformed shared row')
        self.question(owner=None, paper=None, source_document=None, prompt='Ship public practice')
        titles = [item['title'] for item in self.client.get(self.url, {'q':'ship'}).json()['results']]
        self.assertIn('Ship public practice', titles)
        self.assertFalse(any(word in title for title in titles for word in ['private', 'draft', 'malformed']))

    def test_short_query_is_empty_without_running_search_queries(self):
        # Authentication needs the session/user queries; the view itself needs none.
        from django.test import RequestFactory
        from .workspace_search import workspace_search
        request = RequestFactory().get(self.url, {'q':'s'})
        request.user = self.user
        with self.assertNumQueries(0):
            response = workspace_search(request)
        self.assertEqual(response.content, b'{"results": []}')

    def test_per_category_limits_keep_results_small(self):
        for index in range(8):
            Document.objects.create(owner=self.user, title=f'Ship document {index}', file='book.txt')
            ExamPaper.objects.create(owner=self.user, title=f'Ship paper {index}', topic='Ship')
            self.question(prompt=f'Ship question {index}')
        results = self.client.get(self.url, {'q':'ship'}).json()['results']
        self.assertEqual(len(results), 12)
        for kind in ['document','paper','question']:
            self.assertEqual(sum(item['kind'] == kind for item in results), 4)

    def test_case_insensitive_topic_matches_and_literal_special_characters(self):
        self.question(topic='Hydrostatics', prompt='Compare two hulls.')
        response = self.client.get(self.url, {'q':'HYDROSTATICS'})
        self.assertEqual(response.json()['results'][0]['kind'], 'question')
        self.assertEqual(self.client.get(self.url, {'q': '<script>'}).json()['results'], [])

    def test_authentication_and_get_required(self):
        self.assertEqual(self.client.post(self.url, {'q':'ship'}).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url, {'q':'ship'}).status_code, 302)
