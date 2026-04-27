"""
app_streamlit.py — interface chat do agente RAG ANEEL.

Recursos:
  - Chat com histórico (últimos N turnos visíveis)
  - Reescrita automática de follow-ups
  - Guardrails visíveis (recusa temporal / escopo com mensagem clara)
  - Fontes clicáveis (expander mostra breadcrumb, página, distância)
  - Indicação de confiança

Uso (requer DB_ADMIN_PASS setado):
    $env:DB_ADMIN_PASS = "..."
    python3.14 -m streamlit run scripts/app_streamlit.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from rag_agent import RAGAgent, AgentResponse, MAX_HISTORY_TURNS, classify_intent

SUGESTOES = [
    "O que é a tarifa branca?",
    "Quem é o Diretor-Geral da ANEEL?",
    "Como funciona a microgeração distribuída?",
    "Qual o procedimento para reclamar da distribuidora?",
]

st.set_page_config(
    page_title="RAG ANEEL",
    page_icon="⚡",
    layout="wide",
)

# ---- Sidebar: info + controles ----
with st.sidebar:
    st.title("⚡ RAG ANEEL")
    st.caption("Legislação do setor elétrico brasileiro (2016, 2021, 2022)")

    st.markdown("---")
    st.markdown("### Arquitetura")
    st.markdown(
        "- **Oracle 23ai Vector Search** (1024 dim)\n"
        "- **Cohere Embed Multilingual v3** (OCI)\n"
        "- **Cohere Command R+ 08-2024** (OCI)\n"
        "- Parent-Child retrieval + RRF fusion\n"
        "- Guardrails temporal + escopo"
    )

    st.markdown("---")
    st.markdown("### Escopo coberto")
    st.markdown("**Anos:** 2016, 2021, 2022\n\n**Tema:** Legislação ANEEL (setor elétrico)")

    st.markdown("---")
    if st.button("🔄 Limpar conversa", use_container_width=True):
        st.session_state.messages = []
        st.session_state.responses = []
        st.rerun()

    if "stats" in st.session_state:
        s = st.session_state.stats
        st.markdown(f"**Turnos:** {s.get('turns', 0)}")
        st.markdown(f"**Latência média:** {s.get('avg_ms', 0):.0f} ms")


# ---- Inicialização de session_state ----
@st.cache_resource
def get_agent():
    return RAGAgent(verbose=False)


if "messages" not in st.session_state:
    st.session_state.messages = []
if "responses" not in st.session_state:
    st.session_state.responses = []
if "stats" not in st.session_state:
    st.session_state.stats = {"turns": 0, "total_ms": 0, "avg_ms": 0}

# Inicializa agent (rápido — bge carrega em background)
try:
    with st.spinner("Conectando ao banco vetorial e ao OCI Generative AI..."):
        agent = get_agent()
except Exception as e:
    st.error(f"Erro ao inicializar agente: {e}")
    st.stop()

# Indicador discreto de aquecimento do bge (módulo já importado no topo)
import rag_agent as _ragmod
_bge_warming = (
    _ragmod._bge_reranker_cache is None
    and hasattr(agent, '_bge_warmup_thread')
    and agent._bge_warmup_thread.is_alive()
)


# ---- Cabeçalho ----
col_title, col_status = st.columns([5, 1])
with col_title:
    st.title("Agente RAG — Legislação ANEEL")
    st.caption("Legislação do setor elétrico (2016, 2021, 2022). Cita fontes e mantém contexto da conversa.")
with col_status:
    if _bge_warming:
        st.caption("⏳ aquecendo BGE")
    else:
        st.caption("⚡ pronto")


# ---- Sugestões clicáveis (só na 1ª tela, antes de qualquer mensagem) ----
if not st.session_state.messages:
    st.markdown("**Algumas perguntas pra começar:**")
    cols_sug = st.columns(2)
    for i, sug in enumerate(SUGESTOES):
        with cols_sug[i % 2]:
            if st.button(sug, key=f"sug_{i}", use_container_width=True):
                st.session_state.pending_query = sug
                st.rerun()


# ---- Renderizar histórico ----
for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant" and idx // 2 < len(st.session_state.responses):
            resp = st.session_state.responses[idx // 2]
            if resp and not resp.refused:
                with st.expander(f"📚 Fontes consultadas ({len(resp.sources)})"):
                    for i, s in enumerate(resp.sources, start=1):
                        score = (f"rerank {s.rerank_score:.3f}" if s.rerank_score is not None
                                 else f"dist {s.vector_dist:.3f}" if s.vector_dist is not None
                                 else "")
                        doc_ref = s.registro_titulo or s.pdf_id[:50]
                        st.markdown(
                            f"**[{i}]** {doc_ref}  \n"
                            f"_{s.breadcrumb}_ • pg.{s.page_start} • {s.chunk_type} • {score}"
                        )
            if resp:
                cols = st.columns([1, 1, 1])
                with cols[0]:
                    st.caption(f"⏱ {resp.elapsed_ms} ms")
                with cols[1]:
                    if resp.confidence > 0:
                        st.caption(f"🎯 confiança {resp.confidence:.0%}")
                with cols[2]:
                    if resp.rewritten_query:
                        st.caption(f"🔄 reformulado")


# ---- Input do usuário (chat OU clique em sugestão) ----
prompt = st.chat_input("Pergunte sobre legislação ANEEL...")
if not prompt and st.session_state.get("pending_query"):
    prompt = st.session_state.pop("pending_query")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        history_before = st.session_state.messages[:-1]
        meta_resp = None
        final_resp = None

        # Placeholders pra atualizar incrementalmente
        status_placeholder = st.empty()
        answer_placeholder = st.empty()

        # Status visual diferenciado por intenção (cheap, rodado local)
        intent = classify_intent(prompt, has_history=bool(history_before))
        if intent == "chitchat":
            initial_status = "💬 _Conversa..._"
        elif intent == "meta":
            initial_status = "💬 _Respondendo no contexto da conversa (sem buscar na base)..._"
        else:
            initial_status = "🔵 _Iniciando..._"

        # Mapeamento de phase → texto visual + emoji + step
        # 5 etapas pra dar sensação de progresso durante os ~30s do pipeline
        PHASE_LABELS = {
            "embedding":  ("🔵", "Etapa 1/5 — Embedding da pergunta (Cohere v3)..."),
            "retrieval":  ("🔍", "Etapa 2/5 — Buscando no banco (vetor HNSW + BM25)..."),
            "rerank":     ("🧮", "Etapa 3/5 — Rerankeando candidatos (bge-reranker-v2-m3)..."),
            "expanding":  ("📚", "Etapa 4/5 — Expandindo contexto (Parent-Child)..."),
            "generating": ("✍️", "Etapa 5/5 — Gerando resposta (Cohere Command R+)..."),
        }

        try:
            status_placeholder.markdown(initial_status)
            buf = []

            for evt, payload in agent.answer_stream(prompt, history=history_before):
                if evt == "phase":
                    if intent == "real_question":
                        emoji, label = PHASE_LABELS.get(payload, ("⏳", payload))
                        status_placeholder.markdown(f"{emoji} _{label}_")
                elif evt == "meta":
                    meta_resp = payload
                    if payload.rewritten_query:
                        st.info(f"🔄 Reformulado para busca: _{payload.rewritten_query}_")
                elif evt == "token":
                    buf.append(payload)
                    answer_placeholder.markdown("".join(buf))
                elif evt == "done":
                    final_resp = payload
                    status_placeholder.empty()

            if final_resp is None:
                final_resp = AgentResponse(query=prompt, answer="Erro: stream terminou sem 'done'", refused=True)

            # Caso recusa: mostrar com warning
            if final_resp.refused and final_resp.refusal_reason in ("fora_escopo_temporal", "fora_escopo_tematico"):
                answer_placeholder.empty()
                st.warning(final_resp.answer)
            elif final_resp.refused:
                answer_placeholder.empty()
                st.error(final_resp.answer)
            else:
                answer_placeholder.markdown(final_resp.answer)

        except Exception as e:
            status_placeholder.empty()
            answer_placeholder.empty()
            st.error(f"Erro: {type(e).__name__}: {e}")
            final_resp = AgentResponse(query=prompt, answer=f"Erro interno: {e}", refused=True)

        # Badge específico pra meta-conversa
        if final_resp.refusal_reason == "meta_conversa":
            st.caption("💬 Resposta com base no histórico da conversa (sem nova consulta à base).")
        elif final_resp.refusal_reason == "chitchat":
            pass  # silencioso, já é óbvio que é conversa

        # Fontes em expander (só pra perguntas reais)
        if final_resp.sources and not final_resp.refused:
            with st.expander(f"📚 Fontes consultadas ({len(final_resp.sources)})"):
                for i, s in enumerate(final_resp.sources, start=1):
                    score = (f"rerank {s.rerank_score:.3f}" if s.rerank_score is not None
                             else f"dist {s.vector_dist:.3f}" if s.vector_dist is not None
                             else "")
                    doc_ref = s.registro_titulo or s.pdf_id[:50]
                    st.markdown(
                        f"**[{i}]** {doc_ref}  \n"
                        f"_{s.breadcrumb}_ • pg.{s.page_start} • {s.chunk_type} • {score}"
                    )

        cols = st.columns([1, 1, 1])
        with cols[0]:
            st.caption(f"⏱ {final_resp.elapsed_ms} ms")
        with cols[1]:
            if final_resp.confidence > 0:
                st.caption(f"🎯 confiança {final_resp.confidence:.0%}")
        with cols[2]:
            if final_resp.rewritten_query:
                st.caption(f"🔄 reformulado")

    st.session_state.messages.append({"role": "assistant", "content": final_resp.answer})
    st.session_state.responses.append(final_resp)

    st.session_state.stats["turns"] += 1
    st.session_state.stats["total_ms"] += final_resp.elapsed_ms
    st.session_state.stats["avg_ms"] = (
        st.session_state.stats["total_ms"] / st.session_state.stats["turns"]
    )
