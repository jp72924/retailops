"""
Order writes that no service owns.

Most changes to a channel's order go through a service that publishes its events at the end (a
payment applied or reviewed, a cancellation or refund, a key supplied or revoked). Marking an
order shipped or delivered is different: the back-office views and the staff API set
`order.status` and save, in five places. A `post_save` hook covers all of them, and only for
the two statuses nothing else handles, so a service that is half way through a change never
publishes a state it has not finished writing.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import SalesOrder


@receiver(post_save, sender=SalesOrder, dispatch_uid='channel_events_on_order_shipped_or_delivered')
def publish_when_shipped_or_delivered(sender, instance, **kwargs):
    if instance.status not in (SalesOrder.SHIPPED, SalesOrder.DELIVERED):
        return
    from core.services.channel_events import publish_order_changes
    publish_order_changes(instance.pk)
