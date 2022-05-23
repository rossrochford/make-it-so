from django.db import models
from django.utils import timezone


class BaseModel(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created", "id"]
        get_latest_by = ["created"]

    @property
    def age_seconds(self):
        return (timezone.now() - self.created).total_seconds()

    def update_fields(self, set_nones=False, **kwargs):
        if not kwargs:
            return False

        modified = False
        for fn, val in kwargs.items():
            if set_nones and val is None:
                continue
            if getattr(self, fn) == val:
                continue
            setattr(self, fn, val)
            modified = True

        if modified:
            self.save()
        return modified
