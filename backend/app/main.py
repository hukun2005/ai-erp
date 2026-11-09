from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .audit import AuditMiddleware
from .database import SessionLocal, ensure_schema
from .scheduler import scheduler
from .seed import seed
from .services import gl as gl_service
from .routers import (
    adjustments,
    ai,
    approval_rules,
    approvals,
    audit,
    auth,
    automations,
    backup,
    bank,
    bom,
    credit,
    credit_admin,
    dashboard,
    data_exchange,
    finance,
    inbox,
    inbounds,
    inquiries,
    inventory,
    invoices,
    ledger,
    notifications,
    outbounds,
    partners,
    payables,
    period,
    pipeline,
    print_jobs,
    production,
    products,
    public_statements,
    purchase,
    quotes,
    receivables,
    reconciliation,
    reports,
    requisitions,
    returns,
    risk,
    sales,
    search,
    stocktakes,
    tax,
    templates,
    transfers,
    uploads,
    users,
    vision,
    warehouses,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    db = SessionLocal()
    try:
        seed(db)
        gl_service.seed_accounts(db)
    finally:
        db.close()
    # 后台巡检与自动化规则求值；没有它，没人动库存时系统永远不预警
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="AI 辅助的企业资源管理系统（ERP）后端服务",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(products.router)
app.include_router(vision.router)
app.include_router(partners.router)
app.include_router(credit.router)
app.include_router(inventory.router)
app.include_router(warehouses.router)
app.include_router(transfers.router)
app.include_router(sales.router)
app.include_router(purchase.router)
app.include_router(bom.router)
app.include_router(production.router)
app.include_router(quotes.router)
app.include_router(requisitions.router)
app.include_router(inquiries.router)
app.include_router(adjustments.router)
app.include_router(reports.router)
app.include_router(approvals.router)
app.include_router(approval_rules.router)
app.include_router(credit_admin.router)
app.include_router(returns.router)
app.include_router(stocktakes.router)
app.include_router(inbounds.router)
app.include_router(outbounds.router)
app.include_router(print_jobs.router)
app.include_router(templates.router)
app.include_router(uploads.router)
app.include_router(receivables.router)
app.include_router(payables.router)
app.include_router(reconciliation.router)
app.include_router(invoices.router)
app.include_router(public_statements.router)
app.include_router(dashboard.router)
app.include_router(data_exchange.router)
app.include_router(finance.router)
app.include_router(ledger.router)
app.include_router(period.router)
app.include_router(pipeline.router)
app.include_router(tax.router)
app.include_router(bank.router)
app.include_router(ai.router)
app.include_router(audit.router)
app.include_router(notifications.router)
app.include_router(inbox.router)
app.include_router(automations.router)
app.include_router(risk.router)
app.include_router(search.router)
app.include_router(backup.router)


@app.get("/api/health", tags=["系统"])
def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "llm_enabled": settings.llm_enabled,
        "credit_policy": settings.CREDIT_POLICY,
        "scheduler": scheduler.jobs if settings.SCHEDULER_ENABLED else [],
    }


# ---- 桌面版静态资源托管 ----
# 打包后前端 dist 随安装包一起分发，由后端同源提供；前端用的是 hash 路由，
# 所以只需要把 index.html 兜底返回，不必做路由重写。
_frontend_dir = settings.FRONTEND_DIR
if _frontend_dir.is_dir():
    _assets_dir = _frontend_dir / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        if full_path.startswith(("api/", "uploads/", "docs", "redoc")):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (_frontend_dir / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_frontend_dir.resolve()):
                return FileResponse(candidate)
        return FileResponse(_frontend_dir / "index.html", headers={"Cache-Control": "no-cache"})
