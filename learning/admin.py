from django.contrib import admin
from .models import Question
@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['prompt','topic','reviewed','owner']
    list_filter = ['topic','reviewed','owner']
    search_fields = ['prompt','topic']
