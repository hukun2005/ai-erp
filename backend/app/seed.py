"""初始化演示数据：首次启动时自动写入。"""
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import (
    Customer,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    SalesOrder,
    SalesOrderItem,
    StockBalance,
    StockLog,
    StorageLocation,
    Supplier,
    User,
    Warehouse,
)
from .security import hash_password

random.seed(2026)


def seed(db: Session) -> None:
    if db.query(User).count() > 0:
        return

    # ---- 用户（每个角色一个演示账号）----
    db.add_all(
        [
            User(username="admin", hashed_password=hash_password("admin123"), full_name="系统管理员", role="admin"),
            User(username="sales01", hashed_password=hash_password("123456"), full_name="张销售", role="sales"),
            User(username="sales02", hashed_password=hash_password("123456"), full_name="陈销售", role="sales"),
            User(username="buyer01", hashed_password=hash_password("123456"), full_name="李采购", role="purchaser"),
            User(username="wh01", hashed_password=hash_password("123456"), full_name="王仓管", role="warehouse"),
            User(username="fin01", hashed_password=hash_password("123456"), full_name="赵财务", role="finance"),
        ]
    )

    # ---- 商品 ----
    product_rows = [
        ("SKU-1001", "工业级螺丝刀套装", "五金工具", "套", 45.0, 89.0, 320, 40),
        ("SKU-1002", "不锈钢扳手 12寸", "五金工具", "把", 18.0, 39.0, 260, 50),
        ("SKU-1003", "电动螺丝批", "五金工具", "台", 120.0, 239.0, 90, 25),
        ("SKU-2001", "A4 复印纸 70g", "办公耗材", "箱", 95.0, 138.0, 180, 60),
        ("SKU-2002", "中性签字笔 0.5mm", "办公耗材", "盒", 22.0, 45.0, 400, 80),
        ("SKU-2003", "墨盒 HP 802", "办公耗材", "个", 68.0, 129.0, 60, 30),
        ("SKU-3001", "工业防护手套", "劳保用品", "副", 6.5, 15.0, 800, 150),
        ("SKU-3002", "安全帽 ABS", "劳保用品", "顶", 19.0, 42.0, 150, 60),
        ("SKU-3003", "防尘口罩 N95", "劳保用品", "盒", 35.0, 78.0, 95, 60),
        ("SKU-4001", "LED 工矿灯 100W", "电子设备", "盏", 145.0, 288.0, 70, 20),
        ("SKU-4002", "数字万用表", "电子设备", "台", 88.0, 168.0, 45, 15),
        ("SKU-4003", "PVC 绝缘胶带", "电子设备", "卷", 3.2, 8.0, 1200, 200),
        ("SKU-5001", "仓储货架 4层", "仓储设备", "组", 320.0, 599.0, 35, 10),
        ("SKU-5002", "手动液压搬运车", "仓储设备", "台", 680.0, 1280.0, 12, 5),
    ]
    products: list[Product] = []
    for sku, name, category, unit, cost, sale, stock, safety in product_rows:
        p = Product(
            sku=sku, name=name, category=category, unit=unit, spec="标准款",
            product_type="finished_good",
            cost_price=cost, sale_price=sale, stock_qty=stock, safety_stock=safety,
        )
        db.add(p)
        products.append(p)
    # 原料商品：只采购/领用，不参与销售演示
    raw_rows = [
        ("RM-1001", "冷轧钢板 Q235", "五金工具", "张", 42.0, 0.0, 400, 80),
        ("RM-2001", "原木浆纸张 70g", "办公耗材", "卷", 58.0, 0.0, 300, 60),
        ("RM-3001", "丁腈橡胶原料", "劳保用品", "桶", 26.0, 0.0, 200, 40),
        ("RM-4001", "LED 灯珠 100W", "电子设备", "盒", 96.0, 0.0, 150, 30),
        ("RM-5001", "冷轧型钢 4层", "仓储设备", "根", 180.0, 0.0, 100, 20),
    ]
    raw_products: list[Product] = []
    for sku, name, category, unit, cost, sale, stock, safety in raw_rows:
        p = Product(
            sku=sku, name=name, category=category, unit=unit, spec="原料",
            product_type="raw_material",
            cost_price=cost, sale_price=sale, stock_qty=stock, safety_stock=safety,
        )
        db.add(p)
        raw_products.append(p)
    all_products = products + raw_products
    # 库存先清零，等销售数据生成后统一回填，保证演示数据库存不为负
    for p in all_products:
        p.stock_qty = 0

    # ---- 客户 ----
    customer_rows = [
        ("华东机电有限公司", "王经理", "13800138001", "A", 500000),
        ("南方建设集团", "李工", "13900139002", "A", 300000),
        ("恒通物流", "赵主管", "13700137003", "B", 150000),
        ("金鼎制造", "陈总", "13600136004", "B", 200000),
        ("宏达五金店", "刘老板", "13500135005", "C", 50000),
        ("新时代办公", "周主任", "13400134006", "C", 80000),
        ("蓝海电子", "孙经理", "13300133007", "B", 120000),
        ("远航仓储", "吴经理", "13200132008", "C", 60000),
    ]
    customers: list[Customer] = []
    for index, (name, contact, phone, level, credit) in enumerate(customer_rows):
        c = Customer(
            name=name, contact=contact, phone=phone, level=level, credit_limit=credit,
            email=f"{phone}@example.com", address="上海市浦东新区",
            # 前 5 个客户归张销售，后 3 个归陈销售，用于演示数据范围隔离
            owner="sales02" if index >= 5 else "sales01",
        )
        db.add(c)
        customers.append(c)

    # ---- 供应商 ----
    supplier_rows = [
        ("鼎力五金制造", "五金工具", "钱经理", "13100131001"),
        ("优品办公供应链", "办公耗材", "孙经理", "13100131002"),
        ("安捷劳保用品", "劳保用品", "李经理", "13100131003"),
        ("光远电子科技", "电子设备", "郑经理", "13100131004"),
        ("中仓仓储设备", "仓储设备", "王经理", "13100131005"),
    ]
    suppliers: list[Supplier] = []
    for name, category, contact, phone in supplier_rows:
        s = Supplier(name=name, supply_category=category, contact=contact, phone=phone,
                     email=f"{phone}@example.com", address="江苏省苏州市")
        db.add(s)
        suppliers.append(s)

    # ---- 仓库与库位 ----
    warehouses = {
        "MAIN": Warehouse(
            code="MAIN", name="主仓（上海）", address="上海市浦东新区仓储路 88 号",
            manager="王仓管", is_default=True,
        ),
        "EAST": Warehouse(
            code="EAST", name="华东分仓（苏州）", address="江苏省苏州市工业园区",
            manager="李仓管",
        ),
        "RTN": Warehouse(
            code="RTN", name="退货仓", address="上海市浦东新区退货处理中心",
            manager="赵仓管",
        ),
    }
    db.add_all(list(warehouses.values()))
    db.flush()
    location_rows = [
        ("MAIN", "A-01", "A 区重型货架"),
        ("MAIN", "A-02", "A 区整箱备货位"),
        ("MAIN", "B-01", "B 区零散拣货位"),
        ("EAST", "E-01", "E 区整托位"),
        ("EAST", "E-02", "E 区零散位"),
        ("RTN", "R-01", "退货暂存位"),
    ]
    locations: dict[tuple[str, str], StorageLocation] = {}
    for wh_code, code, name in location_rows:
        loc = StorageLocation(warehouse_id=warehouses[wh_code].id, code=code, name=name)
        db.add(loc)
        locations[(wh_code, code)] = loc

    db.flush()

    # ---- 近 30 天销售订单 ----
    statuses_pool = ["completed"] * 6 + ["confirmed"] * 3 + ["cancelled"]
    order_seq = 0
    sold: dict[int, int] = {}
    for day_offset in range(29, -1, -1):
        day = datetime.now() - timedelta(days=day_offset)
        # 周末订单少一点；今天固定留 2~3 单，保证看板有当日数据
        if day_offset == 0:
            count = random.randint(2, 3)
        else:
            count = random.randint(0, 2) if day.weekday() >= 5 else random.randint(1, 4)
        for _ in range(count):
            order_seq += 1
            customer = random.choice(customers[:5]) if random.random() < 0.7 else random.choice(customers)
            status = random.choice(statuses_pool)
            order = SalesOrder(
                order_no=f"SO{day.strftime('%Y%m%d')}{order_seq:04d}",
                customer_id=customer.id,
                order_date=day.replace(hour=random.randint(9, 17), minute=random.randint(0, 59)),
                status=status,
                remark="演示数据",
                created_by=customer.owner or "sales01",
            )
            total = 0.0
            for _ in range(random.randint(1, 3)):
                product = random.choice(products)
                qty = random.randint(1, 25)
                amount = round(qty * product.sale_price, 2)
                total += amount
                order.items.append(
                    SalesOrderItem(product_id=product.id, quantity=qty,
                                   unit_price=product.sale_price, amount=amount)
                )
                if status in ("confirmed", "completed"):
                    sold[product.id] = sold.get(product.id, 0) + qty
            order.total_amount = round(total, 2)
            db.add(order)

    # ---- 采购订单 ----
    po_seq = 0
    for day_offset in (10, 7, 3, 1):
        po_seq += 1
        day = datetime.now() - timedelta(days=day_offset)
        supplier = random.choice(suppliers)
        status = "received" if day_offset > 3 else "ordered"
        order = PurchaseOrder(
            order_no=f"PO{day.strftime('%Y%m%d')}{po_seq:04d}",
            supplier_id=supplier.id,
            order_date=day,
            status=status,
            remark="演示数据",
            created_by="buyer01" if po_seq <= 3 else "admin",
        )
        total = 0.0
        candidates = [p for p in raw_products if p.category == supplier.supply_category] or raw_products
        for product in random.sample(candidates, k=min(len(candidates), random.randint(1, 2))):
            qty = random.randint(20, 120)
            amount = round(qty * product.cost_price, 2)
            total += amount
            order.items.append(
                PurchaseOrderItem(product_id=product.id, quantity=qty,
                                  unit_price=product.cost_price, amount=amount)
            )
        order.total_amount = round(total, 2)
        db.add(order)

    # ---- 回填库存：按 30 天销量推算，保留 2 个低库存商品用于演示预警 ----
    for p in all_products:
        p.stock_qty = int(sold.get(p.id, 0) * 1.2) + p.safety_stock + random.randint(10, 40)
    demo_low = [products[9], products[13]]
    for p in demo_low:
        p.stock_qty = max(int(p.safety_stock * 0.5), 1)

    # ---- 把商品库存拆分建账到仓库/库位，并写入期初库存流水 ----
    def book(product: Product, wh_code: str, loc_code: str, qty: int) -> None:
        if qty <= 0:
            return
        loc = locations[(wh_code, loc_code)]
        db.add(
            StockBalance(
                product_id=product.id,
                warehouse_id=loc.warehouse_id,
                location_id=loc.id,
                quantity=qty,
            )
        )
        db.add(
            StockLog(
                product_id=product.id,
                warehouse_id=loc.warehouse_id,
                location_id=loc.id,
                change_type="in",
                biz_type="期初建账",
                ref_no="INIT",
                change_qty=qty,
                stock_before=0,
                stock_after=qty,
                reason="系统初始化导入库存",
                created_by="admin",
            )
        )

    for index, p in enumerate(all_products):
        total = p.stock_qty
        if total <= 0:
            continue
        # 每 3 个商品有 1 个在华东分仓也有库存，用于演示跨仓库扣减
        east_qty = int(total * 0.25) if index % 3 == 0 else 0
        main_qty = total - east_qty
        shelf_qty = int(main_qty * 0.7)
        book(p, "MAIN", "A-01", shelf_qty)
        book(p, "MAIN", "B-01", main_qty - shelf_qty)
        book(p, "EAST", "E-01", east_qty)

    # ---- 2 张草稿销售单，方便演示状态流转 ----
    for i in range(2):
        customer = customers[i]
        order = SalesOrder(
            order_no=f"SO{datetime.now().strftime('%Y%m%d')}9{i + 1:03d}",
            customer_id=customer.id,
            order_date=datetime.now(),
            status="draft",
            remark="待确认草稿",
            created_by="sales01",
        )
        product = products[i * 3]
        qty = 10
        amount = round(qty * product.sale_price, 2)
        order.items.append(SalesOrderItem(product_id=product.id, quantity=qty,
                                          unit_price=product.sale_price, amount=amount))
        order.total_amount = amount
        db.add(order)

    db.commit()
    print("[seed] 演示数据初始化完成，默认账号 admin / admin123")
