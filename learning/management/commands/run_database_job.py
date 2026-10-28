"""Internal entry point: execute one token-guarded job in an isolated process."""
from uuid import UUID
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('kind', choices=['document', 'reviewer'])
        parser.add_argument('pk', type=int)
        parser.add_argument('token', type=UUID)

    def handle(self, kind, pk, token, **options):
        if settings.JOB_BACKEND != 'database':
            raise CommandError('This command requires JOB_BACKEND=database.')
        from learning.tasks import process_document
        from learning.reviewers import generate_reviewer
        task = process_document if kind == 'document' else generate_reviewer
        task.run(pk, str(token))
