"""进程内定时任务调度器。

没有它，库存预警只在「有人动了库存」的那一刻触发：某个商品卖到安全线以下后
再没人操作，系统就永远不会提醒。调度器补上这半拍——按固定周期主动巡检，
顺便把自动化规则也跑了。

实现上刻意保持轻量：一个 asyncio 后台任务 + 每任务独立的数据库会话，
不引入额外依赖，也不和业务请求共享事务。
"""
import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

from .config import settings
from .database import SessionLocal

logger = logging.getLogger(__name__)


class Scheduler:
    """极简周期调度器：注册任务 → start() → stop()。"""

    def __init__(self) -> None:
        self._jobs: list[dict] = []
        self._task: asyncio.Task | None = None
        self._stopping = False

    def every(self, name: str, minutes: int, handler) -> None:
        """注册一个按分钟周期执行的任务。"""
        if minutes <= 0:
            raise ValueError(f"任务「{name}」的周期必须大于 0 分钟")
        self._jobs.append(
            {
                "name": name,
                "interval": timedelta(minutes=minutes),
                "handler": handler,
                "next_run": datetime.now(),
            }
        )

    @property
    def jobs(self) -> list[str]:
        return [job["name"] for job in self._jobs]

    def start(self) -> None:
        """在事件循环内启动；重复调用是安全的。"""
        if not settings.SCHEDULER_ENABLED:
            logger.info("定时调度器已禁用（SCHEDULER_ENABLED=0）")
            return
        if self._task is not None or not self._jobs:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())
        logger.info("定时调度器已启动：%s", "、".join(self.jobs))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping = True
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        tick = max(settings.SCHEDULER_TICK_SECONDS, 5)
        while not self._stopping:
            await asyncio.sleep(tick)
            now = datetime.now()
            for job in self._jobs:
                if job["next_run"] > now:
                    continue
                job["next_run"] = now + job["interval"]
                # 数据库操作是同步的，放到线程里跑，避免阻塞请求处理
                await asyncio.to_thread(self._run, job)

    def _run(self, job: dict) -> None:
        db = SessionLocal()
        try:
            job["handler"](db)
        except Exception:  # noqa: BLE001 单个任务失败不能拖垮调度器
            logger.exception("定时任务执行失败：%s", job["name"])
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 回滚失败只需记录
                logger.exception("定时任务回滚失败：%s", job["name"])
        finally:
            db.close()


def _stock_alert_scan(db) -> None:
    """定时巡检库存：把低于安全线的商品交给预警服务（冷却去重由它负责）。"""
    from .services import alerts, analytics

    products = analytics.low_stock_products(db)
    if not products:
        return
    alerts.check_and_notify(
        db,
        [product.id for product in products],
        username="系统巡检",
        scene="定时库存巡检",
    )


def _automation_scan(db) -> None:
    from .services import automation

    results = automation.run_due(db)
    if results:
        logger.info(
            "自动化规则执行 %d 条：%s",
            len(results),
            "、".join(f"{row['rule_name']}({row['status']})" for row in results),
        )


def _approval_sla_scan(db) -> None:
    """审批超时巡检：到点提醒或升级，避免单据卡在待审批没人管。"""
    from .services import approval_sla

    result = approval_sla.scan(db)
    if result["reminded"] or result["escalated"]:
        logger.info(
            "审批超时巡检：待审批 %d 条，提醒 %d 条，升级 %d 条",
            result["checked"],
            result["reminded"],
            result["escalated"],
        )


def _auto_backup(db) -> None:
    """按配置周期自动备份；距上次备份不足周期则跳过，避免启动即重复备份。"""
    from .services import backup

    interval = timedelta(hours=max(settings.BACKUP_AUTO_INTERVAL_HOURS, 1))
    last = backup.last_backup_at()
    if last is not None and datetime.now() - last < interval:
        return
    backup.create_backup()
    logger.info("数据自动备份完成")


def build_scheduler() -> Scheduler:
    """装配默认任务；周期都来自配置，部署时可改 .env 调整。"""
    scheduler = Scheduler()
    scheduler.every("库存定时巡检", settings.ALERT_SCAN_MINUTES, _stock_alert_scan)
    scheduler.every("自动化规则求值", settings.AUTOMATION_INTERVAL_MINUTES, _automation_scan)
    scheduler.every("审批超时巡检", max(settings.APPROVAL_SLA_SCAN_MINUTES, 5), _approval_sla_scan)
    if settings.BACKUP_AUTO_ENABLED:
        scheduler.every(
            "数据自动备份", max(settings.BACKUP_AUTO_INTERVAL_HOURS, 1) * 60, _auto_backup
        )
    return scheduler


scheduler = build_scheduler()
