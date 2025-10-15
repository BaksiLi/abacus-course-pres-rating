from datetime import datetime
from typing import Dict, List, Optional
import sys
import os

# 添加项目根目录到 Python 路径，以便导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config import (
        ADMIN_KEY,
        COURSE_NAME,
        COURSE_INSTITUTION,
        LOCK_EXPIRY_MINUTES,
        SESSION_COOKIE_NAME,
        SESSION_SECRET,
    )
except Exception:
    # 允许无 config.py 情况，退回到环境变量
    ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
    COURSE_NAME = os.getenv("COURSE_NAME", "Course Presentation Ratings")
    COURSE_INSTITUTION = os.getenv("COURSE_INSTITUTION", "")
    LOCK_EXPIRY_MINUTES = int(os.getenv("LOCK_EXPIRY_MINUTES", "120"))
    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "abacus_token")
    SESSION_SECRET = os.getenv("SESSION_SECRET", "please-set-session-secret")

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from itsdangerous import URLSafeSerializer
import secrets
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .storage import (
    ScoreRecord,
    _active_session_id,
    acquire_lock,
    add_group,
    compute_averages,
    create_session,
    delete_group,
    delete_session,
    delete_submissions_by_rater,
    detect_score_anomalies,
    get_all_groups,
    get_all_scores,
    get_conn,
    get_existing_submission,
    get_scores_by_rater,
    list_sessions,
    get_presentation_order,
    init_db,
    insert_submission,
    list_targets_for_rater,
    save_presentation_order,
    release_lock,
    reset_all,
    set_active_session,
    toggle_group_scorable,
)


app = FastAPI(title="Abacus to Quantum - Scoring")


# Templates
templates_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

_session_secret = (
    secrets.token_urlsafe(48)
    if not SESSION_SECRET or SESSION_SECRET == "please-set-session-secret"
    else SESSION_SECRET
)
SESSION_SIGNER = URLSafeSerializer(_session_secret)
ADMIN_SESSION_COOKIE = "abacus_admin"


def render_template(name: str, **ctx: Dict) -> HTMLResponse:
    template = templates_env.get_template(name)
    # 自动注入全局配置到所有模板
    ctx.setdefault('course_name', COURSE_NAME)
    ctx.setdefault('course_institution', COURSE_INSTITUTION)
    return HTMLResponse(template.render(**ctx))


@app.on_event("startup")
def _startup() -> None:
    init_db()


def get_or_create_token(response: Response, token_cookie: Optional[str]) -> str:
    if token_cookie:
        try:
            SESSION_SIGNER.loads(token_cookie)
            return token_cookie
        except Exception:
            pass
    token = SESSION_SIGNER.dumps({"created": datetime.utcnow().isoformat()})
    response.set_cookie(SESSION_COOKIE_NAME, token, max_age=60 * 60 * 24 * 7, httponly=True)
    return token


def _set_admin_session(response: Response) -> None:
    token = SESSION_SIGNER.dumps({"admin": True, "created": datetime.utcnow().isoformat()})
    response.set_cookie(ADMIN_SESSION_COOKIE, token, max_age=60 * 60 * 4, httponly=True)


def _is_admin_session(admin_cookie: Optional[str]) -> bool:
    if not admin_cookie:
        return False
    try:
        data = SESSION_SIGNER.loads(admin_cookie)
        return bool(data.get("admin"))
    except Exception:
        return False


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    from .storage import get_conn, _active_session_id
    
    # Get active session info
    session_name = None
    session_topic = None
    with get_conn() as conn:
        sid = _active_session_id(conn)
        row = conn.execute("SELECT name FROM sessions WHERE id=?", (sid,)).fetchone()
        if row:
            session_name = row["name"]
        # Try to get session topic from settings
        topic_row = conn.execute("SELECT value FROM settings WHERE key='session_topic'").fetchone()
        if topic_row:
            session_topic = topic_row[0]
    
    # Get presentation order and filter preview to only scorable groups
    all_groups = get_all_groups()
    scorable_names = {g["name"] for g in all_groups if g.get("scorable")}
    presentation_order = get_presentation_order()
    filtered_order = [g for g in (presentation_order or []) if g in scorable_names]
    presentation_with_idx = [(i + 1, g) for i, g in enumerate(filtered_order)]
    
    # Homepage rater select should include ALL groups from DB; no hardcoded extra options
    rater_groups = [g["name"] for g in all_groups]
    
    return render_template(
        "index.html",
        rater_groups=rater_groups,
        session_name=session_name,
        session_topic=session_topic,
        presentation_order=presentation_with_idx,
    )


@app.get("/start", response_class=HTMLResponse)
def start(
    rater: str,
    request: Request,
    response: Response,
    token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE),
) -> HTMLResponse:
    # If rater is "教学组", require admin session to access admin panel
    if rater == "教学组":
        if _is_admin_session(admin_session):
            return RedirectResponse(url="/admin")
        return render_template("message.html", title="访问受限", message="需要管理员登录。")

    token_value = get_or_create_token(response, token)
    
    is_admin_unlock = False  # URL unlock removed

    # This logic is no longer needed as there's no separate rater_name field
    # if rater == "教学组" and is_admin_unlock and rater_name:
    #     rater = rater_name.strip() or "教学组"
    
    # 检查是否已提交（管理员身份允许在解锁后覆盖，普通组只允许一次）
    existing = get_existing_submission(rater)
    if existing and not is_admin_unlock:
        return render_template("message.html", title="已提交", message=f"「{rater}」组已提交过评分，无法再次提交。")

    # 验证教学组成员身份
    is_admin = False
    if rater.startswith("教学组"):
        if not _is_admin_session(admin_session):
            return render_template("message.html", title="访问受限", message="需要管理员登录。")
        is_admin = True
    
    # 尝试获取锁（教学组成员和管理员解锁不受锁限制）
    # 锁提示已废弃：仅尝试获取锁，不展示提示
    lock_info = None
    if not is_admin and not is_admin_unlock:
        acquire_lock(
            rater=rater,
            token=token_value,
            expiry_minutes=LOCK_EXPIRY_MINUTES,
        )

    # 获取目标列表和发表顺序
    targets = list_targets_for_rater(rater)
    presentation_order = get_presentation_order()
    
    # 如果是重新编辑，获取旧分数
    # 回填旧分数：若当前场次已有历史评分，则用于默认值（管理员或解锁后进入均可）
    existing_scores = get_scores_by_rater(rater)

    return render_template(
        "form.html",
        rater=rater,
        targets=targets,
        weights={"solve": 4, "logic": 3, "analysis": 3},  # 10分制
        lock_info=lock_info,
        presentation_order=presentation_order,
        is_admin=is_admin,
        admin_key=None,
        existing_scores=existing_scores,
    )


@app.post("/submit")
async def submit(
    rater: str = Form(...),
    admin_key: Optional[str] = Form(None),
    token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    response: Response = None,
    request: Request = None,
):
    is_admin = rater.startswith("教学组") and admin_key == ADMIN_KEY
    
    # 检查是否已提交（非管理员）- 已提交即被锁定
    if get_existing_submission(rater) and not is_admin:
        return render_template("message.html", title="已提交", message=f"「{rater}」组已提交过评分，无法再次提交。如需修改，请联系管理员解锁。", success=False)

    # 如果是管理员覆盖，删除旧评分
    if is_admin and get_existing_submission(rater):
        delete_submissions_by_rater(rater)

    # Parse dynamic fields
    form = dict(await request.form())  # type: ignore

    # Collect targets from form
    targets: List[str] = [key.split("_")[1] for key in form.keys() if key.startswith("total_")]
    targets = sorted(set(targets))

    if not targets:
        return render_template("message.html", title="无数据", message="未接收到任何评分数据。")

    records: List[ScoreRecord] = []

    for t in targets:
        total_str = form.get(f"total_{t}") or ""
        solve_str = form.get(f"solve_{t}") or ""
        logic_str = form.get(f"logic_{t}") or ""
        analysis_str = form.get(f"analysis_{t}") or ""

        def to_float(s: str) -> Optional[float]:
            try:
                if s == "":
                    return None
                return float(s)
            except Exception:
                return None

        total = to_float(total_str)
        solve = to_float(solve_str)
        logic = to_float(logic_str)
        analysis = to_float(analysis_str)

        if total is None and any(v is not None for v in [solve, logic, analysis]):
            # 计算总分（百分制）：(solve+logic+analysis)*10，四舍五入，0..100
            sv = solve or 0.0
            lv = logic or 0.0
            av = analysis or 0.0
            total = round(max(0.0, min(100.0, (sv + lv + av) * 10.0)))

        # 跳过全0记录
        if (total is not None and total == 0.0) and ((solve or 0.0) == 0.0 and (logic or 0.0) == 0.0 and (analysis or 0.0) == 0.0):
            continue

        if total is None:
            # Skip empty rows
            continue

        # Clamp total to 0..100
        total = max(0.0, min(100.0, total))

        records.append(
            ScoreRecord(
                rater=rater,
                target=t,
                total=total,
                solve=solve,
                logic=logic,
                analysis=analysis,
            )
        )

    if not records:
        return render_template("message.html", title="无数据", message="未填写任何有效分数。")

    insert_submission(rater, records)
    release_lock(rater)
    return render_template("message.html", title="提交成功", message="感谢提交！", success=True)


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, response: Response, key: Optional[str] = None, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)) -> HTMLResponse:
    # If key provided and valid, establish admin session
    if key == ADMIN_KEY:
        redirect = RedirectResponse(url="/admin")
        _set_admin_session(redirect)
        return redirect
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    
    # 获取所有组（排除教学组，教学组不应作为特殊组出现）
    all_groups = get_all_groups()
    groups = [g["name"] for g in all_groups if g.get("scorable", True)]  # 只显示可被评分的组
    
    # 获取锁状态（基于是否已提交）
    locked_raters = set()
    with get_conn() as conn:
        sid = _active_session_id(conn)
        rows = conn.execute("SELECT DISTINCT rater FROM submissions2 WHERE session_id=?", (sid,)).fetchall()
        for row in rows:
            locked_raters.add(row["rater"])
    
    # 获取发表顺序
    presentation_order = get_presentation_order()
    
    # 获取平均分
    averages = compute_averages()
    
    # 获取所有评分详情
    all_scores = get_all_scores()
    
    # 将评分数据组织成表格形式（只包含可被评分的组）
    score_matrix = {}
    all_raters = set(g["name"] for g in all_groups if g.get("scorable", True))
    for r in all_raters:
        score_matrix[r] = {}

    for score in all_scores:
        rater = score["rater"]
        target = score["target"]
        # 只显示评分者和被评分者都是可被评分的组
        if rater in all_raters and target in all_raters:
            if rater not in score_matrix:
                score_matrix[rater] = {}
            score_matrix[rater][target] = score
    
    sessions = list_sessions()
    anomalies = detect_score_anomalies()
    
    return render_template(
        "admin.html", 
        groups=groups,
        locked_raters=locked_raters,
        presentation_order=presentation_order,
        admin_key=None,
        lock_expiry_minutes=LOCK_EXPIRY_MINUTES,
        averages=averages,
        score_matrix=score_matrix,
        sessions=sessions,
        anomalies=anomalies,
    )


@app.get("/results", response_class=HTMLResponse)
def results(admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)) -> HTMLResponse:
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    rows = compute_averages()
    return render_template("results.html", rows=rows, admin_key=None)


@app.get("/export.csv")
def export_csv(admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)) -> StreamingResponse:
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    rows = compute_averages()
    # Build CSV
    lines = ["target,average,ratings\n"]
    for r in rows:
        lines.append(f"{r['target']},{r['average']:.2f},{r['count']}\n")
    data = "".join(lines).encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv")


@app.get("/presentation-order", response_class=HTMLResponse)
def presentation_order_page(admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)) -> HTMLResponse:
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    
    # 重定向到管理面板的发表顺序标签页
    return RedirectResponse(url="/admin#presentation")


@app.post("/save-presentation-order")
def save_order(order: dict, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    
    if "order" not in order:
        raise HTTPException(status_code=400, detail="缺少顺序数据")
    
    success = save_presentation_order(order["order"])
    return {"ok": success}


@app.post("/admin/session/create")
def admin_create_session(name: str, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    sid = create_session(name)
    return {"ok": True, "id": sid}


@app.post("/admin/session/activate")
def admin_activate_session(session_id: int, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    set_active_session(session_id)
    return {"ok": True}


@app.post("/admin/session/delete")
def admin_delete_session(session_id: int, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    success = delete_session(session_id)
    if not success:
        return {"ok": False, "error": "不能删除当前活跃场次"}
    return {"ok": True}


@app.post("/admin/groups/add")
def admin_add_group(group: dict, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    name = group.get("name")
    scorable = group.get("scorable", True)
    if not name:
        raise HTTPException(status_code=400, detail="需要组名")
    
    success = add_group(name, scorable)
    if not success:
        return {"ok": False, "error": f"组名 '{name}' 已存在"}
    return {"ok": success}


@app.post("/admin/groups/delete")
def admin_delete_group(name: str, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    success = delete_group(name)
    return {"ok": success}


@app.post("/admin/groups/toggle-scorable")
def admin_toggle_scorable(group: dict, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    name = group.get("name")
    scorable = group.get("scorable")
    if not name or scorable is None:
        raise HTTPException(status_code=400, detail="缺少参数")
    success = toggle_group_scorable(name, scorable)
    return {"ok": success}


@app.get("/admin/groups")
def admin_get_groups(admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    groups = get_all_groups()
    return groups


@app.post("/release-lock/{rater}")
def admin_release_lock(rater: str, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")

    release_lock(rater)
    # Also delete submission record to allow re-submission
    with get_conn() as conn:
        from .storage import _active_session_id
        sid = _active_session_id(conn)
        conn.execute("DELETE FROM submissions2 WHERE rater=? AND session_id=?", (rater, sid))
    return {"ok": True}


@app.post("/admin/reset-rater/{rater}")
def admin_reset_rater(rater: str, admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    """重置某个评分组的所有评分并解锁"""
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")
    
    with get_conn() as conn:
        sid = _active_session_id(conn)
        # 删除该组提交的所有评分
        conn.execute("DELETE FROM scores2 WHERE rater=? AND session_id=?", (rater, sid))
        # 删除提交记录（解锁）
        conn.execute("DELETE FROM submissions2 WHERE rater=? AND session_id=?", (rater, sid))
        # 释放锁
        conn.execute("DELETE FROM locks2 WHERE rater=? AND session_id=?", (rater, sid))
    
    return {"ok": True}


@app.post("/delete-score")
async def delete_score(
    rater: str = Form(...),
    target: str = Form(...),
    admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)
):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="需要管理员登录")

    with get_conn() as conn:
        sid = _active_session_id(conn)
        conn.execute("DELETE FROM scores2 WHERE rater=? AND target=? AND session_id=?", (rater, target, sid))

    return {"ok": True}


@app.get("/admin/reset")
def admin_reset(admin_session: Optional[str] = Cookie(default=None, alias=ADMIN_SESSION_COOKIE)):
    if not _is_admin_session(admin_session):
        raise HTTPException(status_code=403, detail="forbidden")
    reset_all()
    return {"ok": True}


@app.get("/api/progress")
def get_progress():
    """获取当前场次的评分进度"""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        
        # 获取所有可评分的组（排除教学组）
        all_groups = get_all_groups()
        scorable_groups = [g["name"] for g in all_groups if g.get("scorable")]
        total = len(scorable_groups)
        
        # 获取已提交评分的组数
        submitted = conn.execute(
            "SELECT COUNT(DISTINCT rater) as cnt FROM submissions2 WHERE session_id=?",
            (sid,)
        ).fetchone()
        submitted_count = submitted["cnt"] if submitted else 0
        
        # 计算进度百分比
        progress = (submitted_count / total * 100) if total > 0 else 0
        
        return {
            "total": total,
            "submitted": submitted_count,
            "progress": round(progress, 1),
            "remaining": total - submitted_count
        }


