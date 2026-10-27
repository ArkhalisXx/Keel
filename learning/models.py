from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid

def document_path(instance, filename):
    return f'{instance.owner_id}/{uuid.uuid4().hex}{__import__("pathlib").Path(filename).suffix.lower()}'

class Document(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        PROCESSING = 'processing', 'Processing'
        READY = 'ready', 'Ready'
        FAILED = 'failed', 'Failed'

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    processing_token = models.UUIDField(default=uuid.uuid4, editable=False)
    processing_deadline = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    pages_processed = models.PositiveIntegerField(default=0)
    total_pages = models.PositiveIntegerField(default=0)
    ocr_page_count = models.PositiveIntegerField(default=0)

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=document_path)
    pages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def can_retry(self):
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        return (self.status == self.Status.FAILED
                or (self.status == self.Status.QUEUED and self.queued_at < now - timedelta(minutes=2))
                or (self.status == self.Status.PROCESSING and self.processing_deadline is not None and self.processing_deadline < now))

    def __str__(self): return self.title

class ExamPaper(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=120)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'document'], name='one_import_per_owner_document')]
        ordering = ['-created_at', '-pk']

    def __str__(self): return self.title


class Question(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                              help_text='Blank means a shared question; imported papers always belong to their uploader.')
    paper = models.ForeignKey(ExamPaper, null=True, blank=True, on_delete=models.CASCADE, related_name='questions')
    source_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL)
    source_number = models.CharField(max_length=20, blank=True)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    topic = models.CharField(max_length=120)
    prompt = models.TextField()
    choices = models.JSONField(help_text='Exactly four strings, e.g. ["A", "B", "C", "D"]')
    correct_index = models.PositiveSmallIntegerField(choices=[(i, str(i+1)) for i in range(4)], null=True, blank=True)
    explanation = models.TextField()
    reference = models.CharField(max_length=300, blank=True)
    reviewed = models.BooleanField(default=False)
    def clean(self):
        from django.core.exceptions import ValidationError
        if not isinstance(self.choices, list) or len(self.choices) != 4 or not all(isinstance(x,str) and x.strip() for x in self.choices):
            raise ValidationError({'choices': 'Provide exactly four nonempty text choices.'})
        errors = {}
        if self.reviewed and self.correct_index is None:
            errors['correct_index'] = 'Choose the correct answer before publishing.'
        if self.reviewed and not self.explanation.strip():
            errors['explanation'] = 'Add a verified explanation before publishing.'
        if self.paper_id and self.owner_id != self.paper.owner_id:
            errors['owner'] = 'Imported questions must belong to the paper owner.'
        if self.source_document_id and self.owner_id != self.source_document.owner_id:
            errors['source_document'] = 'The source document must belong to the question owner.'
        if errors:
            raise ValidationError(errors)
    def __str__(self): return self.prompt[:80]

class Attempt(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_index = models.PositiveSmallIntegerField()
    correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
