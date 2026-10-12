"""Which café the current request or message belongs to.

Everything a café owns (menu, chats, orders, bookings, ...) is a db.Scoped table, and every query on one is filtered to
the café in scope here; with no café in scope it raises instead. So a forgotten filter can't show one café's orders to
another. Dashboard requests get their scope from the login session, webhook messages from the phone number or page they
were sent to, and background jobs set it per café.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_shop = ContextVar("shop", default=None)
_staff = ContextVar("staff", default=None)


@contextmanager
def scope(shop, staff: str | None = None):
    """shop: a db.Shop row. staff: the signed-in person's name, for dashboard requests."""
    tokens = _shop.set(shop), _staff.set(staff)
    try:
        yield shop
    finally:
        _shop.reset(tokens[0])
        _staff.reset(tokens[1])


def shop():
    s = _shop.get()
    if s is None:
        raise RuntimeError("No café in scope")
    return s


def shop_id() -> str:
    return shop().id


def cfg() -> dict:
    """The café's settings: everything from its business.yaml except the menu."""
    return shop().config


def staff() -> str | None:
    return _staff.get()
