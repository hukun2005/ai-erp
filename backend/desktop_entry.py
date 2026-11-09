"""桌面版后端入口。

由 Electron 主进程以子进程方式启动，不要把这里当成开发入口使用（开发仍用
`uvicorn app.main:app --reload`）。启动参数全部走环境变量：

    AI_ERP_DATA_DIR  数据目录（数据库、上传文件、.env、日志）
    AI_ERP_HOST      监听地址，本机模式 127.0.0.1，服务端模式 0.0.0.0
    AI_ERP_PORT      监听端口
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _prepare_data_dir() -> Path:
    data_dir = Path(os.getenv("AI_ERP_DATA_DIR") or (Path.home() / "AI-ERP" / "data"))
    data_dir = data_dir.expanduser().resolve()
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    os.environ["AI_ERP_DATA_DIR"] = str(data_dir)
    return data_dir


def _configure_logging(data_dir: Path) -> None:
    """日志同时写文件和 stdout：文件供客户导出诊断包，stdout 供 Electron 实时捕获。"""
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            data_dir / "logs" / "backend.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    ]
    # 打包成 GUI 程序时 stdout 可能为 None，不能直接挂 StreamHandler
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> int:
    data_dir = _prepare_data_dir()
    _configure_logging(data_dir)

    # 冻结后包内路径是 _MEIPASS，源码运行时是 backend 目录
    sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))))

    import uvicorn

    from app.main import app
    from app.config import settings

    host = os.getenv("AI_ERP_HOST", "127.0.0.1")
    port = int(os.getenv("AI_ERP_PORT", "8790"))

    logging.getLogger("erp.desktop").info(
        "AI-ERP 后端启动：http://%s:%s 数据目录=%s 数据库=%s 前端目录=%s",
        host,
        port,
        data_dir,
        settings.DATABASE_URL,
        settings.FRONTEND_DIR,
    )

    # log_config=None 让 uvicorn 沿用上面配好的根日志，不另起一套 handler
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.getLogger("erp.desktop").exception("AI-ERP 后端启动失败")
        raise
