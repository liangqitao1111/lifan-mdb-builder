"""理反 — 统一日志模块

- 基于标准 logging + RotatingFileHandler（5MB × 5 份轮转）
- 日志目录：%APPDATA%\lifan\logs\lizheng_review.log（安装目录只读时日志不丢失）
- 提供 get_logger() / log_error / log_warning / log_info 统一入口，
  收敛 save_manager / connection / updater / verify 内多份 _log_error 拷贝。
"""
import logging
import os
import threading
from logging.handlers import RotatingFileHandler

APP_DIR = 'lifan'
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_log_dir = None
_logger = None
_lock = threading.Lock()


def get_log_dir():
    """返回日志目录（默认 %APPDATA%\lifan\logs，可被 set_log_dir 覆盖）"""
    global _log_dir
    if _log_dir is None:
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        _log_dir = os.path.join(base, APP_DIR, 'logs')
    try:
        os.makedirs(_log_dir, exist_ok=True)
    except Exception:
        pass
    return _log_dir


def set_log_dir(path):
    """测试用：重定向日志目录（必须先于 get_logger 首次调用）"""
    global _log_dir, _logger
    with _lock:
        _log_dir = path
        _logger = None  # 触发重建


def get_logger(name='lifan'):
    """返回统一 logger（线程安全，仅初始化一次）"""
    global _logger
    with _lock:
        if _logger is not None:
            return _logger
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            handler = None
            try:
                handler = RotatingFileHandler(
                    os.path.join(get_log_dir(), 'lizheng_review.log'),
                    maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding='utf-8')
            except Exception:
                handler = logging.NullHandler()  # 目录不可写时兜底，不抛错
            fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                                    datefmt='%Y-%m-%d %H:%M:%S')
            handler.setFormatter(fmt)
            logger.addHandler(handler)
            logger.propagate = False
        _logger = logger
        return logger


def log_error(msg):
    get_logger().error(str(msg))


def log_warning(msg):
    get_logger().warning(str(msg))


def log_info(msg):
    get_logger().info(str(msg))
