from django.contrib import admin

from .models import TransitionModel, TransitionEventModel


admin.site.register(TransitionEventModel)


@admin.register(TransitionModel)
class TransitionModelAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'type', 'resource_fullname', 'status']
