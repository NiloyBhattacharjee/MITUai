from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)
Session = sessionmaker(engine, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(200))
    name_bn: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    price: Mapped[int] = mapped_column(Integer)
    sizes: Mapped[list] = mapped_column(JSON)
    colours: Mapped[list] = mapped_column(JSON)
    legacy_stock: Mapped[int] = mapped_column("stock", Integer, default=0)  # old per-product total; stock now lives in variants
    variants: Mapped[list["Variant"]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    def sync_variants(self, new_stock: int = 0):
        """One variant per size x colour: add missing combinations, drop ones no longer offered, keep the rest's stock."""
        keep = {(v.size, v.colour): v for v in self.variants if v.size in self.sizes and v.colour in self.colours}
        self.variants = [keep.get((s, c)) or Variant(size=s, colour=c, stock=new_stock) for s in self.sizes for c in self.colours]

    def split_stock(self, total: int):
        """Spread a total evenly over every size x colour (the remainder goes to the first ones)."""
        self.variants = []
        self.sync_variants()
        each, extra = divmod(total, len(self.variants))
        for i, v in enumerate(self.variants):
            v.stock = each + (i < extra)

    def stock_of(self, size: str, colour: str) -> int:
        return next((v.stock for v in self.variants if v.size == size and v.colour == colour), 0)


class Variant(Base):
    """Stock for one size and colour of a product."""
    __tablename__ = "variants"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    size: Mapped[str] = mapped_column(String(30), primary_key=True)
    colour: Mapped[str] = mapped_column(String(50), primary_key=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)


def new_product(data: dict) -> Product:
    """A product from business.yaml-style fields, its 'stock' total spread over the sizes and colours."""
    p = Product(**{k: v for k, v in data.items() if k != "stock"})
    p.split_stock(data.get("stock", 0))
    return p


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # WhatsApp number, "messenger-<id>", "instagram-<id>", "sim-<phone>"
    channel: Mapped[str] = mapped_column(String(20))  # whatsapp | messenger | instagram | sim
    phone: Mapped[str] = mapped_column(String(30))
    language: Mapped[str | None] = mapped_column(String(20))
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
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | human (owner via dashboard)
    text: Mapped[str] = mapped_column(Text, default="")
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_path: Mapped[str | None] = mapped_column(String(200))  # file inside MEDIA_DIR: a voice note, or a product photo (products/P01.jpg)
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
    conversation_id: Mapped[str | None] = mapped_column(String(50))
    customer_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    address: Mapped[str] = mapped_column(Text)
    delivery_area: Mapped[str] = mapped_column(String(30))
    payment_method: Mapped[str] = mapped_column(String(20))
    items: Mapped[list] = mapped_column(JSON)
    subtotal: Mapped[int] = mapped_column(Integer)
    delivery_charge: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="new")
    paid: Mapped[bool | None] = mapped_column(Boolean, default=False)  # bKash/Nagad, ticked by the owner after checking
    payment_ref: Mapped[str | None] = mapped_column(String(40))  # transaction ID (TrxID) the customer sent
    created_at: Mapped[datetime] = mapped_column(default=now)


class LlmUsage(Base):
    """One row per AI API call, for the real cost on the Overview page."""
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)  # None when the model's price isn't known


ORDER_STATUSES = ["new", "confirmed", "shipped", "delivered", "cancelled"]


def init_db():
    Base.metadata.create_all(engine)
    # ponytail: add-only migration (new columns land as NULL in old rows); switch to Alembic to rename or drop columns.
    db_tables = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in db_tables.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"))
    with Session() as db:  # products from before per-size stock: spread their old total over sizes x colours once
        for p in db.scalars(select(Product).where(~Product.variants.any())):
            p.split_stock(p.legacy_stock or 0)
        db.commit()
