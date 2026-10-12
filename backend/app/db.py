from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, inspect,
                        text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, with_loader_criteria

from . import options, tenancy
from .config import DATABASE_URL, cents

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)
Session = sessionmaker(engine, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class Base(DeclarativeBase):
    pass


# --- the platform: cafés, their staff, logins ------------------------------------------------------

class Shop(Base):
    """One café. config is its business.yaml without the menu; the channel ids route incoming webhooks to it."""
    __tablename__ = "shops"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # short slug staff type to sign in, e.g. little-fern
    config: Mapped[dict] = mapped_column(JSON)
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    whatsapp_token: Mapped[str | None] = mapped_column(Text)  # ponytail: plain in the DB; encrypt at rest if the DB leaves the server
    page_id: Mapped[str | None] = mapped_column(String(40), unique=True)  # Facebook Page (Messenger)
    instagram_id: Mapped[str | None] = mapped_column(String(40), unique=True)  # Instagram account linked to that Page
    page_token: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("shop_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(String(200))  # see auth.py


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


# --- what each café owns -----------------------------------------------------------------------------

class Scoped:
    """A row that belongs to one café. New rows get the café in scope; every query is filtered to it (below)."""
    shop_id: Mapped[str] = mapped_column(String(40), index=True, default=lambda: tenancy.shop_id())


@event.listens_for(Session, "do_orm_execute")
def _only_this_cafe(state):
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if not any(issubclass(m.class_, Scoped) for m in state.all_mappers):
        return
    shop = tenancy.shop_id()  # raises when no café is in scope: never an unfiltered query
    state.statement = state.statement.options(with_loader_criteria(Scoped, lambda cls: cls.shop_id == shop, include_aliases=True))


class MenuItem(Scoped, Base):
    __tablename__ = "menu_items"
    __table_args__ = (UniqueConstraint("shop_id", "id"),)
    pk: Mapped[int] = mapped_column(primary_key=True)
    id: Mapped[str] = mapped_column(String(20))  # the café's own code, e.g. M01
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(Integer)  # cents
    options: Mapped[list] = mapped_column(JSON, default=list)  # see options.py
    dietary: Mapped[list] = mapped_column(JSON, default=list)  # vegan, gluten-free, ...: the assistant only repeats these
    allergens: Mapped[list] = mapped_column(JSON, default=list)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # other names customers use; helps search
    available: Mapped[bool] = mapped_column(Boolean, default=True)  # False = sold out
    stock: Mapped[int | None] = mapped_column(Integer)  # how many are left; None = not counted (a switch is enough)
    minutes: Mapped[int | None] = mapped_column(Integer)  # set = a service booked as an appointment, not ordered


def new_item(data: dict) -> MenuItem:
    """A menu item from business.yaml-style fields (price as an amount, options as lines)."""
    return MenuItem(id=data["id"], name=data["name"], category=str(data["category"]).lower(), description=data.get("description", ""),
                    price=cents(data["price"]), options=options.parse(data.get("options", [])), dietary=data.get("dietary", []),
                    allergens=data.get("allergens", []), aliases=data.get("aliases", []), available=data.get("available", True),
                    stock=data.get("stock"), minutes=data.get("minutes"))


class Conversation(Scoped, Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # "<café>:<phone>", "<café>:messenger-<id>", "<café>:sim-<phone>", ...
    channel: Mapped[str] = mapped_column(String(20))  # whatsapp | messenger | instagram | sim
    phone: Mapped[str] = mapped_column(String(30))
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    pending_order: Mapped[dict | None] = mapped_column(JSON)  # last quote shown to the customer
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class Message(Scoped, Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | human (staff via dashboard)
    text: Mapped[str] = mapped_column(Text, default="")
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_path: Mapped[str | None] = mapped_column(String(200))  # file inside MEDIA_DIR: a voice note, a photo, or a menu photo
    author: Mapped[str | None] = mapped_column(String(50))  # which staff member sent a "human" reply
    created_at: Mapped[datetime] = mapped_column(default=now)


class SeenWhatsAppMessage(Base):
    """Inbox of WhatsApp, Messenger and Instagram messages. The primary key makes Meta's retries fail to insert;
    done=False means a crash or restart cut processing off, so it's re-run at startup."""
    __tablename__ = "seen_wa_messages"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    done: Mapped[bool | None] = mapped_column(Boolean)
    channel: Mapped[str | None] = mapped_column(String(20))
    shop_id: Mapped[str | None] = mapped_column(String(40))  # which café it was sent to


class Order(Scoped, Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    fulfilment: Mapped[str] = mapped_column(String(20))  # a key of the café's fulfilment settings: pickup | delivery
    address: Mapped[str | None] = mapped_column(Text)
    ready_at: Mapped[datetime] = mapped_column(index=True)  # when the customer wants it (UTC)
    items: Mapped[list] = mapped_column(JSON)  # [{item_id, name, quantity, options: [names], notes, unit_price}]
    subtotal: Mapped[int] = mapped_column(Integer)  # cents, like every amount below
    fee: Mapped[int] = mapped_column(Integer)  # delivery fee
    total: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Booking(Scoped, Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    party_size: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(index=True)  # UTC
    # Appointments (a service with one person) also live here; a table booking leaves these empty.
    service_id: Mapped[str | None] = mapped_column(String(20))
    service: Mapped[str | None] = mapped_column(String(200))  # its name when booked
    provider: Mapped[str | None] = mapped_column(String(50))  # who does it
    minutes: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="booked")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Approval(Scoped, Base):
    """A request the assistant can't decide alone (refund, discount, big group): staff approve or decline it in the
    dashboard and the customer is told in their chat."""
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # one of APPROVAL_KINDS
    summary: Mapped[str] = mapped_column(Text)  # what the customer asked for, written by the assistant
    order_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)  # large_booking: the booking to make if approved
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | declined
    note: Mapped[str | None] = mapped_column(Text)  # staff's message to the customer
    decided_by: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(default=now)
    decided_at: Mapped[datetime | None] = mapped_column()


class Setting(Scoped, Base):
    """Switches staff flip in the dashboard, e.g. orders_paused."""
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("shop_id", "key"),)
    pk: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50))
    value: Mapped[dict | list | bool | None] = mapped_column(JSON)


class ToolCall(Scoped, Base):
    """One tool the AI used while answering a customer message, so staff can see why it replied the way it did."""
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)  # the customer message being answered
    name: Mapped[str] = mapped_column(String(50))
    args: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    ms: Mapped[int] = mapped_column(Integer)


class LlmUsage(Scoped, Base):
    """One row per AI API call, for the real cost on the Overview page (and what each café costs to run)."""
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the customer message it answered
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)  # None when the model's price isn't known


ORDER_STATUSES = ["new", "preparing", "ready", "completed", "cancelled"]
BOOKING_STATUSES = ["booked", "arrived", "no_show", "cancelled"]
APPROVAL_KINDS = ["refund", "discount", "large_booking", "special_request"]


def init_db():
    tables = inspect(engine).get_table_names()
    if "variants" in tables or ("menu_items" in tables and "shops" not in tables):
        raise RuntimeError("This database is from an older, single-business version of Mitu, which this one can't read. "
                           "Point DATABASE_URL at a new file (or move backend/shop.db away) and run seed.py.")
    Base.metadata.create_all(engine)
    # ponytail: add-only migration (new columns land as NULL in old rows); switch to Alembic to rename or drop columns.
    db_tables = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in db_tables.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"))
