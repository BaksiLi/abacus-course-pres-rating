"""
配置示例（不包含敏感信息）

用法：复制为 config.py 并按需修改，或优先通过环境变量覆盖。
生产环境推荐仅使用环境变量（例如 Docker 或部署平台的 Secret 管理）。
"""

import os

# ============= 课程信息 =============
# 示例：export COURSE_NAME="My Course Ratings"
COURSE_NAME = os.getenv("COURSE_NAME", "Course Presentation Ratings")
# 示例：export COURSE_INSTITUTION="My University"
COURSE_INSTITUTION = os.getenv("COURSE_INSTITUTION", "Your Institution")

# ============= 安全设置 =============
# 强烈建议通过环境变量设置，且使用强随机值。
# 示例：export ADMIN_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
# 示例：export SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
SESSION_SECRET = os.getenv("SESSION_SECRET", "please-set-session-secret")

# ============= 系统设置 =============
# 锁过期时间（分钟）示例：export LOCK_EXPIRY_MINUTES="120"
LOCK_EXPIRY_MINUTES = int(os.getenv("LOCK_EXPIRY_MINUTES", "120"))
# Cookie 名称（一般无需修改）
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "abacus_token")

# ============= 评分设置 =============
# 评分权重（百分制），可根据需要调整
SCORE_WEIGHTS = {
    "solve": 4,      # 解决程度：满分4分（40%）
    "logic": 3,      # 答辩逻辑性：满分3分（30%）
    "analysis": 3,   # 分析与总结：满分3分（30%）
}

# ============= 默认组别设置 =============
# 新建场次时的默认组别配置
DEFAULT_GROUPS = [
    {"name": "1", "scorable": True},
    {"name": "2", "scorable": True},
    {"name": "3", "scorable": True},
    {"name": "4", "scorable": True},
    {"name": "5", "scorable": True},
    {"name": "6", "scorable": True},
    {"name": "7", "scorable": True},
    {"name": "8", "scorable": True},
    {"name": "9", "scorable": True},
]

