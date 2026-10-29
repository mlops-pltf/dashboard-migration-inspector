"""Portal UI (Streamlit). Talks only to the portal API (PORTAL_API_URL).

    streamlit run inspector_portal/ui.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import httpx
import streamlit as st

API = os.environ.get("PORTAL_API_URL", "http://localhost:8080").rstrip("/")
MODES = {"Full validation (rules, data and AI review)": "agent",
         "Quick check (rules and data, no AI, seconds)": "rules+data"}
VERDICT_STYLE = {"READY": ("green", "Ready for human review"), "NOT_READY": ("red", "Not ready: fix the blockers"),
                 "NEEDS_REVIEW": ("orange", "Needs review: some blockers could not be proven")}
STATUS_ICON = {"FAIL": "✗", "UNVERIFIED": "?", "PASS": "✓"}

st.set_page_config(page_title="Inspector", page_icon="🔎", layout="wide")


def api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=kw.pop("timeout", 60), **kw)
    except httpx.HTTPError as exc:
        st.error(f"The portal backend is not reachable ({type(exc).__name__}).")
        st.stop()
    if r.status_code >= 400:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise RuntimeError(str(detail))
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%d %b %H:%M") if ts else "–"


def duration(seconds: int | None) -> str:
    if seconds is None:
        return "–"
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def go(page: str, rid: str | None = None) -> None:
    st.session_state.page = page
    st.query_params.clear()
    if rid:
        st.session_state.run_id = rid
        st.query_params["run"] = rid


def open_run(rid: str) -> None:
    go("run", rid)


# -- pages ------------------------------------------------------------------------------------
def page_new() -> None:
    st.header("New validation")
    st.caption("Upload both sides of one migration: the ThoughtSpot export and the Strategy version of it.")

    existing = api("GET", "/api/packages")
    source = st.radio("Package", ["Upload a new package", "Re-run an uploaded package"], horizontal=True,
                      disabled=not existing, label_visibility="collapsed")
    package = None
    if source == "Upload a new package":
        package = upload_form()
    else:
        labels = {f"{p['name']}  ·  {when(p['created'])}  ·  {p['id']}": p for p in existing}
        package = labels[st.selectbox("Uploaded packages", list(labels))]
        show_check(package["check"])
    if package:
        st.session_state.package = package
    package = st.session_state.get("package") if source == "Upload a new package" else package
    if not package:
        return

    st.subheader("Run")
    mode = MODES[st.radio("What to run", list(MODES))]
    instructions = st.text_area(
        "Notes for the Inspector (optional)", max_chars=4000, height=110,
        placeholder="e.g. Focus on the Sales Overview liveboard. The Region filter was removed on purpose.",
        help="Context, not commands. Differences you call intentional are still reported, marked as described by "
             "you. Nothing passes because of a note.")
    if not package["check"].get("ok"):
        st.warning("The experts could not read everything in this package. You can still run, but expect "
                   "unverified findings.")
    if st.button("Start validation", type="primary"):
        run = api("POST", "/api/runs", json={"package_id": package["id"], "mode": mode, "instructions": instructions})
        st.session_state.pop("package", None)
        open_run(run["id"])
        st.rerun()


def upload_form() -> dict | None:
    with st.form("upload", clear_on_submit=False):
        name = st.text_input("Name", placeholder="e.g. Sales Orders")
        two, whole = st.tabs(["Two exports", "One package zip"])
        with two:
            ts = st.file_uploader("ThoughtSpot export: the TML zip, or .tml files", accept_multiple_files=True,
                                  type=["zip", "tml"])
            stg = st.file_uploader("Strategy: a zip (model + dashboards/*.mstr), or the model file and the .mstr files",
                                   accept_multiple_files=True, type=["zip", "yaml", "yml", "json", "mstr"])
            mapping = st.file_uploader("Mapping file (optional)", type=["yaml", "yml"])
        with whole:
            pkg_zip = st.file_uploader("A zip with thoughtspot/ and strategy/ folders (and optionally mapping.yaml)",
                                       type=["zip"])
        submitted = st.form_submit_button("Upload and check")
    if not submitted:
        return None
    if not name.strip():
        st.error("Give the package a name.")
        return None
    files = [("thoughtspot", (f.name, f.getvalue())) for f in ts or []] + \
            [("strategy", (f.name, f.getvalue())) for f in stg or []]
    if mapping:
        files.append(("mapping", (mapping.name, mapping.getvalue())))
    if pkg_zip:
        files = [("package_zip", (pkg_zip.name, pkg_zip.getvalue()))]
    if not files:
        st.error("Upload the two exports, or one package zip.")
        return None
    with st.spinner("Uploading; the ThoughtSpot and Strategy experts are reading it ..."):
        try:
            package = api("POST", "/api/packages", data={"name": name.strip()}, files=files, timeout=600)
        except RuntimeError as exc:
            st.error(f"Upload refused: {exc}")
            return None
    show_check(package["check"])
    return package


def show_check(check: dict) -> None:
    cols = st.columns(2)
    for col, side, title in ((cols[0], "thoughtspot", "ThoughtSpot"), (cols[1], "strategy", "Strategy")):
        info = check.get("sides", {}).get(side, {})
        with col.container(border=True):
            st.markdown(f"**{title}**" + (f" · {info['format']}" if info.get("format") else ""))
            if info.get("error"):
                st.error(info["error"])
                continue
            st.write(f"Models: {', '.join(info.get('models') or []) or 'none found'}")
            st.write(f"Dashboards: {', '.join(info.get('dashboards') or []) or 'none'}")
            for e in info.get("errors", []):
                st.error(e)
            if info.get("warnings"):
                with st.expander(f"{len(info['warnings'])} warnings"):
                    for w in info["warnings"]:
                        st.write(f"- {w}")


def page_runs() -> None:
    st.header("Runs")
    runs = api("GET", "/api/runs")
    if not runs:
        st.info("No runs yet. Start one under **New validation**.")
        return
    names = {p["id"]: p["name"] for p in api("GET", "/api/packages")}
    for r in runs:
        cols = st.columns([3, 2, 2, 2, 2, 1])
        cols[0].markdown(f"**{names.get(r['package_id'], r['package_id'])}**")
        cols[1].write("AI review" if r["mode"] == "agent" else "Quick check")
        cols[2].write(when(r["created"]))
        cols[3].write(verdict_badge(r) if r["status"] == "done" else r["status"])
        cols[4].write(duration(r.get("elapsed_seconds")))
        if cols[5].button("Open", key=f"open-{r['id']}"):
            open_run(r["id"])
            st.rerun()


def verdict_badge(r: dict) -> str:
    color, _ = VERDICT_STYLE.get(r.get("verdict"), ("gray", ""))
    return f":{color}[**{r.get('verdict')}**]"


def page_run(rid: str) -> None:
    if st.button("← All runs"):
        go("runs")
        st.rerun()
    try:
        r = api("GET", f"/api/runs/{rid}")
    except RuntimeError:
        st.error("This run does not exist.")
        return
    package = api("GET", f"/api/packages/{r['package_id']}")
    st.header(package["name"])
    st.caption(f"{'AI review' if r['mode'] == 'agent' else 'Quick check'} · started {when(r.get('started'))} · "
               f"run {r['id']}")
    if r["status"] in ("queued", "running"):
        wait_for(rid)
    elif r["status"] == "failed":
        st.error(f"The run failed: {r.get('error')}")
    else:
        report(r)
        chat(rid)


@st.fragment(run_every=5)
def wait_for(rid: str) -> None:
    """Polls while the run is going; the whole page reloads once it is finished."""
    r = api("GET", f"/api/runs/{rid}")
    if r["status"] not in ("queued", "running"):
        st.rerun()
    st.info(f"**{r['status'].capitalize()}** · {duration(r.get('elapsed_seconds'))}. A full validation usually "
            "takes a few minutes; this page updates by itself.")


def report(r: dict) -> None:
    rep = r.get("report") or {}
    color, meaning = VERDICT_STYLE.get(r["verdict"], ("gray", ""))
    st.markdown(f"### :{color}[{r['verdict']}]  \n{meaning}")
    c = rep.get("counts", {})
    cols = st.columns(5)
    for col, label, key in zip(cols, ["Blockers failing", "Blockers unverified", "Warnings", "Low / info", "Proven"],
                               ["blocker_fail", "blocker_unverified", "warning", None, "pass"]):
        col.metric(label, c.get(key, 0) if key else c.get("low", 0) + c.get("info", 0))

    cmp = r.get("comparison")
    if cmp:
        with st.container(border=True):
            st.markdown(f"**Since the previous run** ({cmp['previous_verdict']} → {cmp['verdict']})")
            a, b, d = st.columns(3)
            a.markdown(f"✅ **Fixed: {len(cmp['fixed'])}**\n" + "".join(f"\n- {f['title']}" for f in cmp["fixed"]))
            b.markdown(f"⏳ **Still open: {len(cmp['still_open'])}**\n"
                       + "".join(f"\n- {f['title']}" for f in cmp["still_open"]))
            d.markdown(f"🆕 **New: {len(cmp['new'])}**\n" + "".join(f"\n- {f['title']}" for f in cmp["new"]))

    if rep.get("developer_notes"):
        with st.container(border=True):
            st.markdown("**Your notes** (context for the review; nothing passes because of them)")
            st.write(rep["developer_notes"])
    if rep.get("summary"):
        st.subheader("Summary")
        st.markdown(rep["summary"])

    findings = rep.get("findings", [])
    groups = [("Blockers failing", lambda f: f["severity"] == "BLOCKER" and f["status"] == "FAIL", True),
              ("Unverified", lambda f: f["status"] == "UNVERIFIED", True),
              ("Warnings", lambda f: f["severity"] == "WARNING" and f["status"] == "FAIL", True),
              ("Low / info", lambda f: f["severity"] in ("LOW", "INFO") and f["status"] == "FAIL", False),
              ("Proven", lambda f: f["status"] == "PASS", False)]
    for title, test, expanded in groups:
        items = [f for f in findings if test(f)]
        if not items:
            continue
        st.subheader(f"{title} ({len(items)})")
        if title == "Proven":
            with st.expander("What was proven identical"):
                for f in items:
                    st.write(f"✓ `{f['check']}` {f['title']}")
            continue
        for f in items:
            finding(f, expanded)

    st.divider()
    a, b, c3 = st.columns(3)
    a.download_button("Report (Markdown)", api("GET", f"/api/runs/{r['id']}/report.md"),
                      file_name=f"{rep.get('package_id', 'report')}-{r['id']}.md")
    b.download_button("Report (JSON)", httpx.get(f"{API}/api/runs/{r['id']}/report.json", timeout=60).content,
                      file_name=f"{rep.get('package_id', 'report')}-{r['id']}.json")
    if r.get("trace_url"):
        c3.link_button("Trace (Langfuse)", r["trace_url"], help="How the run reached this result: for maintainers")


def finding(f: dict, expanded: bool) -> None:
    icon = STATUS_ICON.get(f["status"], "")
    head = f"{icon} {f['title']}  ·  {f['check']} · {f['severity']}" + (" · latent" if f.get("latent") else "")
    with st.expander(head, expanded=expanded):
        cols = st.columns(2)
        for col, side, label in ((cols[0], "thoughtspot", "ThoughtSpot"), (cols[1], "strategy", "Strategy")):
            ref = f.get(side)
            with col.container(border=True):
                st.markdown(f"**{label}**")
                if not ref:
                    st.write("–")
                    continue
                st.write(f"`{ref.get('model')}::{ref.get('name')}`" if ref.get("model") else f"`{ref.get('name')}`")
                if ref.get("location"):
                    st.caption(ref["location"])
                if ref.get("definition"):
                    st.code(ref["definition"], language="sql")
        ev = {k: v for k, v in (f.get("evidence") or {}).items() if k != "data_probes"}
        if ev.get("explanation"):
            st.markdown(ev.pop("explanation"))
        for key, value in ev.items():
            values = value if isinstance(value, list) else [value]
            st.markdown(f"**{key.replace('_', ' ').capitalize()}**" + "".join(f"\n- {v}" for v in values))
        if f.get("symptoms"):
            st.markdown("**Also affects:** " + ", ".join(f["symptoms"]))
        if f.get("needs"):
            st.markdown("**To settle it:** " + ", ".join(f["needs"]))
        if f.get("suggested_fix"):
            st.success(f"**Fix:** {f['suggested_fix']}")
        st.caption(f"Decided by {f.get('decided_by')}")


def chat(rid: str) -> None:
    st.subheader("Ask about this run")
    for q in api("GET", f"/api/runs/{rid}/questions"):
        st.chat_message("user").write(q["question"])
        st.chat_message("assistant").markdown(q["answer"])
    question = st.chat_input("e.g. Why does Net Revenue fail? What exactly should I change?")
    if question:
        st.chat_message("user").write(question)
        with st.spinner("The Inspector is looking into it ..."):
            try:
                answer = api("POST", f"/api/runs/{rid}/questions", json={"question": question}, timeout=900)
            except RuntimeError as exc:
                st.error(str(exc))
                return
        st.chat_message("assistant").markdown(answer["answer"])


# -- layout -----------------------------------------------------------------------------------
if "page" not in st.session_state:
    if "run" in st.query_params:
        st.session_state.page, st.session_state.run_id = "run", st.query_params["run"]
    else:
        st.session_state.page = "new"

with st.sidebar:
    st.title("🔎 Inspector")
    st.caption("Checks that a Strategy migration reproduces every ThoughtSpot number.")
    if st.button("New validation", use_container_width=True):
        go("new")
    if st.button("Runs", use_container_width=True):
        go("runs")

page = st.session_state.page
if page == "run":
    page_run(st.session_state.run_id)
elif page == "runs":
    page_runs()
else:
    page_new()
