from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now() -> datetime:
    return datetime.now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[str] = mapped_column(String(20), default="staff")  # admin / staff
    phone: Mapped[str | None] = mapped_column(
        String(30), unique=True, index=True, nullable=True, default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    barcode: Mapped[str] = mapped_column(String(50), default="", index=True)  # 商品条码，扫码录单用
    name: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(50), default="默认分类", index=True)
    product_type: Mapped[str] = mapped_column(
        String(20), default="finished_good", index=True
    )  # raw_material 原料 / finished_good 成品
    unit: Mapped[str] = mapped_column(String(20), default="件")
    spec: Mapped[str] = mapped_column(String(100), default="")
    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 移动加权平均成本，随入库自动重算
    sale_price: Mapped[float] = mapped_column(Float, default=0.0)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class NameAlias(Base):
    """识别名称别名：单据上的叫法与系统主数据的对应关系。

    用户修正一次商品/供应商后记下来，下次同一张单据写法直接命中，不用反复手选。
    """

    __tablename__ = "name_aliases"
    __table_args__ = (UniqueConstraint("entity_type", "alias_key", name="uq_name_alias_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)  # product / supplier / customer
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    alias: Mapped[str] = mapped_column(String(200), default="")
    alias_key: Mapped[str] = mapped_column(String(200), index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # ocr / manual
    hits: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class StockLog(Base):
    """库存变动流水：按仓库/库位记录出入库、盘点调整与业务单据变动。"""

    __tablename__ = "stock_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    location_id: Mapped[int] = mapped_column(Integer, default=0)
    batch_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    change_type: Mapped[str] = mapped_column(String(20), default="adjust")  # in/out/check
    biz_type: Mapped[str] = mapped_column(String(20), default="adjust")  # 手工出入库/采购入库/销售出库...
    ref_no: Mapped[str] = mapped_column(String(40), default="", index=True)  # 关联单号
    change_qty: Mapped[int] = mapped_column(Integer, default=0)
    stock_before: Mapped[int] = mapped_column(Integer, default=0)  # 该库位变动前数量
    stock_after: Mapped[int] = mapped_column(Integer, default=0)  # 该库位变动后数量
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    product: Mapped[Product] = relationship()


class Warehouse(Base):
    """仓库主数据。"""

    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    address: Mapped[str] = mapped_column(String(200), default="")
    manager: Mapped[str] = mapped_column(String(50), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 默认仓库：采购入库、销售扣减优先
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    locations: Mapped[list["StorageLocation"]] = relationship(
        back_populates="warehouse", cascade="all, delete-orphan"
    )


class StorageLocation(Base):
    """库位：挂在仓库下的具体存放位置。"""

    __tablename__ = "storage_locations"
    __table_args__ = (UniqueConstraint("warehouse_id", "code", name="uq_location_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(80), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    warehouse: Mapped[Warehouse] = relationship(back_populates="locations")


class StockBalance(Base):
    """库存余额：商品 × 仓库 × 库位 × 批次维度的现存量。

    batch_no 为空表示不做批次管理的商品；启用了批次的商品入库时写入批次号与效期，
    出库按「先到期先出」（FEFO）选批次。
    """

    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "warehouse_id", "location_id", "batch_no", name="uq_stock_balance"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("storage_locations.id"), index=True)
    batch_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    product: Mapped[Product] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    location: Mapped[StorageLocation] = relationship()


class StockReservation(Base):
    """销售订单的库存预留：确认订单即占用，发货后转为已消耗，取消订单释放。

    可用量 = 商品总库存 − 活跃预留，避免「先确认的单被后来的单抢走库存」。
    """

    __tablename__ = "stock_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    order_type: Mapped[str] = mapped_column(String(20), default="sales", index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    order_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    # active=占用中 / consumed=已随发货消耗 / released=已释放
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    contact: Mapped[str] = mapped_column(String(50), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    level: Mapped[str] = mapped_column(String(2), default="C")  # A/B/C
    credit_limit: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(50), default="", index=True)  # 负责的销售员，空=公海
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    contact: Mapped[str] = mapped_column(String(50), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    supply_category: Mapped[str] = mapped_column(String(50), default="")
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class OpeningBalance(Base):
    """期初往来余额：客户期初应收 / 供应商期初应付，系统上线时一次性带入。

    只存余额本身，不再造历史单据；账龄与对账单期初都读这张表，
    这样「上线前的欠款」不会凭空消失，也不必伪造订单。
    """

    __tablename__ = "opening_balances"
    __table_args__ = (UniqueConstraint("partner_type", "partner_id", name="uq_opening_balance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    partner_type: Mapped[str] = mapped_column(String(20), index=True)  # customer / supplier
    partner_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    balance_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AuditLog(Base):
    """权限审计日志：记录关键操作、登录事件与越权访问。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), default="", index=True)
    role: Mapped[str] = mapped_column(String(20), default="", index=True)
    action: Mapped[str] = mapped_column(String(100), default="", index=True)  # 动作：登录/新建订单/越权访问...
    resource: Mapped[str] = mapped_column(String(100), default="")  # 资源对象，如 销售订单 SO...
    detail: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(20), default="success", index=True)  # success/denied/failed
    method: Mapped[str] = mapped_column(String(10), default="")
    path: Mapped[str] = mapped_column(String(200), default="")
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    ip: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # draft/pending/confirmed/completed/cancelled；台账按状态+日期筛选，必须有索引
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    order_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 税率(%)，>0 时 total_amount 视为价税合计
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")
    # 照片识别建单时留存原图与识别原文，便于事后核对
    photo_path: Mapped[str] = mapped_column(String(300), default="")
    photo_name: Mapped[str] = mapped_column(String(200), default="")
    photo_paths: Mapped[str] = mapped_column(Text, default="")  # JSON 数组，多页照片
    ocr_status: Mapped[str] = mapped_column(String(20), default="")
    ocr_raw: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    # 多级审批：应走几级、已完成几级；0 表示沿用单级流程
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    customer: Mapped[Customer] = relationship()
    items: Mapped[list["SalesOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    outbounds: Mapped[list["OutboundOrder"]] = relationship(
        back_populates="sales_order", cascade="all, delete-orphan"
    )


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    order: Mapped[SalesOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class OutboundOrder(Base):
    """销售出库单：一张销售订单可拆多张，按实际发货分批发货。

    库存扣减发生在「出库确认」（status=shipped）；签收（signed）只做交付凭证，
    不重复动库存；取消已确认的出库单会把货退回默认库位并写流水。
    """

    __tablename__ = "outbound_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    outbound_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    sales_order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_locations.id"), nullable=True, default=None
    )
    # draft=待发货 / shipped=已出库（库存已扣） / signed=已签收 / cancelled=已取消
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    logistics_company: Mapped[str] = mapped_column(String(60), default="")
    logistics_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    ship_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None, index=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    signed_by: Mapped[str] = mapped_column(String(50), default="")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    total_quantity: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    # 多级审批：配置了出库审批规则时，必须走「提交审批 → 审批通过」才出库
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    sales_order: Mapped[SalesOrder] = relationship(back_populates="outbounds")
    customer: Mapped[Customer] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    location: Mapped[StorageLocation | None] = relationship()
    items: Mapped[list["OutboundItem"]] = relationship(
        back_populates="outbound", cascade="all, delete-orphan"
    )


class OutboundItem(Base):
    __tablename__ = "outbound_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    outbound_id: Mapped[int] = mapped_column(ForeignKey("outbound_orders.id"), index=True)
    # 对应销售订单明细行：多次发货按行累计已发数量，避免超发
    order_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_order_items.id"), nullable=True, default=None, index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(String(200), default="")

    outbound: Mapped[OutboundOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/ordered/received/cancelled
    order_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 税率(%)，>0 时 total_amount 视为价税合计
    remark: Mapped[str] = mapped_column(Text, default="")
    # 照片识别建单时留存原图与识别原文，便于事后核对
    photo_path: Mapped[str] = mapped_column(String(300), default="")
    photo_name: Mapped[str] = mapped_column(String(200), default="")
    photo_paths: Mapped[str] = mapped_column(Text, default="")  # JSON 数组，多页照片
    ocr_status: Mapped[str] = mapped_column(String(20), default="")
    ocr_raw: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    # 多级审批：应走几级、已完成几级；0 表示沿用单级流程
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class TransferOrder(Base):
    """多仓调拨单：草稿 → 发出（源仓扣减、进入在途）→ 到达确认（目标仓入库）/取消回滚。"""

    __tablename__ = "transfer_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    from_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    to_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/in_transit/completed/cancelled
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    from_warehouse: Mapped[Warehouse] = relationship(foreign_keys=[from_warehouse_id])
    to_warehouse: Mapped[Warehouse] = relationship(foreign_keys=[to_warehouse_id])
    items: Mapped[list["TransferItem"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )


class TransferItem(Base):
    """调拨明细：按商品与源/目标库位记录数量。"""

    __tablename__ = "transfer_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("transfer_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    from_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    to_location_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    transfer: Mapped[TransferOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class FeishuConfig(Base):
    """飞书机器人接入配置：管理员在「飞书通知」页面填写，保存即生效，无需改代码或重启服务。"""

    __tablename__ = "feishu_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_url: Mapped[str] = mapped_column(String(300), default="")
    secret: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AlertRule(Base):
    """库存自动预警规则：库存跌破条件时自动推送飞书，冷却期内同一商品不重复提醒。"""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    condition: Mapped[str] = mapped_column(String(20), default="below_safety")  # below_safety / out_of_stock
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=120)
    include_replenishment: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AlertState(Base):
    """自动预警去重状态：记录每个商品最近一次自动预警时间。"""

    __tablename__ = "alert_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    last_alert_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_stock_qty: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base):
    """站内消息：审批提交、审批超时、库存预警等统一收口到顶栏铃铛。

    与飞书通知并存互不影响：飞书走千人群推送，站内消息按用户维度落库，
    各自读取自己的未读列表即可。
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(20), default="approval", index=True)  # approval / sla / stock
    title: Mapped[str] = mapped_column(String(120), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    ref_type: Mapped[str] = mapped_column(String(20), default="", index=True)  # 关联单据类型
    ref_id: Mapped[int] = mapped_column(Integer, default=0, index=True)  # 关联单据 id
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    user: Mapped[User] = relationship()


# ---------------- 应收与对账 ----------------
class Payment(Base):
    """收款单：一笔真实到账，可核销多张销售订单。

    有了收款单，应收才算得清（已确认订单金额 − 已核销金额）；
    没有它，对账单只能按订单一侧计账，对方一句「我早付了」就对不下去。
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    pay_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    method: Mapped[str] = mapped_column(String(20), default="银行转账")
    reference: Mapped[str] = mapped_column(String(60), default="", index=True)  # 银行流水号/回单号
    remark: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)  # pending/posted/voided
    void_reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # 多级审批：配置了收款审批规则时，先挂 pending，审批通过才转 posted 计入应收
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    customer: Mapped["Customer"] = relationship()
    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentAllocation(Base):
    """核销明细：一笔收款抵扣某张销售订单的金额。"""

    __tablename__ = "payment_allocations"
    __table_args__ = (UniqueConstraint("payment_id", "order_id", name="uq_allocation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    payment: Mapped[Payment] = relationship(back_populates="allocations")
    order: Mapped["SalesOrder"] = relationship()


class StatementSnapshot(Base):
    """对账单快照：生成时冻结内容并记录哈希。

    之后订单或收款再变，已发出的版本也不会跟着变——这是「对完又变」的唯一解法。
    """

    __tablename__ = "statement_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    period_start: Mapped[str] = mapped_column(String(10), index=True)  # yyyy-mm-dd
    period_end: Mapped[str] = mapped_column(String(10), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    opening_amount: Mapped[float] = mapped_column(Float, default=0.0)
    increase_amount: Mapped[float] = mapped_column(Float, default=0.0)
    decrease_amount: Mapped[float] = mapped_column(Float, default=0.0)
    closing_amount: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[str] = mapped_column(Text, default="")  # 冻结的对账明细 JSON
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(20), default="issued", index=True)  # issued/confirmed/disputed
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    customer: Mapped["Customer"] = relationship()


class ShareToken(Base):
    """对账单分享凭证：链接与口令分开下发，库里只存哈希。"""

    __tablename__ = "share_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statement_snapshots.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    max_uses: Mapped[int] = mapped_column(Integer, default=20)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class DocumentEvent(Base):
    """单据行为留痕：查看、口令校验、确认、异议，逐条追加、不修改。"""

    __tablename__ = "document_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int | None] = mapped_column(
        ForeignKey("statement_snapshots.id"), index=True, nullable=True, default=None
    )
    event_type: Mapped[str] = mapped_column(String(20), default="view", index=True)  # view/confirm/dispute/deny
    phone: Mapped[str] = mapped_column(String(30), default="")
    code_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str] = mapped_column(String(50), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class PhoneVerificationCode(Base):
    """注册/开户手机验证码：按「手机号 + 场景」维度一次性有效。

    与对账单的 VerificationCode 分开，避免同一张表里混入两种业务含义。
    """

    __tablename__ = "phone_verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), index=True)
    scene: Mapped[str] = mapped_column(String(20), default="register", index=True)
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class VerificationCode(Base):
    """短信验证码：只存哈希，带有效期、尝试上限与已用标记。

    与 ShareToken 的口令并存：配置了短信走短信，没配置走口令，流程不变。
    """

    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statement_snapshots.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------- 订单审批 ----------------
class ApprovalLog(Base):
    """订单审批留痕：提交、通过、驳回逐条追加，不覆盖历史。

    订单本身只用 status 字段承载当前状态，审批人与意见都在这里查，
    这样不改动既有订单表结构，也能完整回答「谁在什么时候批的」。
    """

    __tablename__ = "approval_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_type: Mapped[str] = mapped_column(String(20), index=True)  # sales / purchase
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(20), index=True)  # submit / approve / reject
    # 多级审批时记录这一次动作属于第几级；旧的单级流程为 0
    level: Mapped[int] = mapped_column(Integer, default=0)
    operator: Mapped[str] = mapped_column(String(50), default="")
    # 会签/委托时记录「实际代表谁」：本人审批为自己，代理人审批为委托的 principal
    principal: Mapped[str] = mapped_column(String(50), default="")
    role: Mapped[str] = mapped_column(String(20), default="")
    comment: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class ApprovalRule(Base):
    """审批规则：按单据类型 + 金额区间定义每一级审批需要的权限。

    同一金额区间可以有多条规则（level=1/2/3），命中几条就要走几级；
    没有任何规则命中时退回单级审批，保证老账套升级后行为不变。
    """

    __tablename__ = "approval_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(20), index=True)  # sales / purchase
    name: Mapped[str] = mapped_column(String(60), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    min_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 含下限
    max_amount: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)  # 不含上限
    level: Mapped[int] = mapped_column(Integer, default=1)
    # 该级需要的权限 key，例如 sales:approve / finance:manage / purchase:approve
    permission: Mapped[str] = mapped_column(String(50), default="")
    level_name: Mapped[str] = mapped_column(String(40), default="")
    # 会签/或签：mode=single 单人（默认）/ or 或签（任一）/ counter 会签（全部）
    mode: Mapped[str] = mapped_column(String(10), default="single")
    # 指定审批人用户名（逗号分隔），会签/或签时生效；为空则退回权限校验
    approvers: Mapped[str] = mapped_column(String(300), default="")
    # 条件分支（可选）：JSON 对象，如 {"credit_exceeded": true, "min_margin": 0.15, "customer_level": ["A","B"]}
    # 为空表示不附加条件，仅按金额区间匹配
    conditions: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ApprovalDelegation(Base):
    """审批委托代理：delegator 把审批权委派给 delegatee（按单据类型）。"""

    __tablename__ = "approval_delegations"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(20), index=True)  # sales / purchase
    delegator: Mapped[str] = mapped_column(String(50), index=True)  # 委托人用户名
    delegatee: Mapped[str] = mapped_column(String(50), index=True)  # 代理人用户名
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CreditPolicy(Base):
    """按客户等级配置信用管控策略；customer_level='*' 是全局默认。"""

    __tablename__ = "credit_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_level: Mapped[str] = mapped_column(String(4), unique=True, index=True)  # A/B/C/*
    policy: Mapped[str] = mapped_column(String(10), default="warn")  # off/warn/block
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CreditOverride(Base):
    """超额特批：单张订单在信用超额时申请放行，审批通过后该单可继续出库。"""

    __tablename__ = "credit_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_type: Mapped[str] = mapped_column(String(20), default="sales", index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    over_amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/approved/rejected
    requested_by: Mapped[str] = mapped_column(String(50), default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_by: Mapped[str] = mapped_column(String(50), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    comment: Mapped[str] = mapped_column(String(300), default="")


# ---------------- 销售 / 采购退货 ----------------
class SalesReturn(Base):
    """销售退货单：草稿 → 确认（回补库存并冲减应收）→ 取消可回滚。"""

    __tablename__ = "sales_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    return_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True, default=None)
    location_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/confirmed/cancelled
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # 多级审批
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped["SalesOrder"] = relationship()
    customer: Mapped["Customer"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    items: Mapped[list["SalesReturnItem"]] = relationship(
        back_populates="ret", cascade="all, delete-orphan"
    )


class SalesReturnItem(Base):
    __tablename__ = "sales_return_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("sales_returns.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    ret: Mapped[SalesReturn] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


class PurchaseReturn(Base):
    """采购退货单：草稿 → 确认（扣减库存并冲减应付）→ 取消可回滚。"""

    __tablename__ = "purchase_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    return_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/confirmed/cancelled
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # 多级审批
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped["PurchaseOrder"] = relationship()
    supplier: Mapped["Supplier"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    items: Mapped[list["PurchaseReturnItem"]] = relationship(
        back_populates="ret", cascade="all, delete-orphan"
    )


class PurchaseReturnItem(Base):
    __tablename__ = "purchase_return_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("purchase_returns.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    ret: Mapped[PurchaseReturn] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


# ---------------- 应付与供应商对账 ----------------
class SupplierPayment(Base):
    """付款单：一笔真实付款，可核销多张采购入库单，应付才算得清。"""

    __tablename__ = "supplier_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    pay_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    method: Mapped[str] = mapped_column(String(20), default="银行转账")
    reference: Mapped[str] = mapped_column(String(60), default="", index=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)  # pending/posted/voided
    void_reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # 多级审批：配置了付款审批规则时，先挂 pending，审批通过才转 posted 计入应付
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    supplier: Mapped["Supplier"] = relationship()
    allocations: Mapped[list["SupplierPaymentAllocation"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class SupplierPaymentAllocation(Base):
    __tablename__ = "supplier_payment_allocations"
    __table_args__ = (UniqueConstraint("payment_id", "order_id", name="uq_supplier_allocation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("supplier_payments.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    payment: Mapped[SupplierPayment] = relationship(back_populates="allocations")
    order: Mapped["PurchaseOrder"] = relationship()


class SupplierStatement(Base):
    """供应商对账单快照：生成即冻结，本期采购、退货、付款与期末应付都写死在 payload。"""

    __tablename__ = "supplier_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    period_start: Mapped[str] = mapped_column(String(10), index=True)
    period_end: Mapped[str] = mapped_column(String(10), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    opening_amount: Mapped[float] = mapped_column(Float, default=0.0)
    increase_amount: Mapped[float] = mapped_column(Float, default=0.0)
    decrease_amount: Mapped[float] = mapped_column(Float, default=0.0)
    closing_amount: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    supplier: Mapped["Supplier"] = relationship()


# ---------------- 库存盘点 ----------------
class StocktakeOrder(Base):
    """盘点单：建单时冻结账面数量，录入实盘后一次性生成差异调整。"""

    __tablename__ = "stocktake_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/pending/completed/cancelled
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # 差异审批：提交后由第二个人复核，通过才真正调整库存
    submitted_by: Mapped[str] = mapped_column(String(50), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    approved_by: Mapped[str] = mapped_column(String(50), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    approve_comment: Mapped[str] = mapped_column(String(300), default="")
    # 多级审批：配置了盘点审批规则时，差异复核走通用审批（支持会签/委托）
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)

    warehouse: Mapped["Warehouse"] = relationship()
    items: Mapped[list["StocktakeItem"]] = relationship(
        back_populates="stocktake", cascade="all, delete-orphan"
    )


class StocktakeItem(Base):
    __tablename__ = "stocktake_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_id: Mapped[int] = mapped_column(ForeignKey("stocktake_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("storage_locations.id"), index=True)
    book_qty: Mapped[int] = mapped_column(Integer, default=0)
    actual_qty: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    diff_qty: Mapped[int] = mapped_column(Integer, default=0)

    stocktake: Mapped[StocktakeOrder] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()
    location: Mapped["StorageLocation"] = relationship()


# ---------------- 单据模板 ----------------
class DocumentTemplate(Base):
    """可配置的单据版式：抬头、Logo、可见列、条款、签字栏与纸张。

    同一 doc_type 可有多套模板，is_default 决定打印时使用哪一套；
    配置整体存 JSON，前端新增字段无需改表结构。
    """

    __tablename__ = "document_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(60), default="")
    title: Mapped[str] = mapped_column(String(60), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 客户上传的 Word 初始格式（.docx）；有该文件时打印直接套用，未上传则用系统内置版式
    word_file: Mapped[str] = mapped_column(String(200), default="")
    word_filename: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


# ---------------- 入库单 ----------------
class InboundOrder(Base):
    """入库单：草稿 → 确认入库；可由照片识别生成，照片与原始识别结果一并留存。"""

    __tablename__ = "inbound_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    inbound_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    purchase_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True, default=None, index=True
    )
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_locations.id"), nullable=True, default=None
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/confirmed/cancelled
    receipt_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")
    photo_path: Mapped[str] = mapped_column(String(300), default="")
    photo_name: Mapped[str] = mapped_column(String(200), default="")
    photo_paths: Mapped[str] = mapped_column(Text, default="")  # JSON 数组，多页照片
    ocr_status: Mapped[str] = mapped_column(String(20), default="")  # recognized / manual / failed
    ocr_raw: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    supplier: Mapped["Supplier"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    location: Mapped["StorageLocation"] = relationship()
    purchase_order: Mapped["PurchaseOrder"] = relationship()
    items: Mapped[list["InboundItem"]] = relationship(
        back_populates="inbound", cascade="all, delete-orphan"
    )


class InboundItem(Base):
    __tablename__ = "inbound_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    inbound_id: Mapped[int] = mapped_column(ForeignKey("inbound_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    batch_no: Mapped[str] = mapped_column(String(60), default="")
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    remark: Mapped[str] = mapped_column(String(200), default="")

    inbound: Mapped[InboundOrder] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


# ---------------- 自动化规则 ----------------
class AutomationRule(Base):
    """自动化规则：把「什么条件下做什么」写成可配置规则，由调度器定期求值。

    动作只允许生成草稿采购单或推送通知，绝不确认订单、审批或改动库存，
    这样即使规则配错，最多多出几张草稿或几条消息，不会动到账实。
    """

    __tablename__ = "automation_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(30), default="", index=True)
    trigger_config: Mapped[dict] = mapped_column(JSON, default=dict)
    action_type: Mapped[str] = mapped_column(String(30), default="", index=True)
    action_config: Mapped[dict] = mapped_column(JSON, default=dict)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=240)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    last_status: Mapped[str] = mapped_column(String(20), default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AutomationRun(Base):
    """规则执行记录：每次真实执行留一条，失败原因与命中对象都写在这里。"""

    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("automation_rules.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="", index=True)  # success/skipped/failed
    summary: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ---------------- 生产制造：BOM / 生产订单 / 领料 / 工序 / 费用 ----------------
class BillOfMaterials(Base):
    """物料清单（BOM）：一个成品按 base_qty 产出所需的标准用料。"""

    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    version: Mapped[str] = mapped_column(String(20), default="V1.0")
    base_qty: Mapped[float] = mapped_column(Float, default=1.0)  # 标准产出数量
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active/inactive
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    product: Mapped["Product"] = relationship()
    items: Mapped[list["BomItem"]] = relationship(
        back_populates="bom", cascade="all, delete-orphan"
    )


class BomItem(Base):
    """BOM 用料明细：base_qty 个成品需要多少子件。"""

    __tablename__ = "bom_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    remark: Mapped[str] = mapped_column(String(200), default="")

    bom: Mapped["BillOfMaterials"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


class ProductionOrder(Base):
    """生产订单：下达时按 BOM 展开用料，领料扣料、完工入库成品。"""

    __tablename__ = "production_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    bom_id: Mapped[int | None] = mapped_column(ForeignKey("boms.id"), nullable=True, default=None)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft=草稿 / released=已下达（用料已展开） / in_progress=生产中 / completed=已完工 / cancelled=已取消
    source_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True, default=None, index=True
    )
    plan_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    plan_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    product: Mapped["Product"] = relationship(foreign_keys=[product_id])
    bom: Mapped["BillOfMaterials | None"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    materials: Mapped[list["ProductionMaterial"]] = relationship(
        back_populates="production", cascade="all, delete-orphan"
    )
    processes: Mapped[list["ProductionProcess"]] = relationship(
        back_populates="production", cascade="all, delete-orphan"
    )
    expenses: Mapped[list["ProductionExpense"]] = relationship(
        back_populates="production", cascade="all, delete-orphan"
    )


class ProductionMaterial(Base):
    """生产用料需求：required_qty 为 BOM 展开的需求量，issued_qty 为已领料量。"""

    __tablename__ = "production_materials"
    __table_args__ = (UniqueConstraint("production_id", "product_id", name="uq_production_material"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    required_qty: Mapped[float] = mapped_column(Float, default=0.0)
    issued_qty: Mapped[float] = mapped_column(Float, default=0.0)
    warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True, default=None
    )  # 领料仓，取消生产时按此仓退回

    production: Mapped["ProductionOrder"] = relationship(back_populates="materials")
    product: Mapped["Product"] = relationship()
    warehouse: Mapped["Warehouse | None"] = relationship()


class ProductionProcess(Base):
    """工序节点：下达时按默认工艺生成，完成后可扫码报工。"""

    __tablename__ = "production_processes"

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/completed
    operator: Mapped[str] = mapped_column(String(50), default="")
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    remark: Mapped[str] = mapped_column(String(200), default="")

    production: Mapped["ProductionOrder"] = relationship(back_populates="processes")


class ProductionExpense(Base):
    """生产费用：人工、制造费用等，完工时与材料成本一起归集到生产订单。"""

    __tablename__ = "production_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("production_orders.id"), index=True)
    category: Mapped[str] = mapped_column(String(20), default="制造费用")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    production: Mapped["ProductionOrder"] = relationship(back_populates="expenses")


# ---------------- 销售报价 ----------------
class Quotation(Base):
    """销售报价单：报价经客户确认后可直接转成销售订单草稿。"""

    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft=草稿 / sent=已发送 / accepted=客户确认 / rejected=已失效 / converted=已转订单
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    converted_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_orders.id"), nullable=True, default=None
    )
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    customer: Mapped["Customer"] = relationship()
    items: Mapped[list["QuotationItem"]] = relationship(
        back_populates="quotation", cascade="all, delete-orphan"
    )


class QuotationItem(Base):
    __tablename__ = "quotation_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    quotation_id: Mapped[int] = mapped_column(ForeignKey("quotations.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    quotation: Mapped["Quotation"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


# ---------------- 采购申请 ----------------
class PurchaseRequisition(Base):
    """采购申请单：需求部门先提申请，审批通过后转成采购订单，避免口头采购。"""

    __tablename__ = "purchase_requisitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    req_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft=草稿 / pending=待审批 / approved=已批准 / rejected=已驳回 / converted=已转采购单 / cancelled=已取消
    needed_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    remark: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str] = mapped_column(String(50), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # 多级审批：应走几级、已完成几级；0 表示沿用单级流程
    approval_required: Mapped[int] = mapped_column(Integer, default=0)
    approval_approved: Mapped[int] = mapped_column(Integer, default=0)
    converted_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True, default=None
    )
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    items: Mapped[list["PurchaseRequisitionItem"]] = relationship(
        back_populates="requisition", cascade="all, delete-orphan"
    )


class PurchaseRequisitionItem(Base):
    __tablename__ = "purchase_requisition_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey("purchase_requisitions.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    est_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(String(200), default="")

    requisition: Mapped["PurchaseRequisition"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


# ---------------- 库存调整单据（盘盈入库 / 盘亏出库 / 成本调整） ----------------
class ApprovalSla(Base):
    """审批超时规则：待审批超过 remind_hours 小时提醒，超过 escalate_hours 小时升级到上级。"""

    __tablename__ = "approval_slas"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    remind_hours: Mapped[int] = mapped_column(Integer, default=24)  # N：到时提醒审批人
    escalate_hours: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)  # M：到时升级上级，空=不升级
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StockAdjustment(Base):
    """库存调整单：盘盈、盘亏走数量，成本调整只改单位成本不动数量。"""

    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    adjust_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    adjust_type: Mapped[str] = mapped_column(String(20), default="gain", index=True)  # gain/loss/cost
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft/posted/cancelled
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    # 来源追溯：盘点生成的调整单记录盘点单号，避免重复生成
    source_type: Mapped[str] = mapped_column(String(20), default="", index=True)
    source_no: Mapped[str] = mapped_column(String(40), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    warehouse: Mapped["Warehouse"] = relationship()
    items: Mapped[list["StockAdjustmentItem"]] = relationship(
        back_populates="adjustment", cascade="all, delete-orphan"
    )


class StockAdjustmentItem(Base):
    __tablename__ = "stock_adjustment_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("stock_adjustments.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)  # 盘盈/盘亏数量
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 成本调整时为调整后的单位成本
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 金额影响

    adjustment: Mapped["StockAdjustment"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


# ---------------- 采购询价 ----------------
class PurchaseInquiry(Base):
    """采购询价单：一个需求商品对应多家供应商报价，选定报价后转采购订单。"""

    __tablename__ = "purchase_inquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    inquiry_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft=草稿 / open=询价中 / closed=已截止 / converted=已转采购订单
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    remark: Mapped[str] = mapped_column(Text, default="")
    converted_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id"), nullable=True, default=None
    )
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    product: Mapped["Product"] = relationship()
    quotes: Mapped[list["PurchaseInquiryQuote"]] = relationship(
        back_populates="inquiry", cascade="all, delete-orphan"
    )


class PurchaseInquiryQuote(Base):
    __tablename__ = "purchase_inquiry_quotes"
    __table_args__ = (UniqueConstraint("inquiry_id", "supplier_id", name="uq_inquiry_supplier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("purchase_inquiries.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    inquiry: Mapped["PurchaseInquiry"] = relationship(back_populates="quotes")
    supplier: Mapped["Supplier"] = relationship()


# ---------------- 发票管理 ----------------
class Invoice(Base):
    """发票台账：销项（开给客户）与进项（供应商开入）统一登记，含进项认证标记。"""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    direction: Mapped[str] = mapped_column(String(20), default="sales", index=True)  # sales 销项 / purchase 进项
    partner_name: Mapped[str] = mapped_column(String(100), default="", index=True)  # 客户或供应商名称
    order_no: Mapped[str] = mapped_column(String(40), default="", index=True)  # 关联单据号
    amount: Mapped[float] = mapped_column(Float, default=0.0)  # 不含税金额
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 税率(%)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 税额
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 价税合计
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/issued/voided
    certified: Mapped[bool] = mapped_column(Boolean, default=False)  # 进项已认证
    certified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)  # 已审核
    reviewed_by: Mapped[str] = mapped_column(String(50), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    issue_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class PrintJob(Base):
    """打印记录：谁在什么时候按哪个模板打了哪张单，便于补打与审计。"""

    __tablename__ = "print_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="single", index=True)  # single/batch/calibration
    doc_type: Mapped[str] = mapped_column(String(30), default="", index=True)
    doc_ids: Mapped[str] = mapped_column(String(300), default="")  # 逗号分隔，批量时多条
    doc_nos: Mapped[str] = mapped_column(String(600), default="")
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    watermark: Mapped[str] = mapped_column(String(50), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    operator: Mapped[str] = mapped_column(String(50), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ---------------- 总账：会计科目 / 记账凭证 ----------------
class Account(Base):
    """会计科目（中国小企业会计准则），code 为科目编码，一级科目唯一。"""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # 1001 / 1002 ...
    name: Mapped[str] = mapped_column(String(60), index=True)
    category: Mapped[str] = mapped_column(String(20), default="资产")  # 资产/负债/所有者权益/成本/损益
    direction: Mapped[str] = mapped_column(String(4), default="dr")  # dr 借方 / cr 贷方（正常余额方向）
    parent_code: Mapped[str] = mapped_column(String(20), default="", index=True)  # 上级科目，一级为 ""
    level: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Voucher(Base):
    """记账凭证：复式记账，借贷必相等。status: draft 草稿 / posted 已记账 / voided 作废。"""

    __tablename__ = "vouchers"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)  # 2026-11
    voucher_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    summary: Mapped[str] = mapped_column(String(200), default="")
    total_debit: Mapped[float] = mapped_column(Float, default=0.0)
    total_credit: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="manual", index=True)  # manual/sales/payment/purchase/supplier_payment
    source_no: Mapped[str] = mapped_column(String(60), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class VoucherLine(Base):
    """凭证分录：每个科目一行，借贷单边金额。account_code/name 冗余保存，便于报表直接聚合。"""

    __tablename__ = "voucher_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(Integer, index=True)
    account_code: Mapped[str] = mapped_column(String(20), default="", index=True)
    account_name: Mapped[str] = mapped_column(String(60), default="")
    summary: Mapped[str] = mapped_column(String(200), default="")
    debit: Mapped[float] = mapped_column(Float, default=0.0)
    credit: Mapped[float] = mapped_column(Float, default=0.0)
    partner_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------- 期末结账 / 会计期间 ----------------
class AccountPeriod(Base):
    """会计期间：period=YYYY-MM，status=open/closed。

    期间关闭后禁止再改该期间的凭证（记账、作废、自动生成），
    并生成一张「结转损益」凭证把损益类科目余额转入本年利润；反结账时冲回。
    """

    __tablename__ = "account_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)  # 2026-11
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open/closed
    carry_voucher_no: Mapped[str] = mapped_column(String(40), default="")  # 结转损益凭证号，反结账时冲回
    closed_by: Mapped[str] = mapped_column(String(50), default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    reopened_by: Mapped[str] = mapped_column(String(50), default="")
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


# ---------------- 银行流水对账 ----------------
class BankStatement(Base):
    """银行流水行：从网银导出文件导入，逐条登记并尝试与应收/应付核销。"""

    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account: Mapped[str] = mapped_column(String(60), default="", index=True)
    # 唯一流水标识：银行流水号 + 交易日期 + 金额，避免重复导入
    ref_no: Mapped[str] = mapped_column(String(60), default="", index=True)  # 银行流水号/凭证号
    trans_date: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    direction: Mapped[str] = mapped_column(String(10), default="in", index=True)  # in 流入 / out 流出
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    counterparty: Mapped[str] = mapped_column(String(100), default="", index=True)  # 对方户名
    summary: Mapped[str] = mapped_column(String(200), default="")  # 摘要/备注
    status: Mapped[str] = mapped_column(String(20), default="unmatched", index=True)  # unmatched/matched/ignored
    # 匹配结果：unmatched=未匹配 / matched=已生成收付款核销 / ignored=已忽略（费用、手续费等）
    matched_type: Mapped[str] = mapped_column(String(20), default="")  # receipt 收款 / payment 付款
    matched_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)  # 生成的收/付款单 id
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class BankStatementMatch(Base):
    """银行流水对账建议：一条流水给出一组核销候选，用户确认后落库生成收/付款单。"""

    __tablename__ = "bank_statement_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("bank_statements.id"), index=True)
    partner_type: Mapped[str] = mapped_column(String(10), default="customer")  # customer / supplier
    partner_id: Mapped[int] = mapped_column(Integer, index=True)
    partner_name: Mapped[str] = mapped_column(String(100), default="")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(10), default="low")  # high/medium/low
    strategy: Mapped[str] = mapped_column(String(20), default="")
    allocations: Mapped[str] = mapped_column(Text, default="")  # JSON 数组：核销建议明细
    status: Mapped[str] = mapped_column(String(20), default="suggested", index=True)  # suggested/applied/dismissed
    applied_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
