from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PhotoPage(BaseModel):
    """一张单据照片：识别时按页合并，保存时留档。"""

    path: str = ""
    name: str = ""


# ---------- 认证 ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(ORMBase):
    id: int
    username: str
    full_name: str
    role: str
    role_name: str = ""
    permissions: list[str] = []
    data_scope: str = "all"
    data_scope_name: str = "全部数据"
    ai_prompts: list[str] = []


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str = ""
    role: str = "sales"


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=64)


class RegisterCodeRequest(BaseModel):
    phone: str


class RegisterRequest(BaseModel):
    """自助注册：手机号 + 验证码，注册后账号待管理员审核。"""

    phone: str
    code: str
    password: str = Field(min_length=6, max_length=64)
    username: str = ""
    full_name: str = ""


class StaffCreate(BaseModel):
    """管理员为员工开户：同样要求手机号验证码，账号直接启用。"""

    phone: str
    code: str
    password: str = Field(min_length=6, max_length=64)
    username: str = ""
    full_name: str = ""
    role: str = "sales"


class UserDetail(ORMBase):
    id: int
    username: str
    full_name: str
    phone: str = ""
    role: str
    role_name: str = ""
    is_active: bool
    created_at: datetime


# ---------- 库存 ----------
class StockAdjust(BaseModel):
    product_id: int
    change_type: str = "check"  # in / out / check
    quantity: int = Field(gt=0, description="入/出库数量；盘点时为该库位盘点后实际数量")
    warehouse_id: Optional[int] = None  # 不传则落到默认仓库
    location_id: Optional[int] = None  # 不传则落到该仓库第一个库位
    batch_no: str = ""  # 批次号，空表示不启用批次
    expiry_date: Optional[datetime] = None  # 批次效期
    reason: str = ""


class StockLogOut(ORMBase):
    id: int
    product_id: int
    product_name: str = ""
    product_sku: str = ""
    product_type: str = ""
    warehouse_id: int = 0
    warehouse_name: str = ""
    location_id: int = 0
    location_code: str = ""
    change_type: str
    biz_type: str = ""
    ref_no: str = ""
    change_qty: int
    stock_before: int
    stock_after: int
    reason: str
    created_by: str
    created_at: datetime


# ---------- 仓库 / 库位 ----------
class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=80)
    address: str = ""
    manager: str = ""
    is_default: bool = False
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=30)
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    address: Optional[str] = None
    manager: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class LocationIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = ""
    is_active: bool = True


class LocationUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=30)
    name: Optional[str] = None
    is_active: Optional[bool] = None


# ---------- 库存调拨 ----------
class TransferItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, description="调拨数量")
    from_location_id: Optional[int] = None  # 不传则从源仓默认库位出
    to_location_id: Optional[int] = None  # 不传则入目标仓默认库位


class TransferIn(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    remark: str = ""
    items: list[TransferItemIn] = Field(min_length=1, max_length=50)


class TransferActionIn(BaseModel):
    action: Literal["send", "complete", "cancel"]



# ---------- 商品 ----------
class ProductBase(BaseModel):
    sku: str
    barcode: str = ""
    name: str
    category: str = "默认分类"
    product_type: Literal["raw_material", "finished_good"] = "finished_good"
    unit: str = "件"
    spec: str = ""
    cost_price: float = 0.0
    sale_price: float = 0.0
    stock_qty: int = 0
    safety_stock: int = 0
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductQuickCreate(BaseModel):
    """识别时「新建商品」用：只需要名称，SKU 由后端按日期生成。"""

    name: str = Field(min_length=1, max_length=100)
    spec: str = ""
    unit: str = "件"
    category: str = "默认分类"
    product_type: Literal["raw_material", "finished_good"] = "raw_material"
    cost_price: float = 0.0
    sale_price: float = 0.0


class ProductUpdate(BaseModel):
    barcode: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    product_type: Optional[Literal["raw_material", "finished_good"]] = None
    unit: Optional[str] = None
    spec: Optional[str] = None
    cost_price: Optional[float] = None
    sale_price: Optional[float] = None
    stock_qty: Optional[int] = None
    safety_stock: Optional[int] = None
    is_active: Optional[bool] = None


class ProductOut(ORMBase):
    id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    product_type: Literal["raw_material", "finished_good"] = "finished_good"
    unit: str
    spec: str
    cost_price: float
    avg_cost: float = 0.0
    sale_price: float
    stock_qty: int
    safety_stock: int
    is_active: bool
    created_at: datetime


# ---------- 客户 ----------
class CustomerBase(BaseModel):
    name: str
    contact: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    level: str = "C"
    credit_limit: float = 0.0
    remark: str = ""


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    level: Optional[str] = None
    credit_limit: Optional[float] = None
    remark: Optional[str] = None


class CustomerOut(ORMBase):
    id: int
    name: str
    contact: str
    phone: str
    email: str
    address: str
    level: str
    credit_limit: float
    remark: str
    owner: str = ""
    created_at: datetime


# ---------- 供应商 ----------
class SupplierBase(BaseModel):
    name: str
    contact: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    supply_category: str = ""
    remark: str = ""


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    supply_category: Optional[str] = None
    remark: Optional[str] = None


class SupplierOut(ORMBase):
    id: int
    name: str
    contact: str
    phone: str
    email: str
    address: str
    supply_category: str
    remark: str
    created_at: datetime


# ---------- 订单明细 ----------
class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    # 单据原文，用于记住「识别名 → 商品」的对应关系；只参与别名学习，不落明细表
    raw_name: str = ""


class OrderItemOut(ORMBase):
    id: int
    product_id: int
    product_name: str = ""
    quantity: int
    unit_price: float
    amount: float


# ---------- 销售订单 ----------
class SalesOrderCreate(BaseModel):
    customer_id: int
    order_date: Optional[datetime] = None
    discount_amount: float = 0.0
    tax_rate: float = Field(0.0, ge=0, le=100)  # 税率(%)，>0 时金额按价税合计拆分
    remark: str = ""
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = "manual"
    ocr_raw: str = ""
    raw_customer_name: str = ""
    items: list[OrderItemIn]


class SalesOrderUpdate(BaseModel):
    """草稿订单编辑：字段可选，未传的保持原值；只对 draft 状态生效。"""

    customer_id: Optional[int] = None
    order_date: Optional[datetime] = None
    discount_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    remark: Optional[str] = None
    photo_path: Optional[str] = None
    photo_name: Optional[str] = None
    photo_paths: Optional[list[PhotoPage]] = None
    ocr_status: Optional[str] = None
    ocr_raw: Optional[str] = None
    raw_customer_name: Optional[str] = None
    items: Optional[list[OrderItemIn]] = None


class SalesOrderOut(ORMBase):
    id: int
    order_no: str
    customer_id: int
    customer_name: str = ""
    status: str
    order_date: datetime
    total_amount: float
    discount_amount: float
    tax_rate: float = 0.0
    remark: str
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = ""
    ocr_raw: str = ""
    created_by: str
    items: list[OrderItemOut] = []


class StatusUpdate(BaseModel):
    # 销售：submit / approve / reject / confirm / complete / cancel
    # 采购：submit / approve / reject / order / receive / cancel
    action: str
    comment: str = ""  # 审批意见，驳回时建议填写


# ---------- 采购订单 ----------
class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    order_date: Optional[datetime] = None
    tax_rate: float = Field(0.0, ge=0, le=100)  # 税率(%)，>0 时金额按价税合计拆分
    remark: str = ""
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = "manual"
    ocr_raw: str = ""
    raw_supplier_name: str = ""
    items: list[OrderItemIn]


class PurchaseOrderOut(ORMBase):
    id: int
    order_no: str
    supplier_id: int
    supplier_name: str = ""
    status: str
    order_date: datetime
    total_amount: float
    tax_rate: float = 0.0
    remark: str
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = ""
    ocr_raw: str = ""
    created_by: str
    items: list[OrderItemOut] = []


class PurchaseOrderUpdate(BaseModel):
    """草稿采购单编辑：字段可选，未传的保持原值；只对 draft 状态生效。"""

    supplier_id: Optional[int] = None
    order_date: Optional[datetime] = None
    tax_rate: Optional[float] = None
    remark: Optional[str] = None
    photo_path: Optional[str] = None
    photo_name: Optional[str] = None
    photo_paths: Optional[list[PhotoPage]] = None
    ocr_status: Optional[str] = None
    ocr_raw: Optional[str] = None
    raw_supplier_name: Optional[str] = None
    items: Optional[list[OrderItemIn]] = None


# ---------- AI ----------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    answer: str
    source: str = "rule"  # rule / llm
    data: list[dict] = []
    tools_used: list[str] = []  # 本次回答调用的工具名，用于展示数据来源
    draft: Optional[dict] = None  # 开单预览：前端据此渲染「确认建单」卡片，AI 不自动提交


Token.model_rebuild()


# ---------- 收款与对账 ----------
class PaymentAllocationIn(BaseModel):
    order_id: int
    amount: float = Field(gt=0)


class PaymentCreate(BaseModel):
    customer_id: int
    amount: float = Field(gt=0)
    pay_date: Optional[datetime] = None
    method: str = "银行转账"
    reference: str = ""  # 银行流水号/回单号，便于对方查账
    remark: str = ""
    # 留空则按「先进先出」自动核销到未结清订单
    allocations: list[PaymentAllocationIn] = []


class PaymentOut(BaseModel):
    id: int
    payment_no: str
    customer_id: int
    customer_name: str = ""
    amount: float
    pay_date: datetime
    method: str
    reference: str
    remark: str
    status: str
    created_by: str
    allocations: list[dict] = []


class StatementCreate(BaseModel):
    customer_id: int
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


# ---------- 销售 / 采购退货 ----------
class ReturnItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)


class SalesReturnCreate(BaseModel):
    order_id: int
    reason: str = ""
    warehouse_id: Optional[int] = None  # 不传则落到退货仓/默认仓
    location_id: Optional[int] = None
    items: list[ReturnItemIn]


class PurchaseReturnCreate(BaseModel):
    order_id: int
    reason: str = ""
    warehouse_id: Optional[int] = None  # 退货出库仓；不传则默认仓
    items: list[ReturnItemIn]


class ReturnItemOut(ORMBase):
    id: int
    product_id: int
    product_name: str = ""
    product_sku: str = ""
    quantity: int
    unit_price: float
    amount: float


class ReturnOrderOut(BaseModel):
    id: int
    return_no: str
    order_id: int
    order_no: str = ""
    partner_id: int  # 销售退货为客户 id，采购退货为供应商 id
    partner_name: str = ""
    warehouse_id: Optional[int] = None
    warehouse_name: str = ""
    status: str
    total_amount: float
    reason: str
    created_by: str
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    items: list[ReturnItemOut] = []


# ---------- 审批 ----------
class ApprovalLogOut(ORMBase):
    id: int
    order_type: str
    order_id: int
    action: str
    operator: str
    role: str
    comment: str
    created_at: datetime


class PendingApprovalOut(BaseModel):
    order_type: str  # sales / purchase
    order_id: int
    order_no: str
    partner_name: str
    total_amount: float
    created_by: str
    order_date: datetime
    item_summary: str = ""
    status: str = "pending"


# ---------- 库存盘点 ----------
class StocktakeCreate(BaseModel):
    warehouse_id: int
    remark: str = ""


class StocktakeItemUpdate(BaseModel):
    item_id: int
    actual_qty: int = Field(ge=0)


class StocktakeSave(BaseModel):
    items: list[StocktakeItemUpdate]


class StocktakeAction(BaseModel):
    # complete=提交（有差异且开启审批时转待审批）/ approve=通过 / reject=驳回 / cancel=取消
    action: Literal["complete", "approve", "reject", "cancel"]
    remark: str = ""


class StocktakeItemOut(ORMBase):
    id: int
    product_id: int
    product_name: str = ""
    product_sku: str = ""
    location_id: int
    location_code: str = ""
    book_qty: int
    actual_qty: Optional[int] = None
    diff_qty: int


class StocktakeOut(BaseModel):
    id: int
    stocktake_no: str
    warehouse_id: int
    warehouse_name: str = ""
    status: str
    remark: str
    created_by: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    diff_count: int = 0
    diff_quantity: int = 0
    items: list[StocktakeItemOut] = []


# ---------- 应付与供应商对账 ----------
class SupplierPaymentAllocationIn(BaseModel):
    order_id: int
    amount: float = Field(gt=0)


class SupplierPaymentCreate(BaseModel):
    supplier_id: int
    amount: float = Field(gt=0)
    pay_date: Optional[datetime] = None
    method: str = "银行转账"
    reference: str = ""
    remark: str = ""
    # 留空则按「先进先出」自动核销到未结清入库单
    allocations: list[SupplierPaymentAllocationIn] = []


class SupplierPaymentOut(BaseModel):
    id: int
    payment_no: str
    supplier_id: int
    supplier_name: str = ""
    amount: float
    pay_date: datetime
    method: str
    reference: str
    remark: str
    status: str
    void_reason: str = ""
    created_by: str
    allocations: list[dict] = []


class SupplierStatementCreate(BaseModel):
    supplier_id: int
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


# ---------- 单据模板 ----------
DocType = Literal[
    "sales_order",
    "purchase_order",
    "inbound",
    "sales_return",
    "purchase_return",
    "stocktake",
    "statement",
    "payment",
]


class TemplateUpsert(BaseModel):
    doc_type: DocType
    name: str = Field(min_length=1, max_length=60)
    title: str = ""
    is_default: bool = False
    enabled: bool = True
    # company / logo / columns / signs / terms / paper 等版式细节统一放这里
    config: dict = Field(default_factory=dict)


class TemplateOut(ORMBase):
    id: int
    doc_type: str
    name: str
    title: str
    is_default: bool
    enabled: bool
    config: dict
    word_filename: str = ""
    updated_at: datetime


# ---------- 入库单 ----------
class InboundItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    batch_no: str = ""
    expiry_date: Optional[datetime] = None
    remark: str = ""
    # 单据原文，用于记住「识别名 → 商品」的对应关系；只参与别名学习，不落明细表
    raw_name: str = ""


class InboundCreate(BaseModel):
    supplier_id: int
    warehouse_id: int
    location_id: Optional[int] = None
    purchase_order_id: Optional[int] = None  # 仅作关联参考，不改变采购单状态
    receipt_date: Optional[datetime] = None
    remark: str = ""
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = "manual"
    ocr_raw: str = ""
    raw_supplier_name: str = ""
    items: list[InboundItemIn]


class InboundAction(BaseModel):
    action: Literal["confirm", "cancel"]
    remark: str = ""


class InboundItemOut(ORMBase):
    id: int
    product_id: int
    product_name: str = ""
    product_sku: str = ""
    quantity: int
    unit_price: float
    amount: float
    batch_no: str = ""
    remark: str = ""


class InboundOut(ORMBase):
    id: int
    inbound_no: str
    purchase_order_id: Optional[int] = None
    purchase_order_no: str = ""
    supplier_id: int
    supplier_name: str = ""
    warehouse_id: int
    warehouse_name: str = ""
    location_id: Optional[int] = None
    location_code: str = ""
    status: str
    receipt_date: datetime
    total_amount: float
    remark: str
    photo_path: str = ""
    photo_name: str = ""
    photo_paths: list[PhotoPage] = []
    ocr_status: str = ""
    ocr_raw: str = ""
    created_by: str
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    items: list[InboundItemOut] = []


class RecognizeOut(BaseModel):
    photo_path: str
    photo_name: str
    ocr_status: str
    raw_text: str = ""
    supplier_name: str = ""
    supplier_id: Optional[int] = None
    receipt_date: str = ""
    total_amount: float = 0.0
    items: list[dict] = []

# ---------- 智能核销 ----------
class PaymentMatchRequest(BaseModel):
    """收款核销建议请求：只计算建议，不写库。"""

    customer_id: int
    amount: float = Field(gt=0)
    pay_date: Optional[datetime] = None
    reference: str = Field("", max_length=60)


class SupplierPaymentMatchRequest(BaseModel):
    """付款核销建议请求：只计算建议，不写库。"""

    supplier_id: int
    amount: float = Field(gt=0)
    pay_date: Optional[datetime] = None
    reference: str = Field("", max_length=60)


# ---------- BOM ----------
class BomItemIn(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)
    remark: str = ""


class BomIn(BaseModel):
    product_id: int
    name: str = ""
    version: str = "V1.0"
    base_qty: float = Field(default=1.0, gt=0)
    status: str = "active"
    remark: str = ""
    items: list[BomItemIn] = Field(min_length=1, max_length=100)


# ---------- 生产订单 ----------
class ProductionCreate(BaseModel):
    product_id: int
    bom_id: Optional[int] = None
    quantity: int = Field(gt=0)
    warehouse_id: int
    plan_start: Optional[datetime] = None
    plan_end: Optional[datetime] = None
    remark: str = ""


class ProductionAction(BaseModel):
    action: Literal["release", "complete", "cancel"]
    remark: str = ""


class MaterialIssueItem(BaseModel):
    product_id: int
    quantity: float = Field(gt=0)


class MaterialIssue(BaseModel):
    items: list[MaterialIssueItem] = []  # 留空则按未领数量整单领料
    warehouse_id: Optional[int] = None  # 领料仓，不传则默认仓
    remark: str = ""


class ProcessScan(BaseModel):
    operator: str = ""
    remark: str = ""


class ProductionExpenseIn(BaseModel):
    category: str = "制造费用"
    amount: float = Field(gt=0)
    remark: str = ""


# ---------- 销售报价 ----------
class QuotationItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)


class QuotationCreate(BaseModel):
    customer_id: int
    valid_until: Optional[datetime] = None
    discount_amount: float = 0.0
    remark: str = ""
    items: list[QuotationItemIn] = Field(min_length=1, max_length=100)


class QuotationUpdate(BaseModel):
    customer_id: Optional[int] = None
    valid_until: Optional[datetime] = None
    discount_amount: Optional[float] = None
    remark: Optional[str] = None
    items: Optional[list[QuotationItemIn]] = None


class QuotationStatus(BaseModel):
    status: Literal["draft", "sent", "accepted", "rejected"]


# ---------- 采购申请 ----------
class RequisitionItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    est_price: float = Field(ge=0)
    remark: str = ""


class RequisitionCreate(BaseModel):
    needed_date: Optional[datetime] = None
    remark: str = ""
    items: list[RequisitionItemIn] = Field(min_length=1, max_length=100)


class RequisitionUpdate(BaseModel):
    needed_date: Optional[datetime] = None
    remark: Optional[str] = None
    items: Optional[list[RequisitionItemIn]] = None


class RequisitionAction(BaseModel):
    action: Literal["submit", "approve", "reject", "cancel"]
    comment: str = ""


class RequisitionConvert(BaseModel):
    supplier_id: int


# ---------- 库存调整单据 ----------
class AdjustmentItemIn(BaseModel):
    product_id: int
    quantity: int = Field(default=0, ge=0)
    unit_cost: float = Field(default=0.0, ge=0)


class AdjustmentCreate(BaseModel):
    adjust_type: Literal["gain", "loss", "cost"]
    warehouse_id: int
    reason: str = ""
    items: list[AdjustmentItemIn] = Field(min_length=1, max_length=100)


class AdjustmentAction(BaseModel):
    action: Literal["post", "cancel"]
    remark: str = ""


# ---------- 采购询价 ----------
class InquiryCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    deadline: Optional[datetime] = None
    remark: str = ""


class InquiryQuoteIn(BaseModel):
    supplier_id: int
    unit_price: float = Field(gt=0)
    remark: str = ""


class InquiryConvert(BaseModel):
    quote_id: int


# ---------- 发票管理 ----------
class InvoiceCreate(BaseModel):
    invoice_no: str
    direction: Literal["sales", "purchase"] = "sales"
    partner_name: str = ""
    order_no: str = ""
    amount: float = Field(0.0, ge=0)
    tax_rate: float = Field(0.0, ge=0, le=100)
    tax_amount: float = Field(0.0, ge=0)
    total_amount: float = Field(0.0, ge=0)
    issue_date: Optional[datetime] = None
    remark: str = ""


class InvoiceUpdate(BaseModel):
    invoice_no: Optional[str] = None
    direction: Optional[Literal["sales", "purchase"]] = None
    partner_name: Optional[str] = None
    order_no: Optional[str] = None
    amount: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    issue_date: Optional[datetime] = None
    remark: Optional[str] = None


class InvoiceAction(BaseModel):
    action: Literal["issue", "void", "certify"]


class InvoiceOut(ORMBase):
    id: int
    invoice_no: str
    direction: str
    partner_name: str
    order_no: str
    amount: float
    tax_rate: float
    tax_amount: float
    total_amount: float
    status: str
    certified: bool
    certified_at: Optional[datetime] = None
    reviewed: bool
    reviewed_by: str
    reviewed_at: Optional[datetime] = None
    issue_date: datetime
    remark: str
    created_by: str
    created_at: datetime

# ---------------- 销售出库单 ----------------

class OutboundItemIn(BaseModel):
    order_item_id: int
    quantity: int = Field(gt=0)


class OutboundCreate(BaseModel):
    sales_order_id: int
    # 为空表示按订单剩余未发数量整单发货
    items: list[OutboundItemIn] = []
    warehouse_id: Optional[int] = None
    logistics_company: str = ""
    logistics_no: str = ""
    remark: str = ""


class OutboundUpdate(BaseModel):
    logistics_company: Optional[str] = None
    logistics_no: Optional[str] = None
    ship_date: Optional[datetime] = None
    remark: Optional[str] = None


class OutboundAction(BaseModel):
    # ship=确认出库（扣库存）/ sign=登记签收 / cancel=取消并回库
    action: str
    signed_by: str = ""
    comment: str = ""

# ---------------- 审批规则 / 信用策略 ----------------

class ApprovalRuleIn(BaseModel):
    doc_type: str = "sales"
    name: str = ""
    enabled: bool = True
    min_amount: float = 0.0
    max_amount: Optional[float] = None
    level: int = 1
    permission: str = ""
    level_name: str = ""
    mode: str = "single"  # single=单人 / or=或签(任一) / counter=会签(全部)
    approvers: str = ""  # 指定审批人用户名（逗号分隔），or/counter 时生效
    conditions: dict = Field(default_factory=dict)  # 条件分支，如 {"credit_exceeded": true, "min_margin": 0.15}


class ApprovalSlaIn(BaseModel):
    remind_hours: int = Field(24, ge=1, le=720)
    escalate_hours: int | None = Field(None, ge=1, le=720)  # 空=不升级
    enabled: bool = True


class ApprovalDelegationIn(BaseModel):
    doc_type: str = "sales"
    delegator: str = ""  # 委托人用户名
    delegatee: str = ""  # 代理人用户名
    enabled: bool = True


class CreditConfigIn(BaseModel):
    # 形如 {"*": "warn", "A": "block"}；键为 * 表示全局默认
    policies: dict[str, str] = {}


class CreditOverrideCreate(BaseModel):
    order_id: int
    reason: str = ""


class CreditOverrideAction(BaseModel):
    action: str  # approve / reject
    comment: str = ""
# ---------------- 单据打印 ----------------

class PrintBatchIn(BaseModel):
    doc_type: str
    ids: list[int] = []
    template_id: Optional[int] = None
    copies: int = 1
    watermark: str = ""
    paper: str = "A4"
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    copy_labels: list[str] = []
    barcode: bool = True
