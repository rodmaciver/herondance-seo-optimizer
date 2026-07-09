"""Trigger and monitor Cloud Run Job executions from the Gradio UI."""
from __future__ import annotations

import json
import os
import time
from typing import Iterator

_PROJECT = "seo-optimizer-499718"
_REGION = "us-east1"
_JOB = "seo-batch-job"
_RUN_BASE = f"https://run.googleapis.com/v2/projects/{_PROJECT}/locations/{_REGION}"


def _access_token() -> str:
    from google.auth.transport.requests import Request as _Req

    key_json = os.environ.get("SHEETS_SERVICE_ACCOUNT_KEY")
    if key_json:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(key_json),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        import google.auth
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    creds.refresh(_Req())
    return creds.token  # type: ignore[return-value]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def trigger_batch_job(limit: int | None = None) -> str:
    """Submit a Cloud Run Job execution. Returns the LRO operation name."""
    import requests

    args = ["batch_runner.py"]
    if limit is not None:
        args += ["--limit", str(limit)]

    resp = requests.post(
        f"{_RUN_BASE}/jobs/{_JOB}:run",
        json={"overrides": {"containerOverrides": [{"args": args}]}},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["name"]


def _elapsed_str(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _get_queue_status() -> tuple[int, str]:
    """Read the queue sheet and return (total_generated_count, next_pending_url).

    next_pending_url is the first non-Generated URL — since the batch runner
    processes sequentially, that is the URL currently being worked on.
    """
    import pandas as pd
    from . import sheets_client, workbook

    raw = sheets_client.read_queue()
    priority_df, backlog_df = workbook.parse_queue_raw(raw)
    all_rows = pd.concat([priority_df, backlog_df], ignore_index=True)
    all_rows = all_rows[all_rows["url"].str.startswith("http")]
    if "generated" in all_rows.columns:
        generated_count = int(all_rows["generated"].astype(bool).sum())
        pending = all_rows[~all_rows["generated"].astype(bool)]
    else:
        generated_count = 0
        pending = all_rows
    next_url = pending.iloc[0]["url"] if not pending.empty else ""
    return generated_count, next_url


def _progress_md(newly_done: int, batch_size: int, elapsed: float, current_url: str = "") -> str:
    """Return a markdown progress block."""
    pct = min(100, int(100 * newly_done / batch_size)) if batch_size else 0
    filled = pct // 10
    bar = "█" * filled + "░" * (10 - filled)
    lines = [f"**URLs completed: {newly_done} / {batch_size}** `[{bar}]` {pct}%"]
    if current_url:
        lines.append(f"Processing: `{current_url}`")
    if newly_done > 0 and newly_done < batch_size:
        avg_secs = elapsed / newly_done
        eta = (batch_size - newly_done) * avg_secs
        lines.append(f"Avg {_elapsed_str(avg_secs)} per URL · Est. remaining: ~{_elapsed_str(eta)}")
    return "\n\n" + "  \n".join(lines)


def run_batch_with_status(limit: int | None) -> Iterator[str]:
    """Generator for Gradio: trigger the Cloud Run Job and stream status updates."""
    import pandas as pd
    from . import sheets_client, workbook

    # Read queue before triggering so we can track incremental progress.
    initial_generated = 0
    batch_size: int | None = limit
    have_sheet = False
    try:
        raw = sheets_client.read_queue()
        priority_df, backlog_df = workbook.parse_queue_raw(raw)
        all_rows = pd.concat([priority_df, backlog_df], ignore_index=True)
        all_rows = all_rows[all_rows["url"].str.startswith("http")]
        if "generated" in all_rows.columns:
            initial_generated = int(all_rows["generated"].astype(bool).sum())
            pending = int((~all_rows["generated"].astype(bool)).sum())
        else:
            pending = len(all_rows)
        batch_size = min(limit, pending) if limit else pending
        have_sheet = True
    except Exception:
        pass

    size_msg = f"{batch_size} URLs" if batch_size else "all pending URLs"
    yield f"⏳ Submitting batch job — {size_msg}…"

    try:
        op_name = trigger_batch_job(limit)
    except Exception as exc:
        yield f"❌ Failed to submit job: {exc}"
        return

    short_op = op_name.split("/")[-1]
    yield (
        f"✅ Job submitted — processing **{size_msg}** (`{short_op}`).\n\n"
        "Polling every 30s. You can close this tab — "
        "results upload to Shared Drive regardless."
    )

    lro_url = f"https://run.googleapis.com/v2/{op_name}"
    start_time = time.time()
    import requests

    poll_tick = 0
    newly_done = 0  # last known count — reused between sheet polls
    while True:
        time.sleep(30)
        poll_tick += 1
        elapsed = time.time() - start_time

        try:
            resp = requests.get(lro_url, headers=_headers(), timeout=30)
            resp.raise_for_status()
            lro = resp.json()
        except Exception as exc:
            yield f"⚠ Status check error: {exc} — retrying in 30s…"
            continue

        exec_name = lro.get("metadata", {}).get("name", op_name)
        short_exec = exec_name.split("/")[-1]

        # Sheet progress: download every 2nd tick (~60s) to avoid hammering Drive.
        prog = ""
        if have_sheet and batch_size and (poll_tick % 2 == 0 or lro.get("done")):
            try:
                total_gen, current_url = _get_queue_status()
                newly_done = max(0, total_gen - initial_generated)
                # Don't show "Processing:" on the final tick — job is done.
                prog = _progress_md(newly_done, batch_size, elapsed,
                                    current_url if not lro.get("done") else "")
            except Exception:
                pass
        elif have_sheet and batch_size and newly_done:
            prog = _progress_md(newly_done, batch_size, elapsed)

        if lro.get("done"):
            if "error" in lro:
                err = lro["error"]
                yield (
                    f"❌ Batch job **failed** after {_elapsed_str(elapsed)}: "
                    f"{err.get('message', str(err))}{prog}"
                )
            else:
                failed_note = ""
                if have_sheet and batch_size:
                    try:
                        total_gen, _ = _get_queue_status()
                        newly_done = max(0, total_gen - initial_generated)
                        failed = batch_size - newly_done
                        if failed > 0:
                            failed_note = (
                                f"\n\n⚠ **{failed} URL(s) failed** — "
                                "see `batch_errors_*.txt` in Shared Drive for details."
                            )
                    except Exception:
                        pass
                yield (
                    f"✅ Batch job **completed** in {_elapsed_str(elapsed)}. "
                    f"Check Shared Drive for new files.{prog}{failed_note}"
                )
            return

        yield f"⏳ Running — {_elapsed_str(elapsed)} elapsed (execution: `{short_exec}`){prog}"
