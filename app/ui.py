import streamlit as st, requests, os
API = os.getenv("RAG_API_URL", "http://localhost:8000")
st.set_page_config(page_title="SecureRAG", page_icon="🔐", layout="wide")
st.title("SecureRAG | Company Knowledge Assistant")
st.caption("Development UI — demo identity only. Never expose demo header authentication publicly.")
with st.sidebar:
    st.header("Demo identity")
    user = st.selectbox("Sign in as", ["alice", "finn", "hannah", "maya", "ceo", "admin"])
    role = {"alice":"employee", "finn":"finance", "hannah":"hr", "maya":"marketing", "ceo":"executive", "admin":"admin"}[user]
    st.info(f"Role: {role}")
    st.caption("Production: replace with Microsoft Entra ID / OIDC.")
if "messages" not in st.session_state: st.session_state.messages = []
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("Sources and excerpts"):
                for src in msg["sources"]: st.write(f"**{src['filename']}** · {src['department']} · {src['classification']}"); st.caption(src["excerpt"])
question = st.chat_input("Ask about company policies, processes, and authorized documents")
if question:
    st.session_state.messages.append({"role":"user", "content":question})
    with st.chat_message("user"): st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Checking access and searching approved documents..."):
            try:
                res = requests.post(f"{API}/chat", headers={"X-User":user,"X-Role":role}, json={"question":question}, timeout=90)
                if res.ok:
                    data = res.json(); st.markdown(data["answer"])
                    st.caption(f"Request: {data['request_id']} · estimated cost ${data['estimated_cost_usd']:.6f} · guardrail: {data['guardrail']}")
                    if data["sources"]:
                        with st.expander("Sources and excerpts"):
                            for src in data["sources"]: st.write(f"**{src['filename']}** · {src['department']} · {src['classification']}"); st.caption(src["excerpt"])
                    st.session_state.messages.append({"role":"assistant","content":data["answer"],"sources":data["sources"]})
                else:
                    msg = f"Service error ({res.status_code}): {res.text}"; st.error(msg); st.session_state.messages.append({"role":"assistant","content":msg})
            except requests.RequestException as e:
                msg = f"Cannot reach API: {e}"; st.error(msg); st.session_state.messages.append({"role":"assistant","content":msg})
