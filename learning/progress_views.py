"""Date-scoped practice analytics, always limited to the signed-in account."""
import calendar
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from .models import Attempt


class ProgressFilterForm(forms.Form):
    period = forms.ChoiceField(choices=[('week', 'Week'), ('month', 'Month'), ('year', 'Year'), ('range', 'Custom dates')])
    date = forms.DateField(required=False, label='A day in the week', widget=forms.DateInput(attrs={'type': 'date'}))
    month = forms.CharField(required=False, widget=forms.TextInput(attrs={'type': 'month'}))
    year = forms.IntegerField(required=False, min_value=1900, max_value=9998)
    start = forms.DateField(required=False, label='From', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Through', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean(self):
        values = super().clean()
        today = timezone.localdate()
        period = values.get('period')
        if period == 'week':
            anchor = values.get('date') or today
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6) if start.year < 9999 else start
        elif period == 'month':
            try:
                value = values.get('month') or today.strftime('%Y-%m')
                anchor = date.fromisoformat(value + '-01')
                start, end = anchor, anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
            except (ValueError, TypeError):
                self.add_error('month', 'Choose a valid month.')
                return values
        elif period == 'year':
            year = values.get('year') or today.year
            start, end = date(year, 1, 1), date(year, 12, 31)
        elif period == 'range':
            start = values.get('start') or today - timedelta(days=29)
            end = values.get('end') or today
        else:
            return values
        if not 1900 <= start.year <= end.year <= 9998:
            self.add_error(None, 'Choose dates between 1900 and 9998.')
        elif start > end:
            self.add_error('end', 'The end date must be on or after the start date.')
        elif (end - start).days > 3659:
            self.add_error('end', 'Choose a custom range of up to 10 years (3,660 days).')
        else:
            values.update(start_date=start, end_date=end)
        return values


def _month_after(value):
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


def _bins(start, end, period):
    """At most 121 chart columns even for the longest supported custom range."""
    days = (end - start).days + 1
    kind = 'month' if period == 'year' or days > 180 else 'week' if days > 31 else 'day'
    cursor = start
    while cursor <= end:
        stop = min(end, (_month_after(cursor.replace(day=1)) - timedelta(days=1)) if kind == 'month'
                   else cursor + timedelta(days=6) if kind == 'week' else cursor)
        label = cursor.strftime('%b') if kind == 'month' and period == 'year' else cursor.strftime('%b %Y') if kind == 'month' else cursor.strftime('%d %b')
        yield {'start': cursor, 'end': stop, 'label': label, 'total': 0, 'correct': 0}
        cursor = stop + timedelta(days=1)


def _navigation(start, end, period):
    links = []
    for direction, offset in [('previous', -1), ('next', 1)]:
        try:
            if period == 'week':
                args = {'date': (start + timedelta(days=7 * offset)).isoformat()}
            elif period == 'month':
                anchor = start - timedelta(days=1) if offset < 0 else _month_after(start)
                args = {'month': anchor.strftime('%Y-%m')}
            elif period == 'year':
                args = {'year': start.year + offset}
            else:
                delta = timedelta(days=((end - start).days + 1) * offset)
                args = {'start': (start + delta).isoformat(), 'end': (end + delta).isoformat()}
            candidates = [str(value)[:4] for value in args.values()]
            valid = all(1900 <= int(value) <= 9998 for value in candidates)
            query = urlencode({'period': period, **args}) if valid else ''
        except (ValueError, OverflowError):
            query = ''
        links.append((direction, query))
    return dict(links)


@login_required
def progress(request):
    today = timezone.localdate()
    data = request.GET.copy()
    if not data.get('period'):
        data['period'] = 'month'
    for key, value in {'date': today.isoformat(), 'month': today.strftime('%Y-%m'), 'year': today.year,
                       'start': (today - timedelta(days=29)).isoformat(), 'end': today.isoformat()}.items():
        data.setdefault(key, value)
    form = ProgressFilterForm(data)
    context = {'filter_form': form, 'period': data['period'], 'analytics_timezone': timezone.get_current_timezone_name(),
               'periods': [('week', 'Week'), ('month', 'Month'), ('year', 'Year'), ('range', 'Custom dates')]}
    if not form.is_valid():
        return render(request, 'learning/progress.html', context, status=400)
    selection = form.cleaned_data
    start, end = selection['start_date'], selection['end_date']
    tz = timezone.get_current_timezone()
    lower = timezone.make_aware(datetime.combine(start, time.min), tz)
    upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    attempts = Attempt.objects.filter(owner=request.user, created_at__gte=lower, created_at__lt=upper).filter(
        Q(question__owner=request.user) | Q(question__owner__isnull=True))
    summary = attempts.aggregate(total=Count('pk'), correct=Count('pk', filter=Q(correct=True)),
                                 questions=Count('question_id', distinct=True))
    days = list(attempts.annotate(day=TruncDate('created_at', tzinfo=tz)).values('day').annotate(
        total=Count('pk'), correct=Count('pk', filter=Q(correct=True))).order_by('day'))
    chart = list(_bins(start, end, selection['period']))
    index = 0
    for row in days:
        row.update(incorrect=row['total'] - row['correct'], accuracy=round(row['correct'] * 100 / row['total']))
        while index < len(chart) - 1 and row['day'] > chart[index]['end']:
            index += 1
        chart[index]['total'] += row['total']
        chart[index]['correct'] += row['correct']
    peak = max((item['total'] for item in chart), default=0) or 1
    for item in chart:
        item.update(height=round(item['total'] * 100 / peak, 2),
                    correct_percent=round(item['correct'] * 100 / item['total'], 2) if item['total'] else 0,
                    query=urlencode({'period': 'range', 'start': item['start'].isoformat(), 'end': item['end'].isoformat()}))
    topics = list(attempts.values('question__topic').annotate(total=Count('pk'), correct=Count('pk', filter=Q(correct=True))).order_by('-total', 'question__topic')[:12])
    for topic in topics:
        topic['accuracy'] = round(topic['correct'] * 100 / topic['total'])
    context.update(
        start_date=start, end_date=end, summary=summary, chart=chart, topics=topics,
        accuracy=round(summary['correct'] * 100 / summary['total']) if summary['total'] else None,
        active_days=len(days), date_rows=Paginator(list(reversed(days)), 20).get_page(request.GET.get('page')),
        navigation=_navigation(start, end, selection['period']),
        filter_defaults={'date': selection.get('date') or today, 'month': selection.get('month') or today.strftime('%Y-%m'),
                         'year': selection.get('year') or today.year, 'start': start, 'end': end},
    )
    return render(request, 'learning/progress.html', context)
