import braintree
from django.core.mail import send_mail
from django.http import JsonResponse
from django.views import View
from django.views.decorators.http import require_POST

from appointments.models import Appointment


class AppointmentListView(View):
    """List or book appointments."""

    def get(self, request):
        return JsonResponse({"items": list(Appointment.objects.filter(paid=True).values())})

    def post(self, request):
        appt = Appointment.objects.create(patient_email=request.POST["email"], starts_at=request.POST["at"])
        return JsonResponse({"id": appt.pk})


def appointment_detail(request, pk):
    appt = Appointment.objects.get(pk=pk)
    if request.method == "PUT":
        appt.starts_at = request.POST["at"]
        appt.save()
    return JsonResponse({"id": appt.pk})


@require_POST
def pay_deposit(request, pk):
    """Take a card deposit for an appointment."""
    appt = Appointment.objects.get(pk=pk)
    braintree.Transaction.sale({"amount": "25.00", "payment_method_nonce": request.POST["nonce"]})
    Appointment.objects.filter(pk=pk).update(paid=True)
    send_mail("Deposit received", "See you soon.", "clinic@example.com", [appt.patient_email])
    return JsonResponse({"paid": True})


@require_POST
def cancel_appointment(request, pk):
    Appointment.objects.filter(pk=pk).delete()
    return JsonResponse({"cancelled": True})
