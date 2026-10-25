from django.conf import settings
from django.db import models
import uuid

def document_path(instance, filename):
    return f'{instance.owner_id}/{uuid.uuid4().hex}{__import__("pathlib").Path(filename).suffix.lower()}'

class Document(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=document_path)
    pages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title

class Question(models.Model):
    topic = models.CharField(max_length=120)
    prompt = models.TextField()
    choices = models.JSONField(help_text='Exactly four strings, e.g. ["A", "B", "C", "D"]')
    correct_index = models.PositiveSmallIntegerField(choices=[(i, str(i+1)) for i in range(4)])
    explanation = models.TextField()
    reference = models.CharField(max_length=300, blank=True)
    reviewed = models.BooleanField(default=False)
    def clean(self):
        from django.core.exceptions import ValidationError
        if not isinstance(self.choices, list) or len(self.choices) != 4 or not all(isinstance(x,str) and x.strip() for x in self.choices):
            raise ValidationError({'choices': 'Provide exactly four nonempty text choices.'})
    def __str__(self): return self.prompt[:80]

class Attempt(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_index = models.PositiveSmallIntegerField()
    correct = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
