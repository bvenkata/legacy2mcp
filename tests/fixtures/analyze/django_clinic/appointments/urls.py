from django.urls import path, re_path

from appointments import views

urlpatterns = [
    path("appointments/", views.AppointmentListView.as_view()),
    path("appointments/<int:pk>/", views.appointment_detail),
    path("appointments/<int:pk>/pay/", views.pay_deposit),
    re_path(r"^appointments/(?P<pk>\d+)/cancel/$", views.cancel_appointment),
]
