from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import options
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


class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(Integer)  # cents
    options: Mapped[list] = mapped_column(JSON, default=list)  # see options.py
    dietary: Mapped[list] = mapped_column(JSON, default=list)  # vegan, gluten-free, ...: the assistant only repeats these
    allergens: Mapped[list] = mapped_column(JSON, default=list)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # other names customers use; helps search
    available: Mapped[bool] = mapped_column(Boolean, default=True)  # False = sold out


def new_item(data: dict) -> MenuItem:
    """A menu item from business.yaml-style fields (price as an amount, options as lines)."""
    return MenuItem(id=data["id"], name=data["name"], category=str(data["category"]).lower(), description=data.get("description", ""),
                    price=cents(data["price"]), options=options.parse(data.get("options", [])), dietary=data.get("dietary", []),
                    allergens=data.get("allergens", []), aliases=data.get("aliases", []), available=data.get("available", True))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # WhatsApp number, "messenger-<id>", "instagram-<id>", "sim-<phone>"
    channel: Mapped[str] = mapped_column(String(20))  # whatsapp | messenger | instagram | sim
    phone: Mapped[str] = mapped_column(String(30))
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    pending_order: Mapped[dict | None] = mapped_column(JSON)  # last quote shown to the customer
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | human (staff via dashboard)
    text: Mapped[str] = mapped_column(Text, default="")
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_path: Mapped[str | None] = mapped_column(String(200))  # file inside MEDIA_DIR: a voice note, a photo, or a menu photo (menu/M01.jpg)
    author: Mapped[str | None] = mapped_column(String(50))  # which staff member sent a "human" reply
    created_at: Mapped[datetime] = mapped_column(default=now)


class SeenWhatsAppMessage(Base):
    """Inbox of WhatsApp, Messenger and Instagram messages. The primary key makes Meta's retries fail to insert;
    done=False means a crash or restart cut processing off, so it's re-run at startup."""
    __tablename__ = "seen_wa_messages"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    done: Mapped[bool | None] = mapped_column(Boolean)
    channel: Mapped[str | None] = mapped_column(String(20))  # NULL = whatsapp (rows from before Messenger)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(50), index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    fulfilment: Mapped[str] = mapped_column(String(20))  # a key of business.yaml fulfilment: pickup | delivery
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


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(50), index=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    party_size: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(index=True)  # UTC
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="booked")
    created_at: Mapped[datetime] = mapped_column(default=now)


class Setting(Base):
    """Switches staff flip in the dashboard, e.g. orders_paused."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict | list | bool | None] = mapped_column(JSON)


class ToolCall(Base):
    """One tool the AI used while answering a customer message, so staff can see why it replied the way it did."""
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)  # the customer message being answered
    name: Mapped[str] = mapped_column(String(50))
    args: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    ms: Mapped[int] = mapped_column(Integer)


class LlmUsage(Base):
    """One row per AI API call, for the real cost on the Overview page."""
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(50), index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the customer message it answered; NULL in old rows
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)  # None when the model's price isn't known


ORDER_STATUSES = ["new", "preparing", "ready", "completed", "cancelled"]
BOOKING_STATUSES = ["booked", "arrived", "no_show", "cancelled"]


def init_db():
    if "variants" in inspect(engine).get_table_names():
        raise RuntimeError("This database is from the clothing-shop version of Mitu, which the café version can't read. "
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
