from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('books/<int:pk>/', views.book_detail, name='book'),
    path('books/<int:pk>/reserve/', views.reserve_book, name='reserve'),
    path('books/<int:pk>/review/', views.add_review, name='review'),
    path('signup/', views.signup, name='signup'),
    path('login/', views.EmailLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('confirm/<str:token>/', views.confirm_email, name='confirm-email'),
    path('account/', views.account, name='account'),
    path('account/resend/', views.resend_confirmation, name='resend-confirmation'),
    path('reservations/<int:pk>/cancel/', views.cancel_reservation, name='cancel'),
    path('staff/', views.staff_dashboard, name='staff'),
    path('staff/loans/<int:pk>/return/', views.return_loan, name='return'),
]
