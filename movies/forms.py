from django import forms
from django.core.exceptions import ValidationError

from .models import Genre, Language, Movie, ShowTime, Theater, Screen


class MovieAvailabilityForm(forms.ModelForm):
    """Movie metadata + availability window for the custom admin scheduler."""

    duration_hours = forms.IntegerField(
        min_value=0, max_value=12, initial=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 12}),
        help_text='Hours portion of runtime',
    )
    duration_mins = forms.IntegerField(
        min_value=0, max_value=59, initial=0,
        label='Duration minutes',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 59}),
        help_text='Minutes portion of runtime',
    )
    genres = forms.ModelMultipleChoiceField(
        queryset=Genre.objects.all().order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Genres',
    )

    class Meta:
        model = Movie
        fields = [
            'name',
            'image',
            'description',
            'duration_minutes',
            'release_date',
            'end_date',
            'default_cleaning_buffer_minutes',
            'language',
            'age_certification',
            'trailer_url',
            'genres',
            'rating',
        ]
        widgets = {
            'release_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3,
                                                  'placeholder': 'Brief synopsis or description…'}),
            'default_cleaning_buffer_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'trailer_url': forms.URLInput(attrs={'class': 'form-control',
                                                  'placeholder': 'https://www.youtube.com/watch?v=…'}),
            'rating': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1',
                                               'min': '0', 'max': '10'}),
        }
        labels = {
            'release_date': 'Available from',
            'end_date': 'Available until',
            'default_cleaning_buffer_minutes': 'Buffer / cleaning time (minutes)',
            'rating': 'Critic / base rating (out of 10, optional)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].required = not self.instance.pk
        # Pre-fill duration split fields from the stored minutes value
        if self.instance.pk and self.instance.duration_minutes:
            hours, mins = divmod(self.instance.duration_minutes, 60)
            self.fields['duration_hours'].initial = hours
            self.fields['duration_mins'].initial = mins
        # Pre-fill genres from existing instance
        if self.instance.pk:
            self.fields['genres'].initial = self.instance.genres.all()
        # Apply Bootstrap class to select widgets
        for name in ('language', 'age_certification'):
            if name in self.fields:
                self.fields[name].widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        hours = cleaned.get('duration_hours')
        mins  = cleaned.get('duration_mins')
        if hours is not None and mins is not None:
            total = hours * 60 + mins
            if total <= 0:
                raise ValidationError('Movie duration must be greater than zero.')
            cleaned['duration_minutes'] = total
        release = cleaned.get('release_date')
        end     = cleaned.get('end_date')
        if release and end and end < release:
            raise ValidationError({'end_date': 'Available until must be on or after available from.'})
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get('duration_minutes') is not None:
            instance.duration_minutes = self.cleaned_data['duration_minutes']
        if commit:
            instance.save()
            self.save_m2m()   # saves genres M2M
        return instance


class AdminShowTimeForm(forms.ModelForm):
    class Meta:
        model = ShowTime
        fields = ['movie', 'theater', 'screen', 'date', 'start_time', 'cleaning_buffer_minutes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, movie=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.movie = movie
        if movie:
            self.fields['movie'].initial = movie.pk
            self.fields['movie'].widget = forms.HiddenInput()
        self.fields['theater'].queryset = Theater.objects.all().order_by('name')
        self.fields['screen'].queryset = Screen.objects.select_related('theater').order_by('theater__name', 'name')

    def clean(self):
        cleaned = super().clean()
        screen  = cleaned.get('screen')
        theater = cleaned.get('theater')
        if screen and theater and screen.theater_id != theater.id:
            raise ValidationError({'screen': 'Screen must belong to the selected theater.'})
        return cleaned
