"""
Tying a storefront's customer to a RetailOps customer.

The store authenticates its own customers and names each with an `external_ref`
(its own user id). RetailOps stores no password for them and believes only the
channel's key. Three rules keep that from becoming a way into someone else's
record:

* A link, once made, decides everything. The same `external_ref` is always the
  same customer, whatever email the store sends later.
* Without a link, the only thing a customer is ever matched on is a verified
  email address, and only when the channel is set to trust that claim. A
  national id is never used to link: it is not something the store can have
  proved.
* A Customer is never overwritten. A link records what the channel sent, for
  support, and the Customer row stays as RetailOps knew it. Nothing stored
  about a customer is ever returned to the channel.
"""
from dataclasses import asdict, dataclass

from django.db import IntegrityError, transaction

from core.models import ChannelCustomerLink, Customer


class CustomerEmailConflict(Exception):
    """The email already belongs to a customer the channel is not trusted to claim."""
    code = 'customer_email_conflict'

    def __init__(self):
        super().__init__('A customer with this email address already exists.')


@dataclass(frozen=True)
class CustomerProfile:
    external_ref: str
    email: str
    first_name: str
    last_name: str
    email_verified: bool = False
    phone: str = ''

    def as_json(self):
        return asdict(self)


def link_customer(channel, profile):
    """
    Return the ChannelCustomerLink for this channel's customer, creating the
    customer and the link when there is neither.

    Raises CustomerEmailConflict when the email belongs to an existing customer
    and the channel cannot vouch for it. Call it inside the order's
    transaction, so a failed order leaves no customer behind.
    """
    email = profile.email.strip().lower()
    trusted = channel.trusts_email_verification and profile.email_verified

    # Two requests for a brand-new customer can race to create the same row;
    # the loser's savepoint rolls back and one more pass finds the winner's.
    for attempt in (1, 2):
        link = (
            ChannelCustomerLink.objects
            .filter(channel=channel, external_ref=profile.external_ref)
            .first()
        )
        if link is not None:
            link.profile = profile.as_json()
            link.save(update_fields=['profile', 'updated_at'])
            return link

        customer = Customer.objects.filter(email__iexact=email).first()
        if customer is not None and not trusted:
            raise CustomerEmailConflict()

        try:
            with transaction.atomic():
                if customer is None:
                    customer = Customer.objects.create(
                        first_name=profile.first_name.strip(),
                        last_name=profile.last_name.strip(),
                        email=email,
                        phone=profile.phone.strip(),
                    )
                return ChannelCustomerLink.objects.create(
                    channel=channel,
                    external_ref=profile.external_ref,
                    customer=customer,
                    profile=profile.as_json(),
                )
        except IntegrityError:
            if attempt == 2:
                raise
