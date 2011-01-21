"""
A service's status should be:

- red if any(updates affecting service) are red
- yellow if any(updates affecting service) are yellow
- green if all(updates affecting service) are green
"""

from django.db import models
from django.db.models.signals import post_save, m2m_changed

import datetime

STATUS_CHOICES = (
    (0, 'No Problems'),
    (1, 'Some Issues'),
    (2, 'Unavailable'),
)

class Service(models.Model):
    """
    A ``Service`` can describe any part of your architecture. Each 
    service can have many events, in which the last event should be shown
    (unless the status is 'No Problems').
    """
    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128, unique=True)
    description = models.TextField(blank=True, null=True)
    status = models.SmallIntegerField(choices=STATUS_CHOICES, editable=False, default=0)
    order = models.IntegerField(default=0)
    date_created = models.DateTimeField(default=datetime.datetime.now)
    date_updated = models.DateTimeField(default=datetime.datetime.now)
    
    class Meta:
        ordering = ('order', 'name')

    def __unicode__(self):
        return self.name

    @models.permalink
    def get_absolute_url(self):
        return ('overseer:service', [self.slug], {})

    @classmethod
    def handle_event_m2m_save(cls, sender, instance, action, reverse, model, pk_set, **kwargs):
        if not action.startswith('post_'):
            return
        if not pk_set:
            return
        
        if model is Service:
            for service in Service.objects.filter(pk__in=pk_set):
                service.update_from_event(instance)
        else:
            for event in Event.objects.filter(pk__in=pk_set):
                instance.update_from_event(event)

    @classmethod
    def handle_event_save(cls, instance, **kwargs):
        for service in instance.services.all():
            service.update_from_event(instance)

    def update_from_event(self, event):
        update_qs = Service.objects.filter(pk=self.pk)
        if event.date_updated > self.date_updated:
            # If the update is newer than the last update to the self
            update_qs.filter(date_updated__lt=event.date_updated)\
                     .update(date_updated=event.date_updated)
            self.date_updated = event.date_updated

        if event.status > self.status:
            # If our status more critical (higher) than the current
            # self status, update to match the current
            update_qs.filter(status__lt=event.status)\
                     .update(status=event.status)
            self.status = event.status

        elif event.status < self.status:
            # If no more events match the current self status, let's update
            # it to the current status
            if not Event.objects.filter(services=self, status=self.status)\
                                .exclude(pk=event.pk).exists():
                update_qs.filter(status__gt=event.status)\
                         .update(status=event.status)
                self.status = event.status

class EventBase(models.Model):
    class Meta:
        abstract = True

    def get_message(self):
        if self.message:
            return self.message
        elif self.status == 0:
            return 'Service is operating as expected.'
        elif self.status == 1:
            return 'Experiencing some issues. Services mostly operational.'
        elif self.status == 2:
            return 'Service is unavailable.'
        return ''

class Event(EventBase):
    """
    An ``Event`` is a collection of updates related to one event.
    
    - ``message`` stores the last message from ``StatusUpdate`` for this event.
    """
    services = models.ManyToManyField(Service)
    status = models.SmallIntegerField(choices=STATUS_CHOICES, editable=False, default=0)
    peak_status = models.SmallIntegerField(choices=STATUS_CHOICES, editable=False, default=0)
    description = models.TextField(null=True, blank=True, help_text='We will auto fill the description from the first event message if not set')
    message = models.TextField(null=True, blank=True, editable=False)
    date_created = models.DateTimeField(default=datetime.datetime.now, editable=False)
    date_updated = models.DateTimeField(default=datetime.datetime.now, editable=False)

    def __unicode__(self):
        return u"%s on %s" % (self.date_created, '; '.join(self.services.values_list('name', flat=True)))

    @models.permalink
    def get_absolute_url(self):
        return ('overseer:event', [self.pk], {})

    def get_services(self):
        return self.services.values_list('slug', 'name')

    def get_duration(self):
        return self.date_updated - self.date_created

    @classmethod
    def handle_update_save(cls, instance, created, **kwargs):
        event = instance.event

        if created:
            is_latest = True
        elif EventUpdate.objects.filter(event=event).order_by('-date_created')\
                                .values_list('event', flat=True)[0] == event.pk:
            is_latest = True
        else:
            is_latest = False

        if is_latest:
            update_kwargs = dict(
                status=instance.status,
                date_updated=instance.date_created,
                message=instance.message
            )

            if not event.description:
                update_kwargs['description'] = instance.message
                
            if not event.peak_status or event.peak_status < instance.status:
                update_kwargs['peak_status'] = instance.status

            Event.objects.filter(pk=event.pk).update(**update_kwargs)

            for k, v in update_kwargs.iteritems():
                setattr(event, k, v)

            # Without sending the signal Service will fail to update
            post_save.send(sender=Event, instance=event, created=False)

class EventUpdate(EventBase):
    """
    An ``EventUpdate`` contains a single update to an ``Event``. The latest update
    will always be reflected within the event, carrying over it's ``status`` and ``message``.
    """
    event = models.ForeignKey(Event)
    status = models.SmallIntegerField(choices=STATUS_CHOICES)
    message = models.TextField(null=True, blank=True)
    date_created = models.DateTimeField(default=datetime.datetime.now, editable=False)

    def __unicode__(self):
        return unicode(self.date_created)

post_save.connect(Service.handle_event_save, sender=Event)
post_save.connect(Event.handle_update_save, sender=EventUpdate)
m2m_changed.connect(Service.handle_event_m2m_save, sender=Event.services.through)
