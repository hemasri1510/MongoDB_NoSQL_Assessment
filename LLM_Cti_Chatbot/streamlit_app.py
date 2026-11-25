"""
Final Assessment (A.5): Streamlit Chat Frontend for ThreatGuard AI
Intelligent multi-model automatic failover across verified FREE Google Gemini models.
"""

import html
import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="ThreatGuard AI - CTI Chatbot", page_icon="🛡️", layout="wide")


st.markdown("""
<style>
:root { color-scheme: light; }
.stApp {
  background:
    radial-gradient(circle at 8% 3%, rgba(190,177,235,.28), transparent 30%),
    radial-gradient(circle at 92% 10%, rgba(255,202,190,.20), transparent 28%),
    #f4f2fb;
  color: #29243e;
}
[data-testid="stHeader"] { background: rgba(244,242,251,.82); }
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #eeebf8 0%, #f8f6fc 100%);
  border-right: 1px solid #ded7ee;
}
h1, h2, h3 { color: #51429d !important; letter-spacing: -.025em; }
p, label, [data-testid="stMarkdownContainer"] { color: #39334f; }
div[data-testid="stChatMessage"] {
  background: rgba(255,255,255,.88);
  border: 1px solid #e3ddef;
  border-radius: 18px;
  padding: 14px 16px;
  box-shadow: 0 8px 24px rgba(55,43,91,.06);
}
div[data-testid="stChatInput"] {
  background: white;
  border: 1px solid #dcd5ea;
  border-radius: 16px;
}
.stButton > button {
  background: linear-gradient(135deg, #6554c0, #8b69c9);
  color: white;
  border: 0;
  border-radius: 12px;
  font-weight: 650;
  padding: .55rem 1rem;
}
.stButton > button:hover { color: white; border: 0; filter: brightness(1.05); }
div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {
  background: white;
  border-color: #dcd5ea;
  border-radius: 11px;
}
hr { border-color: #e3ddef; }
.model-pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 6px;
  background: #ede9fe;
  color: #5b21b6;
  font-size: 0.78rem;
  font-weight: 600;
  margin-top: 4px;
}
.sw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; margin-top: 8px; }
.sw-card { background: #fff; border: 1px solid #e3ddef; border-radius: 14px; padding: 12px 14px; box-shadow: 0 4px 14px rgba(55,43,91,.05); }
.sw-name { font-weight: 700; color: #3b2f7a; font-size: .95rem; word-break: break-word; }
.sw-row { font-size: .8rem; color: #5d5675; margin-top: 3px; word-break: break-word; }
.sw-badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .72rem; font-weight: 700; margin: 5px 0 2px; }
.sw-installed { background: #dcfce7; color: #166534; }
.sw-notinstalled { background: #fee2e2; color: #991b1b; }
.sw-unverified { background: #fef3c7; color: #92400e; }
</style>
""", unsafe_allow_html=True)


def render_software_cards(result):
    """Render software-detection results as cards. All registry-derived text is HTML-escaped."""
    cards = (result or {}).get("cards") or []
    if not cards:
        return
    badge_cls = {"Installed": "sw-installed", "Not Installed": "sw-notinstalled"}
    parts = []
    for c in cards:
        e = lambda v: html.escape(str(v)) if v else "&mdash;"
        status = c.get("status", "Unable to verify")
        extra = f"<div class='sw-row'>Category: {e(c.get('category'))}</div>" if c.get("category") else ""
        parts.append(
            "<div class='sw-card'>"
            f"<div class='sw-name'>{e(c.get('name'))}</div>"
            f"<span class='sw-badge {badge_cls.get(status, 'sw-unverified')}'>{html.escape(status)}</span>"
            f"{extra}"
            f"<div class='sw-row'><b>Version:</b> {e(c.get('version'))}</div>"
            f"<div class='sw-row'><b>Publisher:</b> {e(c.get('publisher'))}</div>"
            f"<div class='sw-row'><b>Scan status:</b> {e(c.get('scan_status'))}</div>"
            "</div>"
        )
    st.markdown("<div class='sw-grid'>" + "".join(parts) + "</div>", unsafe_allow_html=True)


st.title("🛡️ ThreatGuard AI: CTI Threat Intelligence & Remediation")
st.caption("Multi-Model Automatic Failover (FREE Google Gemini API) • MongoDB Vector RAG • Autonomous Scanner")

with st.sidebar:
    st.header("ThreatGuard AI Scope & Filters")
    platform = st.selectbox("Platform", ["All", "Linux", "Windows", "macOS", "Android", "iOS"])
    lifecycle = st.selectbox("Threat Lifecycle", ["All", "Anticipated", "Active", "Evolving", "Resolved"])
    session_id = st.text_input("Session ID", value="cti-analyst-session-01")

    st.divider()
    st.subheader("Model Resilience & Health")
    try:
        hr = requests.get(f"{BACKEND_URL}/health", timeout=3)
        if hr.ok:
            hdata = hr.json()
            st.success(f"System: {hdata.get('system_name', 'ThreatGuard AI')} ({hdata.get('status', 'healthy')})")
            st.caption(f"Primary Model: `{hdata.get('primary_model', 'gemini-3.1-flash-lite')}`")
            avail = hdata.get("available_models", [])
            if avail:
                st.caption(f"Available Free Models: {', '.join(f'`{m}`' for m in avail[:3])}")
        else:
            st.warning("Backend degraded")
    except Exception:
        st.info("Backend health check pending")

    st.divider()
    st.subheader("Background Scanner Service")
    scan_plat = st.selectbox("Scan Platform", ["Linux", "Windows", "macOS", "Android", "iOS"], key="scan_p")
    scan_scope = st.selectbox("Scan Scope", ["system-wide", "project-directory"])
    if st.button("Run Vulnerability Scan", use_container_width=True):
        try:
            r = requests.post(
                f"{BACKEND_URL}/scan",
                json={"platform": scan_plat, "scope": scan_scope},
                timeout=15,
            )
            data = r.json()
            st.success(f"Scan {data['scan_id']} complete ({len(data['findings'])} findings).")
        except Exception as exc:
            st.error(f"Scan failed: {exc}")

    st.divider()
    st.subheader("Windows Software Scanner")
    try:
        sh = requests.get(f"{BACKEND_URL}/installed-software/health", timeout=20).json()
        if sh.get("status") == "ok":
            st.success(f"Scanner ready ({sh.get('scanner_mode')})")
        else:
            st.warning(sh.get("detail", "Scanner unavailable"))
    except Exception:
        st.info("Software scanner status unavailable")
    if st.button("Refresh software inventory", use_container_width=True):
        try:
            inv = requests.get(f"{BACKEND_URL}/installed-software", params={"refresh": "true"}, timeout=60).json()
            st.info(f"Scan {inv.get('scan_status')}: {inv.get('total', 0)} applications")
        except Exception as exc:
            st.error(f"Refresh failed: {exc}")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Welcome to **ThreatGuard AI** — your intelligent Cyber Threat Intelligence and Remediation Assistant. "
                "I operate on an automatic multi-model failover engine using verified free-tier Google Gemini models "
                "(`gemini-3.1-flash-lite`, `gemini-4-hydrogen`, etc.) with MongoDB RAG and platform scanning memory.\n\n"
                "Ask about **Anticipated**, **Active**, **Evolving**, or **Resolved** threats across Linux, Windows, macOS, "
                "Android, and iOS, or reply `CONFIRM FIX <VULN-ID>` to remediate scanner findings."
            ),
        }
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        render_software_cards(msg.get("software_result"))
        if "model_used" in msg:
            st.markdown(f"<span class='model-pill'>🤖 Served by: {msg['model_used']}{' (fallback)' if msg.get('fallback_used') else ''}</span>", unsafe_allow_html=True)

if prompt := st.chat_input("Ask about platform threats, CVEs, IoCs, or type 'CONFIRM FIX VULN-LNX-101'..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        model_used = None
        fallback_used = False
        software_result = None
        try:
            resp = requests.post(
                f"{BACKEND_URL}/chat",
                json={
                    "message": prompt,
                    "session_id": session_id,
                    "platform": platform,
                    "lifecycle": lifecycle,
                },
                timeout=150,  # backend may retry with backoff and automatic failover
            )
            if resp.ok:
                resp_data = resp.json()
                answer = resp_data.get("answer") or "⚠️ The backend returned an empty response."
                model_used = resp_data.get("model")
                fallback_used = resp_data.get("fallback_used", False)
                software_result = resp_data.get("software_result")
            else:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = ""
                answer = f"⚠️ {detail or 'ThreatGuard AI is currently unable to reach the AI service due to temporary high demand. Please try again shortly.'}"
        except Exception as exc:
            answer = f"Backend unreachable ({exc}). Ensure FastAPI is running on `{BACKEND_URL}`."

        st.markdown(answer)
        render_software_cards(software_result)
        if model_used:
            st.markdown(f"<span class='model-pill'>🤖 Served by: {model_used}{' (fallback)' if fallback_used else ''}</span>", unsafe_allow_html=True)

        bot_msg = {"role": "assistant", "content": answer}
        if software_result:
            bot_msg["software_result"] = software_result
        if model_used:
            bot_msg["model_used"] = model_used
            bot_msg["fallback_used"] = fallback_used
        st.session_state.messages.append(bot_msg)
