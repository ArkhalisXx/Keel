from django.contrib import admin
from .models import Question
@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['prompt','topic','reviewed']
    list_filter = ['topic','reviewed']
    search_fields = ['prompt','topic']
