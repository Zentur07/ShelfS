from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from . import services
from .forms import EmailLoginForm, IssueForm, RestrictionForm, ReviewForm, SignupForm
from .models import Book, Genre, Loan, Reservation, User


class EmailLoginView(LoginView):
    authentication_form = EmailLoginForm
    template_name = 'registration/login.html'


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        services.require_staff(request.user)
        return view(request, *args, **kwargs)
    return wrapped


def catalog(request):
    books = Book.objects.prefetch_related('authors', 'genres')
    query = request.GET.get('q', '').strip()[:200]
    genre = request.GET.get('genre', '')
    if query:
        books = books.filter(Q(title__icontains=query) | Q(authors__name__icontains=query))
    if genre.isdigit():
        books = books.filter(genres__pk=genre)
    free = services.available_copies().values('pk')
    if request.GET.get('available') == '1':
        books = books.filter(copies__in=free)
    books = books.distinct().annotate(available_count=Count('copies', filter=Q(copies__in=free), distinct=True)).order_by('title', 'pk')
    page = Paginator(books, 12).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'library/catalog.html', {'page': page, 'genres': Genre.objects.all(), 'query': query, 'genre': genre, 'params': params.urlencode()})


def book_detail(request, pk):
    book = get_object_or_404(Book.objects.prefetch_related('authors', 'genres'), pk=pk)
    return render(request, 'library/book.html', {
        'book': book, 'available_count': services.available_copies().filter(book=book).count(),
        'reviews': Paginator(book.reviews.select_related('reader').order_by('-created_at', '-pk'), 20).get_page(request.GET.get('reviews_page')), 'review_form': ReviewForm(),
    })


def signup(request):
    if request.user.is_authenticated:
        return redirect('account')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = form.save()
        except IntegrityError:
            form.add_error('email', 'Цю адресу вже зареєстровано.')
        else:
            if services.send_confirmation(user):
                messages.success(request, 'Акаунт створено. Перевірте пошту для підтвердження адреси, потім увійдіть.')
            else:
                messages.warning(request, 'Акаунт створено, але лист не вдалося надіслати. Увійдіть і повторіть запит у кабінеті.')
            return redirect('login')
    return render(request, 'registration/signup.html', {'form': form})


def confirm_email(request, token):
    # GET displays a confirmation page; only an explicit POST changes account state.
    if request.method == 'POST':
        try:
            services.confirm_email(token)
            messages.success(request, 'Пошту підтверджено. Тепер можна бронювати книги.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        return redirect('account' if request.user.is_authenticated else 'login')
    return render(request, 'registration/confirm.html')


@login_required
@require_POST
def resend_confirmation(request):
    try:
        if services.send_confirmation(request.user):
            messages.success(request, 'Лист підтвердження надіслано.')
        elif request.user.email_verified:
            messages.info(request, 'Пошту вже підтверджено.')
        else:
            messages.error(request, 'Не вдалося надіслати лист. Спробуйте пізніше.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('account')


@login_required
def account(request):
    loans = request.user.loans.select_related('copy__book').order_by('-issued_at', '-pk')
    loan_state = request.GET.get('loan_state', 'all')
    if loan_state == 'current':
        loans = loans.filter(returned_at__isnull=True)
    elif loan_state == 'history':
        loans = loans.filter(returned_at__isnull=False)
    else:
        loan_state = 'all'
    return render(request, 'library/account.html', {
        'reservations': Paginator(request.user.reservations.select_related('copy__book').order_by('-created_at', '-pk'), 20).get_page(request.GET.get('reservations_page')),
        'loans': Paginator(loans, 20).get_page(request.GET.get('loans_page')),
        'loan_state': loan_state,
    })


@login_required
@require_POST
def reserve_book(request, pk):
    get_object_or_404(Book, pk=pk)
    try:
        services.reserve(request.user, pk)
        messages.success(request, 'Бронювання збережено. Строк дії вказано у вашому кабінеті.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('account')


@login_required
def cancel_reservation(request, pk):
    record = get_object_or_404(Reservation.objects.select_related('copy__book'), pk=pk, reader=request.user)
    if request.method != 'POST' or request.POST.get('confirmed') != 'yes':
        return render(request, 'library/cancel.html', {'reservation': record}, status=400 if request.method == 'POST' else 200)
    services.cancel_reservation(request.user, pk)
    messages.success(request, 'Статус бронювання оновлено.')
    return redirect('account')


@login_required
@require_POST
def add_review(request, pk):
    book = get_object_or_404(Book, pk=pk)
    form = ReviewForm(request.POST)
    if form.is_valid():
        review = form.save(commit=False)
        review.reader, review.book = request.user, book
        review.save()
        messages.success(request, 'Відгук опубліковано.')
    else:
        messages.error(request, 'Відгук має містити від 1 до 2000 символів.')
    return redirect('book', pk=pk)


@staff_required
def staff_dashboard(request):
    issue_form = IssueForm(auto_id='issue_%s')
    restriction_form = RestrictionForm(auto_id='restriction_%s')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'issue':
            issue_form = IssueForm(request.POST, auto_id='issue_%s')
            if issue_form.is_valid():
                try:
                    data = issue_form.cleaned_data
                    services.issue_loan(request.user, data['reader'].pk, data['copy'].pk, data['due_date'])
                    messages.success(request, 'Видачу оформлено.')
                    return redirect('staff')
                except ValidationError as exc:
                    issue_form.add_error(None, exc)
        elif action == 'restrict':
            restriction_form = RestrictionForm(request.POST, auto_id='restriction_%s')
            if restriction_form.is_valid():
                data = restriction_form.cleaned_data
                services.set_restriction(request.user, data['reader'].pk, data['blocked'], data['reason'])
                messages.success(request, 'Обмеження оновлено.')
                return redirect('staff')
    return render(request, 'library/staff.html', {
        'issue_form': issue_form, 'restriction_form': restriction_form,
        'loans': Loan.objects.filter(returned_at__isnull=True).select_related('reader', 'copy__book'),
        'reservations': Reservation.objects.filter(status='active').select_related('reader', 'copy__book'),
        'blocked_readers': User.objects.filter(borrowing_blocked=True),
    })


@staff_required
@require_POST
def return_loan(request, pk):
    get_object_or_404(Loan, pk=pk)
    services.return_loan(request.user, pk)
    messages.success(request, 'Повернення зареєстровано.')
    return redirect('staff')
