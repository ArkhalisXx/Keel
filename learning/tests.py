import io
import json
import os
import tempfile
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from .models import Document, Question, Attempt
from .services import retrieve, explain, extract_pages

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True, OCR_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkflowTests(TestCase):
    def upload(self, data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/library/', data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user('student', password='test-password-934')
        self.other = User.objects.create_user('other', password='test-password-934')
        self.client.force_login(self.user)
        self.q = Question.objects.create(topic='Hydrostatics', prompt='Unit of force?',choices=['N','kg','m','s'],correct_index=0,explanation='The newton is the SI unit of force.',reviewed=True)

    def test_registration_and_login(self):
        self.client.logout()
        response = self.client.post('/accounts/register/',{'username':'newstudent','password1':'strong-test-password-392','password2':'strong-test-password-392'})
        self.assertRedirects(response,'/')
        self.assertEqual(self.client.session['_auth_user_id'], str(User.objects.get(username='newstudent').pk))

    def test_upload_retrieval_download_and_delete(self):
        response = self.upload({'title':'Notes','file':SimpleUploadedFile('notes.txt',b'Buoyancy equals the weight of displaced water.')})
        self.assertRedirects(response,'/library/')
        doc = Document.objects.get()
        self.assertEqual(doc.status, Document.Status.READY)
        self.assertEqual(doc.owner,self.user)
        sources = retrieve([doc],'Explain buoyancy')
        self.assertIn('displaced water',sources[0]['text'])
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code,200)
        response = self.client.post('/tutor/',{'documents':[doc.pk],'question':'Explain buoyancy'})
        self.assertContains(response,'Source passages')
        self.client.post(f'/documents/{doc.pk}/delete/')
        self.assertFalse(Document.objects.exists())

    def test_private_documents_and_attempts(self):
        doc = Document.objects.create(owner=self.other,title='Other account confidential notes',file='private.txt',pages=[{'page':1,'text':'secret buoyancy'}],status=Document.Status.READY)
        self.assertNotContains(self.client.get('/library/'),'Other account confidential notes')
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/').status_code,404)
        self.assertEqual(self.client.post(f'/documents/{doc.pk}/delete/').status_code,404)
        response = self.client.post('/tutor/',{'documents':[doc.pk],'question':'buoyancy'})
        self.assertContains(response,'Select a valid choice')
        a = Attempt.objects.create(owner=self.other,question=self.q,selected_index=0,correct=True)
        self.assertEqual(self.client.get(f'/results/{a.pk}/').status_code,404)

    def test_scoring_is_server_side_and_post_redirect_get(self):
        response = self.client.post(f'/practice/{self.q.pk}/',{'choice':'1','correct':'true'})
        a = Attempt.objects.get()
        self.assertFalse(a.correct)
        self.assertRedirects(response,f'/results/{a.pk}/')
        self.client.get(response.url)
        self.assertEqual(Attempt.objects.count(),1)
        self.assertContains(self.client.get('/'),'0%')

    def test_no_solution_before_answer_and_unreviewed_hidden(self):
        self.assertNotContains(self.client.get(f'/practice/{self.q.pk}/'),self.q.explanation)
        self.q.reviewed=False; self.q.save()
        self.assertEqual(self.client.get(f'/practice/{self.q.pk}/').status_code,404)
        self.assertNotContains(self.client.get('/practice/'),'Unit of force?')

    def test_invalid_choice_and_file_rejected(self):
        self.client.post(f'/practice/{self.q.pk}/',{'choice':'99'})
        self.assertEqual(Attempt.objects.count(),0)
        response=self.upload({'title':'Bad','file':SimpleUploadedFile('bad.exe',b'bad')})
        self.assertContains(response,'Supported formats')
        self.assertEqual(Document.objects.count(),0)

    def test_pages_and_authentication(self):
        for url in ['/','/library/','/practice/','/tutor/']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.client.logout()
        for url in ['/','/library/','/practice/','/tutor/']:
            self.assertEqual(self.client.get(url).status_code,302)

    def test_docx_and_pdf_extraction(self):
        from docx import Document as Docx
        from pypdf import PdfWriter
        stream=io.BytesIO(); d=Docx();d.add_paragraph('Marine engineering notes');d.save(stream)
        self.assertIn('Marine',extract_pages(SimpleUploadedFile('notes.docx',stream.getvalue()))[0]['text'])
        stream=io.BytesIO(); w=PdfWriter();w.add_blank_page(width=100,height=100);w.write(stream)
        with self.assertRaises(ValueError): extract_pages(SimpleUploadedFile('scan.pdf',stream.getvalue()))

    @patch.dict(os.environ, {'AI_PROVIDER':'openai','OPENAI_API_KEY':''})
    def test_no_key_does_not_call_provider(self):
        with patch('urllib.request.urlopen') as call:
            self.assertIsNone(explain('question',[]));call.assert_not_called()

    def test_provider_response_formats(self):
        cases=[('openai','OPENAI_API_KEY',{'output':[{'content':[{'type':'output_text','text':'Explanation [1]'}]}]}),('gemini','GEMINI_API_KEY',{'candidates':[{'content':{'parts':[{'text':'Explanation [1]'}]}}]})]
        for provider,key,payload in cases:
            with self.subTest(provider=provider), patch.dict(os.environ,{'AI_PROVIDER':provider,key:'test-key'}), patch('urllib.request.urlopen') as call:
                call.return_value.__enter__.return_value=io.BytesIO(json.dumps(payload).encode())
                self.assertEqual(explain('force',[{'title':'Notes','page':1,'text':'Force is measured in newtons.'}]),'Explanation [1]')
                request=call.call_args.args[0]
                self.assertIn(b'Force is measured',request.data)

    def test_csrf_enforced(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(f'/practice/{self.q.pk}/',{'choice':'0'}).status_code,403)

    def test_upload_over_old_size_and_text_limits(self):
        text = b'Marine engineering notes.\n' * 450_000
        self.assertGreater(len(text), 10 * 1024 * 1024)
        response = self.upload( {'title': 'Large textbook', 'file': SimpleUploadedFile('large.txt', text)})
        self.assertRedirects(response, '/library/')
        doc = Document.objects.get(title='Large textbook')
        self.assertEqual(len(doc.pages[0]['text']), len(text))

    @override_settings(DOCUMENT_MAX_UPLOAD_MB=1, DOCUMENT_MAX_PDF_PAGES=2, DOCUMENT_MAX_TEXT_CHARS=20)
    def test_configurable_limits_and_precise_errors(self):
        response = self.upload( {'title':'Large', 'file':SimpleUploadedFile('large.txt', b'x' * (1024 * 1024 + 1))})
        self.assertContains(response, 'no larger than 1 MB')
        response = self.upload( {'title':'Too much text', 'file':SimpleUploadedFile('notes.txt', b'x' * 21)})
        self.assertContains(self.client.get('/library/'), 'exceeds 20 characters')
        from pypdf import PdfWriter
        stream = io.BytesIO(); writer = PdfWriter()
        for _ in range(3): writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        response = self.upload( {'title':'Too many pages','file':SimpleUploadedFile('book.pdf', stream.getvalue())})
        response = self.client.get('/library/')
        self.assertContains(response, 'exceeds 2 pages')
        self.assertContains(response, '1 MB · 2 PDF pages')
        self.assertEqual(Document.objects.filter(status=Document.Status.FAILED).count(), 2)

    def test_pdf_over_old_page_limit_and_locked_pdf(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        first = writer.pages[0]
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        first[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b'BT /F1 12 Tf 10 50 Td (Naval architecture) Tj ET')
        first[NameObject('/Contents')] = writer._add_object(content)
        for _ in range(500): writer.add_blank_page(width=100, height=100)
        stream = io.BytesIO(); writer.write(stream)
        pages = extract_pages(SimpleUploadedFile('book.pdf',stream.getvalue()))
        self.assertEqual(len(pages),501)
        self.assertIn('Naval architecture',pages[0]['text'])
        writer.encrypt('password')
        stream = io.BytesIO(); writer.write(stream)
        response = self.upload( {'title':'Locked', 'file':SimpleUploadedFile('locked.pdf',stream.getvalue())})
        self.assertContains(self.client.get('/library/'),'password-protected')

    def test_scan_error_explains_ocr(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO();writer.write(stream)
        response=self.upload({'title':'Scan','file':SimpleUploadedFile('scan.pdf',stream.getvalue())})
        response=self.client.get('/library/')
        self.assertContains(response,'No readable text found')
        self.assertContains(response,'OCR must be enabled')
