from django.core.management.base import BaseCommand

from appointments.models import Appointment


class Command(BaseCommand):
    help = "Delete appointments older than a year."

    def handle(self, *args, **options):
        Appointment.objects.filter(paid=False).delete()
