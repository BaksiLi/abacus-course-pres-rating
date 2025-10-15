"""
工具函数模块
提供通用的辅助功能
"""
from functools import wraps
from typing import Callable, Optional
from fastapi import HTTPException
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app/logs/system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def require_admin(admin_key: str):
    """
    管理员权限装饰器
    
    使用示例：
    @require_admin(ADMIN_KEY)
    def some_admin_function(key: str):
        pass
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = kwargs.get('key') or (args[0] if args else None)
            if key != admin_key:
                logger.warning(f"未授权访问尝试: {func.__name__}")
                raise HTTPException(status_code=403, detail="需要管理员密钥访问")
            return func(*args, **kwargs)
        return wrapper
    return decorator


def log_operation(operation_type: str):
    """
    操作日志装饰器
    
    记录重要操作到日志
    """
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger.info(f"[{operation_type}] 开始执行: {func.__name__}")
            try:
                result = await func(*args, **kwargs)
                logger.info(f"[{operation_type}] 执行成功: {func.__name__}")
                return result
            except Exception as e:
                logger.error(f"[{operation_type}] 执行失败: {func.__name__} - {str(e)}")
                raise
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger.info(f"[{operation_type}] 开始执行: {func.__name__}")
            try:
                result = func(*args, **kwargs)
                logger.info(f"[{operation_type}] 执行成功: {func.__name__}")
                return result
            except Exception as e:
                logger.error(f"[{operation_type}] 执行失败: {func.__name__} - {str(e)}")
                raise
        
        # 判断是否为异步函数
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


def validate_score(score: Optional[float], min_val: float = 0.0, max_val: float = 100.0) -> bool:
    """
    验证评分是否在有效范围内
    """
    if score is None:
        return True
    return min_val <= score <= max_val


def format_datetime(dt: Optional[datetime]) -> str:
    """
    格式化日期时间
    """
    if dt is None:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except (ValueError, TypeError):
            return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value: any, default: float = 0.0) -> float:
    """
    安全地转换为浮点数
    """
    try:
        return float(value) if value is not None and value != "" else default
    except (ValueError, TypeError):
        return default


def sanitize_group_name(name: str) -> str:
    """
    清理组别名称（去除危险字符）
    """
    if not name:
        return ""
    # 移除潜在危险字符
    dangerous_chars = ['<', '>', '"', "'", ';', '--', '/*', '*/', 'script']
    sanitized = name.strip()
    for char in dangerous_chars:
        sanitized = sanitized.replace(char, '')
    return sanitized[:50]  # 限制长度


def calculate_statistics(scores: list[float]) -> dict:
    """
    计算评分统计数据
    """
    if not scores:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "std_dev": 0.0,
            "min": 0.0,
            "max": 0.0
        }
    
    import statistics
    
    return {
        "count": len(scores),
        "mean": statistics.mean(scores),
        "median": statistics.median(scores),
        "std_dev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "min": min(scores),
        "max": max(scores)
    }


class AuditLogger:
    """
    审计日志记录器
    """
    @staticmethod
    def log_score_submission(rater: str, target_count: int, ip_address: str = None):
        """记录评分提交"""
        logger.info(f"[AUDIT] 评分提交 - 评分者: {rater}, 目标数: {target_count}, IP: {ip_address}")
    
    @staticmethod
    def log_admin_action(action: str, target: str, admin_ip: str = None):
        """记录管理员操作"""
        logger.warning(f"[AUDIT] 管理员操作 - 动作: {action}, 目标: {target}, IP: {admin_ip}")
    
    @staticmethod
    def log_unlock(rater: str, unlocked_by: str = "admin"):
        """记录解锁操作"""
        logger.info(f"[AUDIT] 解锁操作 - 组别: {rater}, 操作者: {unlocked_by}")

