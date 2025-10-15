import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Use a neutral database filename for open source distribution
DB_PATH = Path("app/ratings.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        # Base tables
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
              name TEXT PRIMARY KEY
            );
            -- legacy tables (kept for migration and backward compatibility)
            CREATE TABLE IF NOT EXISTS locks (
              rater TEXT PRIMARY KEY,
              token TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS submissions (
              rater TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scores (
              rater TEXT NOT NULL,
              target TEXT NOT NULL,
              total REAL NOT NULL,
              solve REAL,
              logic REAL,
              analysis REAL,
              PRIMARY KEY (rater, target)
            );
            """
        )
        # Seed groups if they don't exist
        with conn:
            # Add scorable column if not exists
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN scorable INTEGER DEFAULT 1")
            except Exception:
                pass  # Column likely already exists
            
            # Ensure default groups exist, preserving any manually added ones
            default_groups = [(str(i), 1) for i in range(1, 10)]
            for name, scorable in default_groups:
                conn.execute(
                    "INSERT OR IGNORE INTO groups(name, scorable) VALUES(?, ?)",
                    (name, scorable)
                )
        # sessions and settings
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS locks2 (
              rater TEXT NOT NULL,
              token TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              session_id INTEGER NOT NULL,
              PRIMARY KEY (rater, session_id)
            );
            CREATE TABLE IF NOT EXISTS submissions2 (
              rater TEXT NOT NULL,
              created_at TEXT NOT NULL,
              session_id INTEGER NOT NULL,
              PRIMARY KEY (rater, session_id)
            );
            CREATE TABLE IF NOT EXISTS scores2 (
              rater TEXT NOT NULL,
              target TEXT NOT NULL,
              total REAL NOT NULL,
              solve REAL,
              logic REAL,
              analysis REAL,
              session_id INTEGER NOT NULL,
              PRIMARY KEY (rater, target, session_id)
            );
            CREATE TABLE IF NOT EXISTS presentation_order2 (
              id INTEGER PRIMARY KEY,
              rater TEXT NOT NULL,
              position INTEGER NOT NULL,
              session_id INTEGER NOT NULL
            );
            """
        )

        # ensure default session and active setting
        row = conn.execute("SELECT id FROM sessions WHERE name=?", ("默认场次",)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO sessions(name, created_at) VALUES(?, ?)",
                ("默认场次", _now().isoformat()),
            )
        sid = conn.execute("SELECT id FROM sessions WHERE name=?", ("默认场次",)).fetchone()[0]
        setting = conn.execute("SELECT value FROM settings WHERE key='active_session_id'").fetchone()
        if setting is None:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('active_session_id', ?)",
                (str(sid),),
            )

        # Migrate legacy data into session-scoped tables if empty
        has_scores2 = conn.execute("SELECT COUNT(*) AS c FROM scores2").fetchone()[0]
        if has_scores2 == 0:
            # submissions
            for r in conn.execute("SELECT rater, created_at FROM submissions").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO submissions2(rater, created_at, session_id) VALUES(?,?,?)",
                    (r["rater"], r["created_at"], sid),
                )
            # scores
            for r in conn.execute("SELECT rater, target, total, solve, logic, analysis FROM scores").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO scores2(rater, target, total, solve, logic, analysis, session_id) VALUES(?,?,?,?,?,?,?)",
                    (r["rater"], r["target"], r["total"], r["solve"], r["logic"], r["analysis"], sid),
                )
            # locks
            for r in conn.execute("SELECT rater, token, expires_at FROM locks").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO locks2(rater, token, expires_at, session_id) VALUES(?,?,?,?)",
                    (r["rater"], r["token"], r["expires_at"], sid),
                )
            # presentation order (legacy optional)
            try:
                for r in conn.execute("SELECT id, rater, position FROM presentation_order").fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO presentation_order2(id, rater, position, session_id) VALUES(?,?,?,?)",
                        (r["id"], r["rater"], r["position"], sid),
                    )
            except Exception:
                pass


def reset_all() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


@dataclass
class Lock:
    rater: str
    token: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.utcnow()


def _active_session_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='active_session_id'").fetchone()
    if not row:
        # Fallback: create default session
        conn.execute(
            "INSERT INTO sessions(name, created_at) VALUES(?, ?)",
            ("默认场次", _now().isoformat()),
        )
        sid = conn.execute("SELECT id FROM sessions WHERE name=?", ("默认场次",)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('active_session_id', ?)",
            (str(sid),),
        )
        return int(sid)
    return int(row[0])


def list_sessions() -> List[dict]:
    with get_conn() as conn:
        sid = _active_session_id(conn)
        rows = conn.execute(
            "SELECT id, name, created_at FROM sessions ORDER BY id DESC"
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "created_at": r["created_at"],
                "active": int(r["id"]) == sid,
            }
            for r in rows
        ]


def create_session(name: str) -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions(name, created_at) VALUES(?, ?)",
            (name, _now().isoformat()),
        )
        sid = conn.execute("SELECT id FROM sessions WHERE name=?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('active_session_id', ?)",
            (str(sid),),
        )
        return int(sid)


def set_active_session(session_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('active_session_id', ?)",
            (str(session_id),),
        )


def delete_session(session_id: int) -> bool:
    """删除一个场次及其所有相关数据"""
    with get_conn() as conn:
        # 检查是否是当前活跃场次
        current_sid = _active_session_id(conn)
        if session_id == current_sid:
            return False  # 不能删除当前场次
        
        # 删除该场次的所有数据
        conn.execute("DELETE FROM scores2 WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM submissions2 WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM locks2 WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return True


def acquire_lock(rater: str, token: str, expiry_minutes: int) -> Tuple[bool, bool]:
    """Return (acquired_or_still_held, held_by_me)."""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        row = conn.execute(
            "SELECT token, expires_at FROM locks2 WHERE rater=? AND session_id=?",
            (rater, sid),
        ).fetchone()
        now = _now()
        if row is None:
            expires = now + timedelta(minutes=expiry_minutes)
            conn.execute(
                "INSERT OR REPLACE INTO locks2(rater, token, expires_at, session_id) VALUES(?,?,?,?)",
                (rater, token, expires.isoformat(), sid),
            )
            return True, True
        # Check expiry
        existing_exp = datetime.fromisoformat(row["expires_at"])
        if existing_exp < now:
            expires = now + timedelta(minutes=expiry_minutes)
            conn.execute(
                "UPDATE locks2 SET token=?, expires_at=? WHERE rater=? AND session_id=?",
                (token, expires.isoformat(), rater, sid),
            )
            return True, True
        if row["token"] == token:
            return True, True
        return False, False


def get_lock(rater: str) -> Optional[Lock]:
    with get_conn() as conn:
        sid = _active_session_id(conn)
        row = conn.execute(
            "SELECT token, expires_at FROM locks2 WHERE rater=? AND session_id=?",
            (rater, sid),
        ).fetchone()
        if not row:
            return None
        return Lock(rater=rater, token=row["token"], expires_at=datetime.fromisoformat(row["expires_at"]))


def release_lock(rater: str) -> None:
    with get_conn() as conn:
        sid = _active_session_id(conn)
        conn.execute("DELETE FROM locks2 WHERE rater=? AND session_id=?", (rater, sid))


@dataclass
class ScoreRecord:
    rater: str
    target: str
    total: float
    solve: Optional[float]
    logic: Optional[float]
    analysis: Optional[float]


def get_existing_submission(rater: str) -> bool:
    with get_conn() as conn:
        sid = _active_session_id(conn)
        row = conn.execute(
            "SELECT 1 FROM submissions2 WHERE rater=? AND session_id=?",
            (rater, sid),
        ).fetchone()
        return row is not None


def delete_submissions_by_rater(rater: str) -> None:
    """删除指定评分者的所有评分记录"""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        conn.execute("DELETE FROM scores2 WHERE rater=? AND session_id=?", (rater, sid))
        conn.execute("DELETE FROM submissions2 WHERE rater=? AND session_id=?", (rater, sid))


def list_targets_for_rater(rater: str) -> List[dict]:
    """
    获取评分目标列表
    返回格式：[{"name": "1", "disabled": False}, {"name": "2", "disabled": True}, ...]
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT name, COALESCE(scorable, 1) as scorable FROM groups").fetchall()
        # 排除教学组和不可被评分的组，但包括自己（标记为禁用）
        targets = []
        for r in rows:
            if r["name"] == "教学组" or not r["scorable"]:
                continue
            targets.append({
                "name": r["name"],
                "disabled": r["name"] == rater  # 自己的组禁用
            })
        
        # 获取发表顺序，如果存在则按照顺序排列目标
        presentation_order = get_presentation_order()
        if presentation_order:
            # 按照发表顺序排序
            ordered_targets = []
            # 先添加有序的
            for group in presentation_order:
                target = next((t for t in targets if t["name"] == group), None)
                if target:
                    ordered_targets.append(target)
            # 再添加剩余的
            for target in targets:
                if target not in ordered_targets:
                    ordered_targets.append(target)
            return ordered_targets
        
        return sorted(targets, key=lambda x: x["name"])


def get_scores_by_rater(rater: str) -> dict:
    """获取指定评分者的所有评分记录"""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        rows = conn.execute(
            "SELECT target, total, solve, logic, analysis FROM scores2 WHERE rater=? AND session_id=?",
            (rater, sid),
        ).fetchall()
        return {
            r["target"]: {
                "total": r["total"],
                "solve": r["solve"],
                "logic": r["logic"],
                "analysis": r["analysis"],
            }
            for r in rows
        }


def insert_submission(rater: str, records: Iterable[ScoreRecord]) -> None:
    with get_conn() as conn:
        sid = _active_session_id(conn)
        # overwrite existing
        conn.execute("DELETE FROM scores2 WHERE rater=? AND session_id=?", (rater, sid))
        conn.execute(
            "INSERT OR REPLACE INTO submissions2(rater, created_at, session_id) VALUES(?,?,?)",
            (rater, _now().isoformat(), sid),
        )
        conn.executemany(
            "INSERT INTO scores2(rater, target, total, solve, logic, analysis, session_id) VALUES(?,?,?,?,?,?,?)",
            [
                (rec.rater, rec.target, rec.total, rec.solve, rec.logic, rec.analysis, sid)
                for rec in records
            ],
        )


def compute_averages() -> List[dict]:
    """计算平均分，只包含可被评分的组（scorable=True）"""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        rows = conn.execute(
            """
            SELECT s.target, AVG(s.total) AS avg_total, COUNT(*) AS cnt
            FROM scores2 s
            JOIN groups g ON s.target = g.name
            WHERE s.session_id=? AND COALESCE(g.scorable, 1) = 1
            GROUP BY s.target
            ORDER BY AVG(s.total) DESC
            """,
            (sid,),
        ).fetchall()
        return [
            {"target": r["target"], "average": float(r["avg_total"]) if r["avg_total"] is not None else 0.0, "count": int(r["cnt"])}
            for r in rows
        ]


def get_all_scores() -> List[dict]:
    """获取所有评分数据，包括详细的每组对每组的评分"""
    with get_conn() as conn:
        sid = _active_session_id(conn)
        rows = conn.execute(
            """
            SELECT s.rater, s.target, s.total, s.solve, s.logic, s.analysis
            FROM scores2 s
            WHERE s.session_id=?
            ORDER BY s.rater, s.target
            """,
            (sid,),
        ).fetchall()
        
        results = []
        for row in rows:
            results.append({
                "rater": row["rater"],
                "target": row["target"],
                "total": float(row["total"]) if row["total"] is not None else 0.0,
                "solve": float(row["solve"]) if row["solve"] is not None else None,
                "logic": float(row["logic"]) if row["logic"] is not None else None,
                "analysis": float(row["analysis"]) if row["analysis"] is not None else None,
            })
        return results


def get_presentation_order() -> List[str]:
    """获取发表顺序，如果不存在则返回空列表"""
    try:
        with get_conn() as conn:
            sid = _active_session_id(conn)
            # 确保表存在
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS presentation_order2 (
                    id INTEGER PRIMARY KEY,
                    rater TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    session_id INTEGER NOT NULL
                )
                """
            )
            rows = conn.execute(
                "SELECT rater FROM presentation_order2 WHERE session_id=? ORDER BY position ASC",
                (sid,),
            ).fetchall()
            
            if rows:
                return [r["rater"] for r in rows]
            return []
    except Exception as e:
        print(f"获取发表顺序出错: {e}")
        return []


def get_all_groups() -> List[dict]:
    """Get all groups with their scorable status"""
    with get_conn() as conn:
        # Add scorable column if not exists
        try:
            conn.execute("ALTER TABLE groups ADD COLUMN scorable INTEGER DEFAULT 1")
        except Exception:
            pass  # Column already exists

        rows = conn.execute("SELECT name, COALESCE(scorable, 1) as scorable FROM groups ORDER BY name").fetchall()
        return [{"name": r["name"], "scorable": bool(r["scorable"])} for r in rows]


def add_group(name: str, scorable: bool = True) -> bool:
    """Add a new group"""
    try:
        with get_conn() as conn:
            # Add scorable column if not exists
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN scorable INTEGER DEFAULT 1")
            except Exception:
                pass

            conn.execute(
                "INSERT INTO groups(name, scorable) VALUES(?, ?)",
                (name, 1 if scorable else 0),
            )
            return True
    except sqlite3.IntegrityError:
        # Group already exists
        return False
    except Exception as e:
        print(f"Error adding group: {e}")
        return False


def delete_group(name: str) -> bool:
    """Delete a group"""
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM groups WHERE name=?", (name,))
            return True
    except Exception:
        return False


def toggle_group_scorable(name: str, scorable: bool) -> bool:
    """Toggle whether a group can be scored"""
    try:
        with get_conn() as conn:
            # Add scorable column if not exists
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN scorable INTEGER DEFAULT 1")
            except Exception:
                pass

            conn.execute(
                "UPDATE groups SET scorable=? WHERE name=?",
                (1 if scorable else 0, name),
            )
            return True
    except Exception:
        return False


def save_presentation_order(order: List[str]) -> bool:
    """保存发表顺序"""
    try:
        with get_conn() as conn:
            sid = _active_session_id(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS presentation_order2 (
                    id INTEGER PRIMARY KEY,
                    rater TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    session_id INTEGER NOT NULL
                )
                """
            )
            conn.execute("DELETE FROM presentation_order2 WHERE session_id=?", (sid,))
            for i, r in enumerate(order):
                conn.execute(
                    "INSERT INTO presentation_order2(rater, position, session_id) VALUES(?, ?, ?)",
                    (r, i, sid),
                )
            return True
    except Exception as e:
        print(f"保存发表顺序出错: {e}")
        return False


def detect_score_anomalies() -> List[dict]:
    """
    检测评分异常
    返回异常评分列表，每项包含：rater, target, score, reason
    """
    anomalies = []
    
    with get_conn() as conn:
        sid = _active_session_id(conn)
        
        # 获取每个组的所有评分，计算平均值和标准差
        targets = conn.execute(
            """
            SELECT DISTINCT target FROM scores2 
            WHERE session_id=? AND target IN (
                SELECT name FROM groups WHERE COALESCE(scorable, 1) = 1
            )
            """,
            (sid,)
        ).fetchall()
        
        for target_row in targets:
            target = target_row["target"]
            
            # 获取该组的所有评分
            scores = conn.execute(
                "SELECT rater, total FROM scores2 WHERE target=? AND session_id=?",
                (target, sid)
            ).fetchall()
            
            if len(scores) < 3:
                continue  # 评分太少，无法判断异常
            
            # 计算平均值和标准差
            import statistics
            score_values = [s["total"] for s in scores]
            mean = statistics.mean(score_values)
            std_dev = statistics.stdev(score_values) if len(score_values) > 1 else 0
            
            # 检测异常：超过 2 个标准差的评分
            for score in scores:
                deviation = abs(score["total"] - mean)
                if std_dev > 0 and deviation > 2 * std_dev:
                    reason = "过高" if score["total"] > mean else "过低"
                    anomalies.append({
                        "rater": score["rater"],
                        "target": target,
                        "score": score["total"],
                        "mean": round(mean, 1),
                        "deviation": round(deviation, 1),
                        "reason": f"相比平均分({mean:.1f})偏{reason}"
                    })
        
        # 检测某个评分者的整体评分偏高或偏低
        raters = conn.execute(
            "SELECT DISTINCT rater FROM scores2 WHERE session_id=?",
            (sid,)
        ).fetchall()
        
        for rater_row in raters:
            rater = rater_row["rater"]
            
            # 获取该评分者给出的所有分数
            rater_scores = conn.execute(
                "SELECT AVG(total) as avg_score FROM scores2 WHERE rater=? AND session_id=?",
                (rater, sid)
            ).fetchone()
            
            # 获取所有评分者的平均分
            overall_avg = conn.execute(
                "SELECT AVG(total) as avg_score FROM scores2 WHERE session_id=?",
                (sid,)
            ).fetchone()
            
            if rater_scores and overall_avg:
                rater_avg = rater_scores["avg_score"]
                global_avg = overall_avg["avg_score"]
                
                if abs(rater_avg - global_avg) > 15:  # 平均分差距超过15分
                    reason = "整体偏高" if rater_avg > global_avg else "整体偏低"
                    anomalies.append({
                        "rater": rater,
                        "target": "全部",
                        "score": round(rater_avg, 1),
                        "mean": round(global_avg, 1),
                        "deviation": round(abs(rater_avg - global_avg), 1),
                        "reason": f"{reason}（该评分者平均{rater_avg:.1f}，全局平均{global_avg:.1f}）"
                    })
    
    return anomalies


