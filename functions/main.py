"""Firebase entry point: runs monitor.check() every minute, state in Firestore."""
import os

os.environ.setdefault("FETCH_MODE", "requests")  # no headless browser in Cloud Functions

from firebase_admin import firestore, initialize_app
from firebase_functions import https_fn, options, scheduler_fn

import monitor

initialize_app()
STATE_DOC = ("vox_monitor", "state")


@scheduler_fn.on_schedule(schedule="every 1 minutes", region="europe-west1",
                          timeout_sec=120, memory=options.MemoryOption.MB_256)
def vox_monitor(event: scheduler_fn.ScheduledEvent) -> None:
    ref = firestore.client().collection(STATE_DOC[0]).document(STATE_DOC[1])
    snap = ref.get()
    state = snap.to_dict() if snap.exists else {}
    before = dict(state)
    monitor.check(state)
    if state != before:
        ref.set(state)


@https_fn.on_request(region="europe-west1")
def test_notify(req: https_fn.Request) -> https_fn.Response:
    """GET .../test_notify?key=ADMIN_KEY sends a test alert on every channel."""
    if not monitor.env("ADMIN_KEY") or req.args.get("key") != monitor.env("ADMIN_KEY"):
        return https_fn.Response("forbidden", status=403)
    sent = monitor.notify("VOX monitor test", "If you see this, alerts work.",
                          monitor.BOOKING_URL, urgent=False)
    return https_fn.Response(f"sent via: {', '.join(sent) or 'nothing (check functions/.env)'}")
