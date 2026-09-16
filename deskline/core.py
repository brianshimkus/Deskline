import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .knowledge import GREETING, MENU, POLICIES
from .storage import now


def encode_state(stage, token):
    return base64.b64encode(
        json.dumps({"deskline": 1, "stage": stage, "token": token}).encode()
    ).decode()


def decode_state(value):
    try:
        result = json.loads(base64.b64decode(value or "", validate=True))
        return (
            result if isinstance(result, dict) and result.get("deskline") == 1 else {}
        )
    except (ValueError, TypeError):
        return {}


class TelnyxVoice:
    def __init__(self, settings):
        self.settings = settings

    async def command(self, call_id, action, body):
        if not self.settings.api_key:
            raise RuntimeError("Telnyx API key is not configured")
        async with httpx.AsyncClient(timeout=6) as client:
            for attempt in range(2):
                try:
                    response = await client.post(
                        f"https://api.telnyx.com/v2/calls/{quote(call_id, safe='')}/actions/{action}",
                        headers={"Authorization": f"Bearer {self.settings.api_key}"},
                        json=body,
                    )
                except httpx.TransportError:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    raise RuntimeError(
                        "Telnyx command outcome is uncertain; inspect the portal"
                    ) from None
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                if not response.is_success:
                    raise RuntimeError(
                        f"Telnyx returned HTTP {response.status_code}; inspect the portal"
                    )
                return response.json().get("data", {})


class Engine:
    """One local worker, explicit call phases, provider-independent rehearsal."""

    def __init__(self, settings, store, provider=None):
        self.settings, self.store = settings, store
        self.provider = provider or TelnyxVoice(settings)
        self.lock = asyncio.Lock()

    async def process_next(self):
        async with self.lock:
            with self.store.connect() as db:
                row = db.execute(
                    "SELECT * FROM events WHERE status='pending' ORDER BY rowid LIMIT 1"
                ).fetchone()
                if row is None:
                    return False
                event = dict(row)
                db.execute(
                    "UPDATE events SET status='processing' WHERE id=?",
                    (event["id"],),
                )
            try:
                await self._process(event)
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE events SET status='done' WHERE id=?",
                        (event["id"],),
                    )
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, RuntimeError)
                    else "Processing failed; inspect provider state"
                )
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE events SET status='failed',error=? WHERE id=?",
                        (message, event["id"]),
                    )
                self.store.update(event["call_id"], phase="needs review")
                self.store.log(event["call_id"], "error", message)
            return True

    async def drain(self):
        while await self.process_next():
            pass

    async def _process(self, event):
        call = self.store.call(event["call_id"])
        payload = json.loads(event["payload"])
        kind = event["kind"]
        if call["ended_at"] or call["phase"] in ("needs review", "hangup requested"):
            return
        if call["incoming"] != 1:
            return
        if kind in ("call.initiated", "call.answered") and call["phase"] in (
            "new",
            "answer requested",
        ):
            if call["answered"]:
                await self._menu(call, GREETING + MENU)
            elif call["phase"] == "new":
                self.store.update(call["id"], phase="answer requested")
                await self._command(call, "answer", {}, "answer")
        elif kind == "call.gather.ended":
            state = decode_state(payload.get("client_state"))
            if (
                call["phase"] != "menu"
                or state.get("stage") != "menu"
                or state.get("token") != call["token"]
            ):
                self.store.log(
                    call["id"], "ignored", "Stale or unrelated keypad result"
                )
                return
            status = payload.get("status")
            if status in ("call_hangup", "cancelled", "cancelled_amd"):
                return
            digit = payload.get("digits", "")
            if digit == "0":
                await self._finish(call, "Thanks for trying Deskline. Goodbye.")
            elif digit in POLICIES and status == "valid":
                item = POLICIES[digit]
                self.store.log(
                    call["id"], "answer", f"{item['title']} · {item['source']}"
                )
                if call["turn"] >= 6:
                    await self._finish(
                        call,
                        item["answer"]
                        + " This demo has reached its menu limit. Goodbye.",
                    )
                else:
                    await self._menu(call, item["answer"] + " " + MENU, failures=0)
            else:
                failures = call["failures"] + 1
                self.store.log(
                    call["id"],
                    "recovery",
                    "No input" if not digit else "Invalid selection",
                )
                if failures >= 2:
                    await self._finish(
                        call,
                        "We could not get a menu selection. Please call again when you are ready. Goodbye.",
                    )
                else:
                    prefix = (
                        "I did not receive a keypress. "
                        if not digit
                        else "That selection is not available. "
                    )
                    await self._menu(call, prefix + MENU, failures=failures)
        elif kind == "call.speak.ended":
            state = decode_state(payload.get("client_state"))
            if (
                call["phase"] == "closing"
                and state.get("stage") == "closing"
                and state.get("token") == call["token"]
            ):
                await self._hangup(call)

    async def _menu(self, call, prompt, failures=0):
        token = uuid.uuid4().hex
        self.store.update(
            call["id"],
            phase="menu",
            turn=call["turn"] + 1,
            failures=failures,
            token=token,
            prompt=prompt,
        )
        await self._command(
            call,
            "gather_using_speak",
            {
                "payload": prompt,
                "payload_type": "text",
                "voice": self.settings.voice,
                "language": "en-US",
                "minimum_digits": 1,
                "maximum_digits": 1,
                "maximum_tries": 1,
                "timeout_millis": 8000,
                "terminating_digit": "",
                "valid_digits": "0123456789*#",
                "client_state": encode_state("menu", token),
            },
            token,
        )

    async def _finish(self, call, prompt):
        token = uuid.uuid4().hex
        self.store.update(call["id"], phase="closing", token=token, prompt=prompt)
        await self._command(
            call,
            "speak",
            {
                "payload": prompt,
                "payload_type": "text",
                "voice": self.settings.voice,
                "language": "en-US",
                "client_state": encode_state("closing", token),
            },
            token,
        )

    async def _hangup(self, call):
        self.store.update(call["id"], phase="hangup requested")
        await self._command(call, "hangup", {}, "hangup")

    async def _command(self, call, action, body, step):
        if self.store.call(call["id"])["ended_at"]:
            return {}
        command_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"deskline:{call['id']}:{action}:{step}")
        )
        with self.store.connect() as db:
            old = db.execute(
                "SELECT status FROM commands WHERE id=?", (command_id,)
            ).fetchone()
            if old:
                if old["status"] == "done":
                    return {}
                raise RuntimeError(
                    "Prior command outcome is uncertain; inspect the portal before retrying"
                )
            db.execute(
                "INSERT INTO commands VALUES(?,?,?,?,?)",
                (command_id, call["id"], action, "sending", now()),
            )
        try:
            result = (
                {"result": "simulated"}
                if call["mode"] == "rehearsal"
                else await self.provider.command(
                    call["id"], action, {**body, "command_id": command_id}
                )
            )
        except Exception:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE commands SET status='needs review' WHERE id=?",
                    (command_id,),
                )
            raise
        with self.store.connect() as db:
            db.execute(
                "UPDATE commands SET status='done' WHERE id=?",
                (command_id,),
            )
        self.store.log(
            call["id"],
            "command",
            ("Simulated " if call["mode"] == "rehearsal" else "Accepted ") + action,
        )
        if body.get("payload"):
            self.store.log(call["id"], "prompt", body["payload"])
        return result

    async def expire_calls(self):
        async with self.lock:
            with self.store.connect() as db:
                calls = [
                    dict(row)
                    for row in db.execute(
                        """
                        SELECT * FROM calls
                        WHERE ended_at IS NULL
                          AND phase NOT IN ('hangup requested','needs review')
                          AND incoming=1
                        """
                    )
                ]
            current = datetime.now(UTC)
            for call in calls:
                age = (
                    current - datetime.fromisoformat(call["created_at"])
                ).total_seconds()
                idle = (
                    current - datetime.fromisoformat(call["updated_at"])
                ).total_seconds()
                if age >= self.settings.max_call_seconds or (
                    call["phase"] == "closing" and idle >= 20
                ):
                    try:
                        self.store.log(
                            call["id"],
                            "recovery",
                            "Call duration or closing timeout reached",
                        )
                        await self._hangup(call)
                        if call["mode"] == "rehearsal":
                            self.store.enqueue(
                                self.event(call["id"], "call.hangup"), "rehearsal"
                            )
                    except RuntimeError as exc:
                        self.store.update(call["id"], phase="needs review")
                        self.store.log(call["id"], "error", str(exc))

    def recover(self):
        with self.store.connect() as db:
            calls = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT call_id FROM commands WHERE status='sending'"
                )
            ]
            calls += [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT call_id FROM events WHERE status='processing'"
                )
            ]
            db.execute(
                "UPDATE events SET status='failed',error='Interrupted; inspect provider state' WHERE status='processing'"
            )
            db.execute(
                "UPDATE commands SET status='needs review' WHERE status='sending'"
            )
        for call_id in set(calls):
            self.store.update(call_id, phase="needs review")
            self.store.log(
                call_id,
                "error",
                "Interrupted during processing; check the provider before taking further action",
            )

    def event(self, call_id, kind, **payload):
        return {
            "id": str(uuid.uuid4()),
            "event_type": kind,
            "occurred_at": now(),
            "payload": {
                "call_control_id": call_id,
                "direction": "incoming",
                **payload,
            },
        }

    async def start_rehearsal(self):
        call_id = "practice-" + uuid.uuid4().hex
        for kind in ("call.initiated", "call.answered"):
            self.store.enqueue(self.event(call_id, kind), "rehearsal")
        await self.drain()
        return self.store.call(call_id)

    async def press(self, call_id, digit):
        call = self.store.call(call_id)
        if not call or call["mode"] != "rehearsal" or call["phase"] != "menu":
            raise ValueError("Start or select an active practice call first")
        self.store.enqueue(
            self.event(
                call_id,
                "call.gather.ended",
                digits=digit,
                status="valid" if digit else "timeout",
                client_state=encode_state("menu", call["token"]),
            ),
            "rehearsal",
        )
        await self.drain()
        call = self.store.call(call_id)
        if call["phase"] == "closing":
            self.store.enqueue(
                self.event(
                    call_id,
                    "call.speak.ended",
                    status="completed",
                    client_state=encode_state("closing", call["token"]),
                ),
                "rehearsal",
            )
            await self.drain()
            self.store.enqueue(self.event(call_id, "call.hangup"), "rehearsal")
            await self.drain()
        return self.store.call(call_id)
