from django.db import models


class Appointment(models.Model):
    patient_email = models.EmailField()
    starts_at = models.DateTimeField()
    paid = models.BooleanField(default=False)
