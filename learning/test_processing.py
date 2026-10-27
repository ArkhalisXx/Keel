"""Document lifecycle, access control, worker failures and real scanned-PDF OCR."""
import io
import shutil
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .forms import TutorForm
from .models import Document
from .services import DocumentExtractionError, extract_pages
from .tasks import process_document, queue_document


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProcessingTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings_override = override_settings(MEDIA_ROOT=temp.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = User.objects.create_user('reader')
        self.other = User.objects.create_user('other')
        self.client.force_login(self.user)

    def document(self, **kwargs):
        return Document.objects.create(owner=self.user, title='Test notes',
            file=SimpleUploadedFile('notes.txt', b'Buoyancy equals displaced water weight.'), **kwargs)

    @patch('learning.tasks.process_document.apply_async')
    def test_upload_queues_after_commit_without_extracting_in_request(self, publish):
        with patch('learning.tasks.extract_pages') as extract, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/library/', {'title':'Notes', 'file':SimpleUploadedFile('notes.txt',b'Buoyancy')})
            self.assertEqual(response.status_code,302)
            publish.assert_not_called()
        publish.assert_called_once()
        extract.assert_not_called()
        doc = Document.objects.get()
        self.assertEqual(doc.status, Document.Status.QUEUED)
        self.assertNotIn(doc, TutorForm(user=self.user).fields['documents'].queryset)
        self.assertNotIn('pages', self.client.get(f'/documents/{doc.pk}/status/').json())

    def test_worker_finishes_and_duplicate_delivery_does_not_reprocess(self):
        doc=self.document()
        process_document.run(doc.pk,str(doc.processing_token))
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.READY)
        self.assertEqual(doc.pages_processed,1)
        self.assertIn(doc,TutorForm(user=self.user).fields['documents'].queryset)
        with patch('learning.tasks.extract_pages') as extract:
            process_document.run(doc.pk,str(doc.processing_token))
            extract.assert_not_called()

    @patch('learning.tasks.process_document.apply_async', side_effect=ConnectionError())
    def test_broker_failure_and_retry_with_new_token(self,publish):
        doc=self.document()
        old=doc.processing_token
        with self.captureOnCommitCallbacks(execute=True):
            queue_document(doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.FAILED)
        self.assertIn('queue is unavailable',doc.processing_error)
        publish.side_effect=None
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/documents/{doc.pk}/retry/')
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.QUEUED)
        self.assertNotEqual(doc.processing_token,old)
        with patch('learning.tasks.extract_pages') as extract:
            process_document.run(doc.pk,str(old))
            extract.assert_not_called()
        process_document.run(doc.pk,str(doc.processing_token))
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.READY)

    def test_private_status_retry_and_active_attempt_protected(self):
        doc=self.document()
        self.assertFalse(doc.can_retry)
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/retry/').status_code,405)
        with patch('learning.tasks.process_document.apply_async') as publish:
            self.client.post(f'/documents/{doc.pk}/retry/')
            publish.assert_not_called()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/documents/{doc.pk}/status/').status_code,404)
        self.assertEqual(self.client.post(f'/documents/{doc.pk}/retry/').status_code,404)

    @patch('learning.tasks.process_document.apply_async')
    def test_stalled_processing_can_be_retried(self,publish):
        doc=self.document(status=Document.Status.PROCESSING,
            processing_deadline=timezone.now()-timedelta(seconds=1))
        old=doc.processing_token
        self.assertTrue(doc.can_retry)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/documents/{doc.pk}/retry/')
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.QUEUED)
        self.assertNotEqual(doc.processing_token,old)
        self.assertEqual(publish.call_count,1)

    @patch('learning.tasks.extract_pages', side_effect=DocumentExtractionError('OCR timed out on a page.'))
    def test_failure_is_visible_and_incomplete_text_is_not_available(self,extract):
        doc=self.document()
        process_document.run(doc.pk,str(doc.processing_token))
        doc.refresh_from_db()
        self.assertEqual(doc.status,Document.Status.FAILED)
        self.assertEqual(doc.pages,[])
        self.assertIn('OCR timed out',self.client.get(f'/documents/{doc.pk}/status/').json()['error'])
        self.assertNotIn(doc,TutorForm(user=self.user).fields['documents'].queryset)

    def test_delete_during_processing_does_not_resurrect_document(self):
        doc=self.document()
        def extract(file, progress):
            doc.delete()
            progress(1,1)
            return []
        with patch('learning.tasks.extract_pages',side_effect=extract):
            process_document.run(doc.pk,str(doc.processing_token))
        self.assertFalse(Document.objects.exists())


class OCRTests(TestCase):
    @staticmethod
    def scan_pdf():
        from PIL import Image,ImageDraw,ImageFont
        image=Image.new('RGB',(1600,700),'white')
        draw=ImageDraw.Draw(image)
        font=ImageFont.load_default(size=42)
        draw.text((80,100),'Buoyancy is the upward force on a ship.',fill='black',font=font)
        draw.text((80,200),'Water supports the hull.',fill='black',font=font)
        stream=io.BytesIO();image.save(stream,format='PDF',resolution=150);image.close()
        return stream.getvalue()

    @override_settings(OCR_ENABLED=True)
    def test_mixed_pdf_preserves_digital_text_and_marks_ocr_page(self):
        from pypdf import PdfReader,PdfWriter
        from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject
        writer=PdfWriter();writer.add_blank_page(width=300,height=200)
        page=writer.pages[0]
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 50 Td (Digital stability notes) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        writer.add_page(PdfReader(io.BytesIO(self.scan_pdf())).pages[0])
        data=io.BytesIO();writer.write(data)
        with patch('learning.ocr.PDFOCR.text',return_value='Recognized scanned notes') as ocr:
            progress=[]
            pages=extract_pages(SimpleUploadedFile('mixed.pdf',data.getvalue()),lambda done,total:progress.append((done,total)))
        self.assertIn('Digital stability notes',pages[0]['text'])
        self.assertEqual(pages[0]['source'],'text')
        self.assertEqual(pages[1]['source'],'ocr')
        ocr.assert_called_once_with(1)
        self.assertEqual(progress,[(0,2),(1,2),(2,2)])

    @override_settings(OCR_ENABLED=True)
    def test_real_tesseract_extracts_scanned_text(self):
        if not shutil.which('tesseract'):
            self.skipTest('Tesseract binary is required for real OCR integration test.')
        pages=extract_pages(SimpleUploadedFile('scan.pdf',self.scan_pdf()))
        self.assertIn('buoyancy',pages[0]['text'].lower())
        self.assertIn('water supports',pages[0]['text'].lower())
        self.assertEqual(pages[0]['source'],'ocr')

    @override_settings(OCR_ENABLED=True)
    def test_missing_tesseract_and_timeout_have_actionable_errors(self):
        import pytesseract
        for exception,message in [(pytesseract.TesseractNotFoundError(),'Tesseract is missing'),(RuntimeError('timeout'),'OCR timed out')]:
            with self.subTest(message=message),patch('pytesseract.image_to_string',side_effect=exception):
                with self.assertRaisesRegex(DocumentExtractionError,message):
                    extract_pages(SimpleUploadedFile('scan.pdf',self.scan_pdf()))

# Migration tests use their own transaction boundaries so SQLite can change schema.
from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class MigrationPreservationTests(TransactionTestCase):
    def test_existing_documents_keep_extracted_text_and_ready_status(self):
        target=('learning','0002_document_ocr_page_count_document_pages_processed_and_more')
        try:
            executor=MigrationExecutor(connection)
            executor.migrate([('learning','0001_initial')])
            old_apps=executor.loader.project_state([('learning','0001_initial')]).apps
            user=old_apps.get_model('auth','User').objects.create(username='legacy')
            OldDocument=old_apps.get_model('learning','Document')
            ready=OldDocument.objects.create(owner_id=user.pk,title='Existing notes',file='existing.txt',pages=[{'page':1,'text':'Existing stability notes'}])
            empty=OldDocument.objects.create(owner_id=user.pk,title='Empty notes',file='empty.pdf',pages=[])
            executor=MigrationExecutor(connection);executor.migrate([target])
            doc=Document.objects.get(pk=ready.pk)
            self.assertEqual(doc.status,Document.Status.READY)
            self.assertEqual(doc.total_pages,1)
            self.assertEqual(doc.pages[0]['text'],'Existing stability notes')
            self.assertEqual(Document.objects.get(pk=empty.pk).status,Document.Status.FAILED)
        finally:
            MigrationExecutor(connection).migrate([target])
