import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS calls (
                  id TEXT PRIMARY KEY,
                  mode TEXT NOT NULL,
                  phase TEXT NOT NULL,
                  incoming INTEGER NOT NULL DEFAULT 0,
                  answered INTEGER NOT NULL DEFAULT 0,
                  turn INTEGER NOT NULL DEFAULT 0,
                  failures INTEGER NOT NULL DEFAULT 0,
                  token TEXT NOT NULL DEFAULT '',
                  prompt TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                  id TEXT PRIMARY KEY,
                  call_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  occurred_at TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  error TEXT
                );
                CREATE TABLE IF NOT EXISTS commands (
                  id TEXT PRIMARY KEY,
                  call_id TEXT NOT NULL,
                  action TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  call_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  detail TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def log(self, call_id, kind, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO activity(call_id,kind,detail,created_at) VALUES(?,?,?,?)",
                (call_id, kind, detail, now()),
            )
        print(f"{now()} {kind}: {detail}", flush=True)

    def call(self, call_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            return dict(row) if row else None

    def update(self, call_id, **values):
        permitted = {"phase", "turn", "failures", "token", "prompt", "ended_at"}
        if not set(values) <= permitted:
            raise ValueError("Unsupported call update")
        values["updated_at"] = now()
        with self.connect() as db:
            db.execute(
                f"UPDATE calls SET {','.join(k + '=?' for k in values)} WHERE id=? AND ended_at IS NULL",
                (*values.values(), call_id),
            )

    def enqueue(self, event, mode="telnyx"):
        payload, kind = event["payload"], event["event_type"]
        call_id = payload["call_control_id"]
        payload = {
            k: v
            for k, v in payload.items()
            if k
            in {
                "call_control_id",
                "connection_id",
                "direction",
                "client_state",
                "digits",
                "status",
            }
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT mode FROM calls WHERE id=?", (call_id,)).fetchone()
            if old and old["mode"] != mode:
                raise ValueError("Call mode cannot change")
            db.execute(
                "INSERT OR IGNORE INTO calls(id,mode,phase,created_at,updated_at) VALUES(?,?,?,?,?)",
                (call_id, mode, "new", now(), now()),
            )
            added = (
                db.execute(
                    "INSERT OR IGNORE INTO events(id,call_id,kind,occurred_at,payload) VALUES(?,?,?,?,?)",
                    (
                        event["id"],
                        call_id,
                        kind,
                        event["occurred_at"],
                        json.dumps(payload),
                    ),
                ).rowcount
                == 1
            )
            if added:
                direction = payload.get("direction")
                if direction in ("incoming", "outgoing"):
                    db.execute(
                        "UPDATE calls SET incoming=? WHERE id=? AND incoming=0",
                        (1 if direction == "incoming" else -1, call_id),
                    )
                if kind == "call.answered":
                    db.execute(
                        "UPDATE calls SET answered=1 WHERE id=?",
                        (call_id,),
                    )
                if kind == "call.hangup":
                    db.execute(
                        "UPDATE calls SET phase='ended',ended_at=?,updated_at=? WHERE id=?",
                        (event["occurred_at"], now(), call_id),
                    )
                db.execute(
                    "INSERT INTO activity(call_id,kind,detail,created_at) VALUES(?,?,?,?)",
                    (call_id, "event", kind, now()),
                )
        if added:
            print(f"{now()} event: {kind}", flush=True)
        return added

    def snapshot(self, mode):
        with self.connect() as db:
            calls = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM calls WHERE mode=? ORDER BY created_at DESC LIMIT 30",
                    (mode,),
                )
            ]
            for call in calls:
                call["activity"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT kind,detail,created_at FROM activity WHERE call_id=? ORDER BY id",
                        (call["id"],),
                    )
                ]
            counts = {
                "calls": db.execute(
                    "SELECT count(*) FROM calls WHERE mode=?", (mode,)
                ).fetchone()[0],
                "answers": db.execute(
                    """
                    SELECT count(*) FROM activity a
                    JOIN calls c ON c.id=a.call_id
                    WHERE c.mode=? AND a.kind='answer'
                    """,
                    (mode,),
                ).fetchone()[0],
                "errors": db.execute(
                    """
                    SELECT count(*) FROM events e
                    JOIN calls c ON c.id=e.call_id
                    WHERE c.mode=? AND e.status='failed'
                    """,
                    (mode,),
                ).fetchone()[0],
            }
            return {"calls": calls, "counts": counts}
