from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.utils import timezone
from .models import Author, Book, Copy, Genre, Loan, Notification, Reservation, Review, User

admin.site.site_header = 'ShelfS — керування каталогом'
admin.site.site_title = 'ShelfS'
admin.site.index_title = 'Книжковий фонд та записи'


class StaffUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'first_name', 'last_name')


class StaffUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'


@admin.register(User)
class ShelfUserAdmin(UserAdmin):
    add_form = StaffUserCreationForm
    form = StaffUserChangeForm
    ordering = ['email']
    list_display = ['email', 'first_name', 'last_name', 'is_staff', 'email_verified', 'borrowing_blocked']
    search_fields = ['email', 'first_name', 'last_name']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Ім’я', {'fields': ('first_name', 'last_name', 'patronymic')}),
        ('Стан', {'fields': ('email_verified', 'borrowing_blocked', 'block_reason')}),
        ('Права', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = ((None, {'fields': ('email', 'first_name', 'last_name', 'password1', 'password2')}),)
    # Staff operations use /staff/. Only a superuser can change account privileges.
    def has_add_permission(self, request):
        return request.user.is_superuser
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser
    def has_delete_permission(self, request, obj=None):
        return False
    def get_readonly_fields(self, request, obj=None):
        fields = ['borrowing_blocked', 'block_reason']
        if obj:
            fields.append('email')
        return fields


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    search_fields = ['title', 'authors__name']
    filter_horizontal = ['authors', 'genres']
    list_display = ['title']


@admin.register(Copy)
class CopyAdmin(admin.ModelAdmin):
    list_display = ['inventory_number', 'book', 'is_active']
    search_fields = ['inventory_number', 'book__title']
    list_filter = ['is_active']
    def get_readonly_fields(self, request, obj=None):
        # A physical copy cannot become another book while keeping old history.
        return ['book'] if obj else []


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Reservation)
class ReservationAdmin(ReadOnlyAdmin):
    list_display = ['id', 'reader', 'copy', 'status', 'expires_at']
    list_filter = ['status']
    search_fields = ['reader__email', 'reader__last_name', 'copy__inventory_number', 'copy__book__title']
    list_select_related = ['reader', 'copy__book']


class LoanStateFilter(admin.SimpleListFilter):
    title = 'Стан видачі'
    parameter_name = 'loan_state'
    def lookups(self, request, model_admin):
        return [('open', 'На руках'), ('overdue', 'Прострочено'), ('returned', 'Повернуто')]
    def queryset(self, request, queryset):
        if self.value() == 'overdue':
            return queryset.filter(returned_at__isnull=True, due_date__lt=timezone.localdate())
        if self.value() == 'open':
            return queryset.filter(returned_at__isnull=True)
        if self.value() == 'returned':
            return queryset.filter(returned_at__isnull=False)
        return queryset


@admin.register(Loan)
class LoanAdmin(ReadOnlyAdmin):
    list_display = ['id', 'reader', 'copy', 'due_date', 'returned_at']
    list_filter = [LoanStateFilter]
    search_fields = ['reader__email', 'reader__last_name', 'copy__inventory_number', 'copy__book__title']
    list_select_related = ['reader', 'copy__book']


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ['id', 'recipient', 'kind', 'scheduled_at', 'state', 'attempts', 'last_error']
    list_filter = ['state', 'kind']
    search_fields = ['recipient', 'loan__copy__book__title']


admin.site.register(Author)
admin.site.register(Genre)
admin.site.register(Review, ReadOnlyAdmin)
