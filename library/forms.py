from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils import timezone
from .models import Copy, Review, User


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['last_name', 'first_name', 'patronymic', 'email']
        labels = {'last_name': 'Прізвище', 'first_name': 'Ім’я'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ('first_name', 'last_name', 'patronymic'):
            self.fields[field].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Цю адресу вже зареєстровано.')
        return email


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(label='Електронна пошта')
    def clean_username(self):
        return self.cleaned_data['username'].strip().lower()


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 4, 'maxlength': 2000})}
    def clean_text(self):
        text = self.cleaned_data['text'].strip()
        if not text:
            raise forms.ValidationError('Напишіть текст відгуку.')
        return text


class IssueForm(forms.Form):
    reader = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True, is_staff=False), label='Читач')
    copy = forms.ModelChoiceField(queryset=Copy.objects.filter(is_active=True).select_related('book'), label='Примірник')
    due_date = forms.DateField(label='Повернути до', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['due_date'].initial = timezone.localdate() + timedelta(days=settings.LOAN_DAYS)


class RestrictionForm(forms.Form):
    reader = forms.ModelChoiceField(queryset=User.objects.filter(is_staff=False), label='Читач')
    blocked = forms.BooleanField(label='Обмежити нові бронювання', required=False)
    reason = forms.CharField(label='Причина', max_length=300, required=False)
    def clean(self):
        data = super().clean()
        if data.get('blocked') and not data.get('reason', '').strip():
            self.add_error('reason', 'Укажіть причину обмеження.')
        return data
