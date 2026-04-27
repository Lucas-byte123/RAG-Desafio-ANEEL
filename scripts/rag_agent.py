"""
rag_agent.py — Agente RAG para legislação ANEEL com 5 camadas de guardrails.

Camadas:
  1. Pre-check TEMPORAL  — se query menciona ano ≠ {2016,2021,2022}, recusa
  2. Pre-check ESCOPO    — se query é sobre tema off-topic (petróleo, saúde, bacen...), recusa
  3. Retrieval HÍBRIDO   — vector search + BM25 fundidos via Reciprocal Rank Fusion
  4. Rerank              — Cohere Rerank v3 melhora precisão do top-K
  5. Prompt + validação  — LLM obrigado a citar fontes; checagem pós-geração

Uso:
    $env:DB_ADMIN_PASS = "..."
    python scripts/rag_agent.py "Quem preside a CEL?"
    python scripts/rag_agent.py --interactive
"""

from __future__ import annotations

import argparse
import array
import os
import re
import sys
import threading as _threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import oci
import oracledb

from _logger import JsonLogger, new_request_id

ROOT = Path(__file__).resolve().parent.parent
WALLET_DIR = ROOT / ".secrets" / "wallet"
WALLET_PASS_FILE = ROOT / ".secrets" / "wallet.pass"

DSN = "aneelrag_medium"
USER = "ADMIN"

# Logger module-level pra eventos de funções standalone (rerank, bge load, etc).
# Funções de classe usam self._jlog. Esse logger compartilha o mesmo arquivo JSONL.
_module_jlog = JsonLogger(ROOT / "logs" / "agent.jsonl")

ANOS_COBERTOS = {2016, 2021, 2022}

# Configuração dos guardrails
DIST_THRESHOLD_NO_CONFIDENCE = 0.62   # acima disso: recusa após rerank
DIST_TOP1_OFFTOPIC = 0.50             # top-1 vetorial pior que isso → suspeito (queries boas: 0.29-0.45)
GAP_THRESHOLD = 0.05                  # se gap top1→top10 < isso E top1 > DIST_TOP1_OFFTOPIC → off-topic
RERANK_OFFTOPIC_THRESHOLD = 0.02      # mais permissivo: 0.05 estava recusando alguns válidos
VECTOR_K = 15                         # intermediário: 12 era pouco, 20 trouxe ruído
BM25_K = 15
RRF_K_CONST = 60
RERANK_TOP_N = 5
RERANK_INPUT_K = 25                   # mantido: chunk gabarito tem mais chance de entrar
MAX_CONTEXT_CHARS = 20000  # subido de 12000: parents do _expand_to_parents são ~3x maiores que children, evidência relevante do top-2/3 não pode ser cortada


# ─── Glossário ANEEL — expansão de siglas pra ajudar embedding ───
# Embeddings são fracos pra siglas únicas (3-4 chars). Expandir antes do search.
ANEEL_GLOSSARY = {
    "AIR": "Análise de Impacto Regulatório",
    "GD": "geração distribuída microgeração minigeração",
    "PLD": "Preço de Liquidação das Diferenças",
    "CCEE": "Câmara de Comercialização de Energia Elétrica",
    "ONS": "Operador Nacional do Sistema Elétrico",
    "TUSD": "Tarifa de Uso do Sistema de Distribuição",
    "TUST": "Tarifa de Uso do Sistema de Transmissão",
    "TE": "Tarifa de Energia",
    "REN": "Resolução Normativa",
    "REH": "Resolução Homologatória",
    "REA": "Resolução Autorizativa",
    "DSP": "Despacho",
    "PRT": "Portaria",
    "OFC": "Ofício",
    "ACR": "Ambiente de Contratação Regulada",
    "ACL": "Ambiente de Contratação Livre",
    "CDE": "Conta de Desenvolvimento Energético",
    "RGR": "Reserva Global de Reversão",
    "RBNI": "Rede Básica de Novas Instalações",
    "PROINFA": "Programa de Incentivo às Fontes Alternativas",
    "MIGDI": "Microssistemas Isolados de Geração e Distribuição de Energia Elétrica",
    "SIGFI": "Sistema Individual de Geração de Energia Elétrica com Fontes Intermitentes",
    "SCG": "Superintendência de Concessões e Autorizações de Geração",
    "SRD": "Superintendência de Regulação dos Serviços de Distribuição",
    "SGT": "Superintendência de Gestão Tarifária",
    "SEL": "Secretaria Executiva de Leilões",
    "CEL": "Comissão Especial de Licitação",
    "PCH": "Pequena Central Hidrelétrica",
    "DRS": "Despacho de Requerimento de Solicitação",
    "DRI": "Despacho de Registro de Intenção",
    "UHE": "Usina Hidrelétrica",
    "UTE": "Usina Termelétrica",
    "EOL": "Eólica",
    "FV": "Fotovoltaica solar",
}


# Mapeia gírias e termos coloquiais brasileiros para a terminologia técnica
# usada na legislação ANEEL. Crítico pra usuários leigos: "gato de luz" jamais
# vai aparecer literalmente nos PDFs, mas "fraude" / "ligação clandestina" sim.
COLLOQUIAL_EXPANSIONS = {
    # furto / fraude de energia
    "gato de luz": "fraude irregularidade ligação clandestina furto energia",
    "gato de energia": "fraude irregularidade ligação clandestina furto energia",
    "roubar luz": "fraude procedimento irregular furto energia",
    "roubar energia": "fraude procedimento irregular furto energia",
    "rouba luz": "fraude procedimento irregular furto energia",
    "rouba energia": "fraude procedimento irregular furto energia",
    "roubam luz": "fraude procedimento irregular furto energia",
    "roubam energia": "fraude procedimento irregular furto energia",
    "roubo de luz": "fraude procedimento irregular furto energia",
    "roubo de energia": "fraude procedimento irregular furto energia",
    "furto de luz": "fraude procedimento irregular furto energia",
    "furto de energia": "fraude procedimento irregular furto energia",
    "puxar luz": "fraude ligação clandestina",
    "ligação clandestina": "fraude procedimento irregular",
    # conta / tarifa
    "conta de luz": "tarifa fatura unidade consumidora",
    "conta de energia": "tarifa fatura unidade consumidora",
    "luz cara": "tarifa preço alto reajuste",
    "energia cara": "tarifa preço alto reajuste",
    # baixa renda / tarifa social
    "pessoa pobre": "baixa renda subclasse residencial tarifa social",
    "quem é pobre": "baixa renda subclasse residencial tarifa social",
    "famílias pobres": "baixa renda subclasse residencial tarifa social",
    # liderança da ANEEL (cargo "presidente" não existe — é Diretor-Geral)
    "presidente da aneel": "Diretor-Geral da ANEEL",
    "chefe da aneel": "Diretor-Geral ANEEL",
    "comanda a aneel": "Diretor-Geral ANEEL Diretores",
    "dirige a aneel": "Diretor-Geral ANEEL Diretores",
    "lidera a aneel": "Diretor-Geral ANEEL",
    "manda na aneel": "Diretor-Geral ANEEL",
    # geração distribuída coloquial
    "energia solar em casa": "geração distribuída microgeração fotovoltaica",
    "painel solar": "fotovoltaica geração distribuída microgeração",
    "placa solar": "fotovoltaica geração distribuída microgeração",
    "minha placa solar": "microgeração distribuída unidade consumidora",
    # reclamação / ouvidoria
    "reclamar da": "ouvidoria reclamação atendimento",
    # apagão / interrupção
    "apagão": "interrupção fornecimento energia continuidade DEC FEC",
    "ficou sem luz": "interrupção fornecimento continuidade DEC FEC",
    "luz cortada": "suspensão fornecimento corte",
    "religar a luz": "religação restabelecimento fornecimento prazo",
    "religar luz": "religação restabelecimento fornecimento prazo",
    "religar energia": "religação restabelecimento fornecimento prazo",
    "voltar a luz": "religação restabelecimento fornecimento",
    "ficou sem energia": "interrupção fornecimento continuidade DEC FEC",
    "queda de energia": "interrupção fornecimento qualidade tensão",
    "oscilação de energia": "qualidade tensão variação fornecimento",
    "tensão baixa": "nível tensão fornecimento qualidade DRC",
    # fatura / valores
    "conta veio errada": "revisão fatura inconsistência erro faturamento",
    "fatura errada": "revisão fatura inconsistência erro faturamento",
    "fatura inflada": "valor cobrado revisão fatura faturamento aferição",
    "conta veio alta": "revisão fatura faturamento aferição valor cobrado",
    "conta muito alta": "revisão fatura faturamento aferição valor cobrado",
    "conta absurda": "revisão fatura faturamento aferição valor cobrado",
    "cobrança indevida": "ressarcimento devolução cobrança incorreta",
    "valor cobrado errado": "ressarcimento revisão fatura cobrança",
    # bandeiras tarifárias
    "bandeira tarifária": "bandeira tarifária verde amarela vermelha escassez hídrica",
    "bandeira vermelha": "bandeira tarifária vermelha adicional R$/kWh",
    "bandeira amarela": "bandeira tarifária amarela adicional R$/kWh",
    "bandeira verde": "bandeira tarifária verde sem adicional",
    # horários e modalidades
    "horário ponta": "posto tarifário ponta intermediário fora ponta tarifa branca",
    "horário de pico": "posto tarifário ponta intermediário fora ponta",
    "tarifa diferente por horário": "tarifa branca posto tarifário ponta",
    "modalidade tarifária": "tarifa convencional branca grupo A grupo B",
    # consumidor / direitos
    "direitos do consumidor": "consumidor direitos deveres unidade consumidora",
    "como reclamar": "ouvidoria reclamação ANEEL agência reguladora",
    "reclamar na aneel": "ANEEL reclamação ouvidoria agência reguladora",
    # medidor / leitura
    "medidor": "medidor energia ativa medição faturamento",
    "leitura do medidor": "leitura medidor faturamento aferição",
    "medidor errado": "aferição medidor verificação inspeção",
    # outros termos comuns
    "subsídio": "subsídio cruzado tarifa social baixa renda",
    "auxílio na conta": "tarifa social baixa renda subsídio benefício",
    "energia limpa": "fontes renováveis solar eólica hídrica biomassa",
    "energia renovável": "fontes renováveis solar eólica hídrica biomassa cogeração",
    "carro elétrico": "veículo elétrico recarga eletroposto mobilidade",
    "ponto de recarga": "eletroposto recarga veículo elétrico mobilidade",
}


def expand_query(query: str) -> str:
    """Expande siglas e coloquialismos para vocabulário técnico do corpus.
    Versão para vector search e BM25: ANEXA os termos técnicos para maximizar recall.
    - Siglas (AIR, GD, ...) viram 'AIR (Análise de Impacto Regulatório)'.
    - Coloquialismos ('gato de luz') ficam 'gato de luz (fraude irregularidade ...)'."""
    expanded = query
    for sigla, expansion in ANEEL_GLOSSARY.items():
        if re.search(rf"\b{sigla}\b", query):
            expanded = expanded + f" ({expansion})"
    q_lower = query.lower()
    for colloquial, technical in COLLOQUIAL_EXPANSIONS.items():
        if colloquial in q_lower:
            expanded = expanded + f" ({technical})"
    return expanded


def expand_query_for_rerank(query: str) -> str:
    """Versão para BGE rerank: SUBSTITUI o coloquial pelo técnico em vez de anexar.
    Anexar ('gato de luz fraude...') confunde BGE: o termo gírioso ('gato')
    domina a comparação semântica e os chunks técnicos ficam com score baixo.
    Substituir ('fraude irregularidade ligação clandestina furto energia')
    deixa o BGE casar diretamente com o vocabulário do corpus."""
    out = query
    q_lower = out.lower()
    for colloquial, technical in COLLOQUIAL_EXPANSIONS.items():
        if colloquial in q_lower:
            pattern = re.compile(re.escape(colloquial), re.IGNORECASE)
            out = pattern.sub(technical, out)
    for sigla, expansion in ANEEL_GLOSSARY.items():
        if re.search(rf"\b{sigla}\b", out):
            out = out + f" ({expansion})"
    return out


# ─── HyDE: Hypothetical Document Embedding ───
# Pra queries vagas, geramos uma "resposta hipotética" curta com vocabulário técnico.
# Embed dessa resposta + query original melhora retrieval.
HYDE_PROMPT = """Você é especialista em legislação ANEEL (setor elétrico brasileiro).
Responda a pergunta abaixo em UMA OU DUAS FRASES CURTAS, usando vocabulário técnico do setor (resoluções, normas, agentes regulatórios). NÃO cite fontes, não use marcadores. Apenas o conteúdo direto da resposta.

Pergunta: {query}
Resposta técnica concisa:"""


ANEEL_DOMAIN_TERMS = {
    "aneel", "energia", "tarifa", "tarifária", "tarifário",
    "distribuidora", "distribuição", "geração", "geradora", "transmissão",
    "leilão", "resolução", "normativa", "portaria", "despacho",
    "consumidor", "unidade consumidora", "concessão", "concessionária",
    "elétrica", "elétrico", "microgeração", "minigeração",
    "solar", "eólica", "hidrelétrica", "termelétrica", "fotovoltaica",
    "regulatório", "regulação", "regulamento", "regulamentação",
    "setor elétrico", "CCEE", "ONS", "PLD", "CDE", "RGR",
    "bandeira", "compensação", "conexão", "medição",
    "diretor", "diretora", "presidente", "agência", "autarquia",
    "ccee", "ons", "fiscalização", "operação", "subestação",
}


def has_domain_terms(query: str) -> bool:
    """Query tem termo do domínio ANEEL (case-insensitive)?"""
    q = query.lower()
    return any(term in q for term in ANEEL_DOMAIN_TERMS)


def is_query_vague(query: str) -> bool:
    """Query 'vaga' que se beneficia de HyDE:
    - Tem termo do domínio ANEEL (pra não rodar HyDE em off-topics)
    - Curta-média, sem números ou siglas únicas (que já dariam bom embed)
    """
    if not has_domain_terms(query):
        return False  # off-topic ou sem domínio → skip HyDE
    if re.search(r"\b\d{2,}\b", query):
        return False  # tem número
    if re.search(r"\b[A-Z]{3,}\b", query):
        return False  # tem sigla
    if len(query.split()) > 12:
        return False
    return True


# Classificador de intenção: chitchat / meta-conversa / pergunta real.
# Roda antes do retrieval pra evitar 30s de pipeline em "obrigado", "ok", "explica melhor".

# Regex MÍNIMA pra casos triviais (~0ms). O resto é classificado por similarity
# de embedding (ver _classify_intent_semantic). Princípio: lista pequena, fixa,
# que captura saudação/agradecimento/despedida óbvios. Variações coloquiais
# (bom demais, show de bola, fera, irado, etc) não entram aqui — vão pelo
# embedding semântico, que captura SIGNIFICADO, não texto literal.
_CHITCHAT_PATTERNS = [
    re.compile(r"^\s*(oi|olá|ola|hey|hi|hello|e a[ií]|opa|bom dia|boa tarde|boa noite)[!.\s,?]*$", re.IGNORECASE),
    re.compile(r"^\s*(obrigad[oa]|muito obrigad[oa]|valeu|vlw|tks|thanks|grato|grata)[!.\s,?]*$", re.IGNORECASE),
    re.compile(r"^\s*(tchau|at[ée] (mais|logo|breve)|fui)[!.\s,?]*$", re.IGNORECASE),
    re.compile(r"^\s*(ok|okay|certo|beleza|blz|sim|claro)[!.\s,?]*$", re.IGNORECASE),
]


# Exemplos canônicos por categoria — usados no classificador semântico via
# embedding similarity. Cobrem variações coloquiais sem precisar regex pra cada.
_INTENT_EXAMPLES = {
    "chitchat": [
        # elogios genéricos
        "muito bom",
        "bom demais",
        "ótima resposta",
        "ficou perfeito",
        "show de bola",
        "isso aí",
        "exatamente",
        "parabéns pela resposta",
        "gostei muito",
        "ajudou bastante",
        "adorei",
        "irado",
        "fera",
        "massa demais",
        # confirmações elaboradas
        "entendi tudo",
        "ficou claro agora",
        "saquei",
        "deu certo",
        "funcionou",
        # agradecimentos elaborados
        "muito obrigado pela ajuda",
        "agradeço a explicação",
    ],
    "meta": [
        "explica melhor",
        "pode repetir",
        "elabore um pouco mais",
        "resume isso",
        "não entendi direito",
        "diga novamente",
        "qual foi a última pergunta",
        "o que você falou antes",
        "continue",
        "prossiga",
    ],
    "real_question": [
        "como funciona a tarifa branca",
        "qual a definição de microgeração distribuída",
        "quem é o Diretor-Geral da ANEEL",
        "o que é PLD",
        "como reclamar da distribuidora",
        "qual o procedimento de revisão de fatura",
        "quanto custa a tarifa social",
        "o que mudou na geração distribuída",
    ],
}

_META_PATTERNS = [
    re.compile(r"\b(pode|poderia|consegue) repetir\b", re.IGNORECASE),
    re.compile(r"\brepete\b|\bdiga (de )?novo\b|\bdiz de novo\b", re.IGNORECASE),
    re.compile(r"\b(explica|explique|elabore|detalhe|aprofunde) (melhor|mais|novamente|isso|esse|esta|este)\b", re.IGNORECASE),
    re.compile(r"\b(resume|resumindo|em resumo|tldr|tl;dr)\b", re.IGNORECASE),
    re.compile(r"\b(n[ãa]o entendi|n[ãa]o ficou claro|n[ãa]o compreendi)\b", re.IGNORECASE),
    re.compile(r"^\s*(continue|continua|prossiga|vai|pode continuar)\b[!.\s,?]*", re.IGNORECASE),
    re.compile(r"\b(qual|sobre o que) (foi|era) (a|sua|minha) (pergunta|resposta|[úu]ltima)\b", re.IGNORECASE),
    re.compile(r"\b(o que você (falou|disse|respondeu))\b", re.IGNORECASE),
    re.compile(r"^\s*(o terceiro|o segundo|o primeiro|o [úu]ltimo|o item \d+|a parte \d+)\b", re.IGNORECASE),
]


def classify_intent(query: str, has_history: bool) -> str:
    """Classifica intenção da query: 'chitchat' | 'meta' | 'real_question'.
    Conservador: em dúvida, retorna 'real_question' (preserva comportamento atual).
    """
    q = query.strip()
    # Chitchat só dispara em mensagens curtas (<= 6 palavras) pra não atrapalhar.
    if len(q.split()) <= 6:
        for p in _CHITCHAT_PATTERNS:
            if p.match(q):
                return "chitchat"
    # Meta-conversa só faz sentido com histórico (referência ao que foi dito antes).
    if has_history:
        for p in _META_PATTERNS:
            if p.search(q):
                # Mas se a query também menciona termo de domínio ANEEL, é pergunta real
                # (ex: "explica melhor a tarifa branca" — quer mais info sobre tarifa branca,
                # não só repetição do que já disse).
                if has_domain_terms(q) and len(q.split()) > 5:
                    return "real_question"
                return "meta"
    return "real_question"


_CHITCHAT_RESPONSES = {
    "saudacao": (
        "Olá! Sou um agente especializado em legislação da ANEEL "
        "(resoluções, portarias, despachos, ofícios e notas técnicas dos "
        "anos 2016, 2021 e 2022). Em que posso ajudar?"
    ),
    "agradecimento": "De nada! Se tiver outra dúvida sobre legislação ANEEL, é só perguntar.",
    "elogio": "Obrigado, fico feliz que ajudou! Pode mandar mais perguntas sobre legislação ANEEL quando quiser.",
    "confirmacao": "Tudo certo. Posso esclarecer mais alguma coisa sobre legislação ANEEL?",
    "despedida": "Até mais. Bom uso da informação regulatória.",
    # Default = mensagem amigável pra qualquer chitchat que chegou via embedding
    # mas não casou regex específica (ex: "fera!", "irado", "massa demais").
    # Como os exemplos canônicos de chitchat são todos elogios/agradecimentos/
    # confirmações, é razoável assumir que é positivo.
    "default": "Obrigado pelo retorno! Se tiver outra dúvida sobre legislação ANEEL, é só perguntar.",
}


def chitchat_response(query: str) -> str:
    q = query.lower().strip()
    if re.match(r"^\s*(oi|olá|ola|hey|hi|hello|e a[ií]|opa|bom dia|boa tarde|boa noite|tudo bem|td bem|como vai|de boa)", q):
        return _CHITCHAT_RESPONSES["saudacao"]
    if re.match(r"^\s*(obrigad|muito obrigad|valeu|vlw|tks|thanks|agrade|grato|grata)", q):
        return _CHITCHAT_RESPONSES["agradecimento"]
    if re.match(r"^\s*(tchau|at[eé]|fui|valew|falou|abra[çc]o)", q):
        return _CHITCHAT_RESPONSES["despedida"]
    # Elogios à resposta
    if re.match(r"^\s*(perfeito|[óo]tim[oa]|excelente|massa|j[óo]ia|joia|show|top|demais|sensacional|maravilh|bom|muito bom|mt bom|mto bom|boa|muito boa|mt boa|mto boa|gostei|adorei|amei|curti|parab[ée]ns|isso|exato|correto|preciso|ficou|funcionou|deu certo|resolvi|ajudou)", q):
        return _CHITCHAT_RESPONSES["elogio"]
    if re.match(r"^\s*(ok|okay|certo|t[áa]|beleza|blz|legal|entendi|entendido|saquei|sim|claro)", q):
        return _CHITCHAT_RESPONSES["confirmacao"]
    return _CHITCHAT_RESPONSES["default"]


META_SYSTEM_PROMPT = (
    "Você é um assistente especializado em legislação ANEEL. O usuário está "
    "se referindo ao histórico da conversa atual (não a uma nova consulta à base). "
    "Responda usando APENAS o histórico fornecido — não invente fatos novos. "
    "Se o usuário pediu para repetir ou reformular, use as informações já "
    "presentes no histórico, sem buscar nada novo. Se o usuário perguntou sobre "
    "algo que não está no histórico, diga 'Esse ponto não foi abordado nas "
    "minhas respostas anteriores. Pode reformular como pergunta direta?'"
)


def build_meta_prompt(query: str, history: list[dict]) -> str:
    parts = ["### HISTÓRICO DA CONVERSA:\n"]
    recent = history[-(MAX_HISTORY_TURNS * 2):]
    for m in recent:
        role = m.get("role", "").upper()
        content = (m.get("content", "") or "")[:1500]
        parts.append(f"{role}: {content}\n")
    parts.append(f"\n### MENSAGEM ATUAL DO USUÁRIO:\n{query}\n")
    parts.append("\n### RESPOSTA (use somente o histórico, não invente):\n")
    return "".join(parts)


def hyde_generate(inf, model_id, tenancy, query: str, max_tokens: int = 100) -> str:
    """Gera resposta hipotética curta pra usar em embedding."""
    try:
        prompt = HYDE_PROMPT.format(query=query)
        from oci.generative_ai_inference.models import (
            ChatDetails, OnDemandServingMode, CohereChatRequest
        )
        req = ChatDetails(
            compartment_id=tenancy,
            serving_mode=OnDemandServingMode(model_id=model_id),
            chat_request=CohereChatRequest(
                message=prompt,
                temperature=0.3,
                max_tokens=max_tokens,
                is_stream=False,
            ),
        )
        resp = inf.chat(req)
        return resp.data.chat_response.text if hasattr(resp.data.chat_response, "text") else ""
    except Exception:
        return ""

# Escopo off-topic: termos que indicam query fora do domínio
OFF_TOPIC_PATTERNS = [
    r"\bbacen\b", r"\bselic\b", r"\bbanco\s+central\b",
    r"\bpetr[oó]leo\b", r"\bg[aá]s\s+natural\b", r"\banp\b",
    r"\bcovid\b", r"\bcorona(v[ií]rus)?\b",
    r"\bimposto\b", r"\bir\b", r"\birpf\b", r"\birpj\b", r"\bicms\b",
    r"\bpreviden[cs][ií]a\b", r"\binss\b", r"\bfgts\b",
    r"\bsa[uú]de\b", r"\bsus\b", r"\beduca[cç][aã]o\b", r"\bmec\b",
    r"\beleitoral\b", r"\btribunal\b", r"\bstf\b", r"\bstj\b",
]
OFF_TOPIC_RE = re.compile("|".join(OFF_TOPIC_PATTERNS), re.IGNORECASE)

# Ano mencionado na query
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


# -------- RESPONSE TYPES --------

@dataclass
class RetrievedChunk:
    chunk_id: str
    parent_chunk_id: str | None
    pdf_id: str
    breadcrumb: str
    chunk_type: str
    page_start: int
    text_raw: str
    text_embed: str
    ano: int | None
    tipo_canonico: str | None
    registro_titulo: str | None = None
    pdf_url: str | None = None  # URL pública do PDF original (manifest.url)
    vector_dist: float | None = None
    bm25_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None


@dataclass
class AgentResponse:
    query: str
    answer: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str = ""
    confidence: float = 0.0
    elapsed_ms: int = 0
    rewritten_query: str | None = None  # quando houve reescrita de follow-up
    invalid_citations: list[str] = field(default_factory=list)  # fontes citadas que não estão nas sources


MAX_HISTORY_TURNS = 5
FOLLOW_UP_SIGNALS = re.compile(
    r"\b(ele|ela|eles|elas|esse|essa|esses|essas|isso|aquele|aquela|"
    r"desse|dessa|deste|desta|nele|nela|tamb[eé]m|"
    r"e\s+(quem|qual|quando|onde|como|por)|"
    r"e\s+o\s+|e\s+a\s+)\b",
    re.IGNORECASE,
)

REWRITE_PROMPT = """Você reformula perguntas de follow-up em perguntas autocontidas.

HISTÓRICO DA CONVERSA:
{history}

NOVA PERGUNTA: {question}

INSTRUÇÕES:
- Se a NOVA PERGUNTA é autocontida (não depende de contexto anterior), retorne ela EXATA.
- Se depende de contexto (pronomes, referências implícitas), reformule-a como uma pergunta autocontida, substituindo pronomes pelos termos explícitos do histórico.
- NÃO adicione informação que não esteja no histórico ou na pergunta.
- Retorne APENAS a pergunta reformulada (sem explicações).

PERGUNTA REFORMULADA:"""


# -------- CONEXÕES --------

def connect_db():
    pwd = os.environ.get("DB_ADMIN_PASS")
    if not pwd:
        sys.exit("ERRO: defina DB_ADMIN_PASS")
    wallet_pwd = WALLET_PASS_FILE.read_text().strip()
    return oracledb.connect(
        user=USER, password=pwd, dsn=DSN,
        config_dir=str(WALLET_DIR),
        wallet_location=str(WALLET_DIR),
        wallet_password=wallet_pwd,
    )


def find_model(mgmt, tenancy, match_fn):
    resp = mgmt.list_models(compartment_id=tenancy)
    for m in resp.data.items:
        if match_fn(m):
            return m
    return None


def _is_embed_multi_v3(m):
    name = (m.display_name or "").lower()
    return name == "cohere.embed-multilingual-v3.0" and m.lifecycle_state == "ACTIVE"


def _is_command_r_plus(m):
    name = (m.display_name or "").lower()
    caps = m.capabilities or []
    return ("command" in name and "r" in name and ("plus" in name or "r-plus" in name)
            and "CHAT" in caps and m.lifecycle_state == "ACTIVE")


def _is_any_chat(m):
    caps = m.capabilities or []
    return "CHAT" in caps and m.lifecycle_state == "ACTIVE"


# -------- GUARDRAILS --------

def check_temporal(query: str) -> tuple[bool, str]:
    """Retorna (ok, razão)."""
    years = YEAR_RE.findall(query)
    if not years:
        return True, ""
    years_full = set()
    for prefix in years:
        for full_y_match in re.finditer(rf"\b{prefix}\d{{2}}\b", query):
            try:
                y = int(full_y_match.group())
                years_full.add(y)
            except ValueError:
                pass
    years_full = {int(full) for full in re.findall(r"\b(?:19|20)\d{2}\b", query)}
    out_of_scope = years_full - ANOS_COBERTOS
    in_scope = years_full & ANOS_COBERTOS
    if out_of_scope and not in_scope:
        anos_str = ", ".join(str(y) for y in sorted(out_of_scope))
        return False, (f"Minha base cobre apenas legislação ANEEL de **2016, 2021 e 2022**. "
                       f"Você mencionou {anos_str}, que está fora do escopo.")
    return True, ""


def check_scope(query: str) -> tuple[bool, str]:
    m = OFF_TOPIC_RE.search(query)
    if m:
        term = m.group()
        return False, (f"Minha base é exclusiva de **legislação ANEEL (setor elétrico brasileiro)**. "
                       f"Sua pergunta menciona '{term}', que está fora deste escopo.")
    return True, ""


# -------- RETRIEVAL --------

# LRU cache pra embeddings de queries — queries repetidas (~30% do tráfego) economizam ~500ms cada
from collections import OrderedDict as _OrderedDict
_embed_cache: "_OrderedDict[tuple[str, str], list[float]]" = _OrderedDict()
_embed_cache_lock = _threading.Lock()
EMBED_CACHE_MAX = 256


def embed_query(inf, model_id, tenancy, query: str):
    key = (model_id, query)
    with _embed_cache_lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)
            return _embed_cache[key]
    req = oci.generative_ai_inference.models.EmbedTextDetails(
        inputs=[query],
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id),
        compartment_id=tenancy,
        input_type="SEARCH_QUERY",
        truncate="END",
    )
    result = inf.embed_text(req).data.embeddings[0]
    with _embed_cache_lock:
        _embed_cache[key] = result
        if len(_embed_cache) > EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return result


def vector_search(cur, query_vec, k=VECTOR_K, filters=None) -> list[RetrievedChunk]:
    qvec = array.array("f", query_vec)
    where_extra = ""
    params = {"qvec": qvec, "k": k}
    if filters:
        if filters.get("ano"):
            where_extra += " AND c.ano = :f_ano"
            params["f_ano"] = filters["ano"]
        if filters.get("tipo"):
            where_extra += " AND c.tipo_canonico = :f_tipo"
            params["f_tipo"] = filters["tipo"]

    sql = f"""
    SELECT c.chunk_id, c.parent_chunk_id, c.pdf_id, c.breadcrumb, c.chunk_type,
           c.page_start, c.text_raw, c.text_embed, c.ano, c.tipo_canonico,
           m.registro_titulo, m.url,
           VECTOR_DISTANCE(v.embedding, :qvec, COSINE) AS dist
    FROM chunks c
    JOIN chunk_vectors v ON v.chunk_id = c.chunk_id
    LEFT JOIN manifest m ON m.pdf_id = c.pdf_id
    WHERE c.chunk_level = 1 {where_extra}
    ORDER BY dist ASC FETCH FIRST :k ROWS ONLY
    """
    cur.execute(sql, params)
    results = []
    for row in cur.fetchall():
        cid, pcid, pdfid, bc, ctype, pg, raw_clob, te, ano, tipo, titulo, url, dist = row
        text_raw = raw_clob.read() if raw_clob and hasattr(raw_clob, "read") else (raw_clob or "")
        results.append(RetrievedChunk(
            chunk_id=cid, parent_chunk_id=pcid, pdf_id=pdfid,
            breadcrumb=bc or "", chunk_type=ctype, page_start=pg,
            text_raw=text_raw, text_embed=te or "",
            ano=ano, tipo_canonico=tipo, registro_titulo=titulo, pdf_url=url,
            vector_dist=float(dist),
        ))
    return results


def bm25_search(cur, query: str, k=BM25_K, filters=None) -> list[RetrievedChunk]:
    q_sanitized = re.sub(r"[^\w\sÀ-ÿ]", " ", query).strip()
    tokens = [t for t in q_sanitized.split() if len(t) >= 3]
    if not tokens:
        return []
    accum_query = " OR ".join(tokens[:10])
    where_extra = ""
    params = {"q": accum_query, "k": k}
    if filters:
        if filters.get("ano"):
            where_extra += " AND c.ano = :f_ano"
            params["f_ano"] = filters["ano"]
        if filters.get("tipo"):
            where_extra += " AND c.tipo_canonico = :f_tipo"
            params["f_tipo"] = filters["tipo"]

    sql = f"""
    SELECT c.chunk_id, c.parent_chunk_id, c.pdf_id, c.breadcrumb, c.chunk_type,
           c.page_start, c.text_raw, c.text_embed, c.ano, c.tipo_canonico,
           m.registro_titulo, m.url,
           SCORE(1) AS s
    FROM chunks c
    LEFT JOIN manifest m ON m.pdf_id = c.pdf_id
    WHERE c.chunk_level = 1 AND CONTAINS(c.text_embed, :q, 1) > 0 {where_extra}
    ORDER BY s DESC FETCH FIRST :k ROWS ONLY
    """
    try:
        cur.execute(sql, params)
    except oracledb.DatabaseError as e:
        if "DRG" in str(e) or "CTX" in str(e):
            return []
        raise
    results = []
    for row in cur.fetchall():
        cid, pcid, pdfid, bc, ctype, pg, raw_clob, te, ano, tipo, titulo, url, score = row
        text_raw = raw_clob.read() if raw_clob and hasattr(raw_clob, "read") else (raw_clob or "")
        results.append(RetrievedChunk(
            chunk_id=cid, parent_chunk_id=pcid, pdf_id=pdfid,
            breadcrumb=bc or "", chunk_type=ctype, page_start=pg,
            text_raw=text_raw, text_embed=te or "",
            ano=ano, tipo_canonico=tipo, registro_titulo=titulo, pdf_url=url,
            bm25_score=float(score),
        ))
    return results


def rrf_fuse(vector_results: list[RetrievedChunk], bm25_results: list[RetrievedChunk],
             k_const=RRF_K_CONST) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion — combina 2 rankings."""
    scores = {}
    lookup = {}
    for rank, c in enumerate(vector_results, start=1):
        scores[c.chunk_id] = scores.get(c.chunk_id, 0) + 1 / (k_const + rank)
        lookup[c.chunk_id] = c
    for rank, c in enumerate(bm25_results, start=1):
        scores[c.chunk_id] = scores.get(c.chunk_id, 0) + 1 / (k_const + rank)
        if c.chunk_id in lookup:
            if c.bm25_score is not None:
                lookup[c.chunk_id].bm25_score = c.bm25_score
        else:
            lookup[c.chunk_id] = c
    for cid, c in lookup.items():
        c.fused_score = scores[cid]
    ordered = sorted(lookup.values(), key=lambda c: -c.fused_score)
    return ordered


def extract_filters_from_query(query: str) -> dict:
    """Extrai filtros (ano) da query. Só filtra por ano quando há exatamente UM
    ano coberto na query — queries com múltiplos anos ("compare 2016 e 2021")
    NÃO filtram, pra não eliminar chunks de outros anos do retrieval."""
    filters = {}
    all_years = re.findall(r"\b(?:19|20)\d{2}\b", query)
    covered_years = {int(y) for y in all_years if int(y) in ANOS_COBERTOS}
    if len(covered_years) == 1:
        filters["ano"] = next(iter(covered_years))
    if re.search(r"resolu[cç][aã]o\s+normativa|\bren\b", query, re.IGNORECASE):
        pass  # podemos filtrar por tipo_canonico mas o vocabulário ainda não tá 100% limpo
    return filters


def rerank_cohere(inf, model_id, tenancy, query: str,
                  chunks: list[RetrievedChunk], top_n=RERANK_TOP_N) -> list[RetrievedChunk]:
    """Rerank usando Cohere Rerank via OCI. Se não disponível, retorna top_n do fused."""
    if not chunks:
        return []
    try:
        texts = [c.text_embed[:2000] for c in chunks]
        RerankDetails = oci.generative_ai_inference.models.RerankTextDetails
        req = RerankDetails(
            input=query,
            documents=texts,
            serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id),
            compartment_id=tenancy,
            top_n=min(top_n, len(chunks)),
            is_echo=False,
        )
        resp = inf.rerank_text(req)
        out = []
        for r in resp.data.document_ranks:
            idx = r.index
            score = r.relevance_score
            c = chunks[idx]
            c.rerank_score = float(score)
            out.append(c)
        return out
    except Exception as e:
        _module_jlog.log("rerank_cohere_failed",
                         error_type=type(e).__name__, error=str(e)[:300],
                         fallback="fusion_score", chunks_returned=min(top_n, len(chunks)))
        print(f"  [rerank cohere falhou: {type(e).__name__}] usando fusion score", file=sys.stderr)
        return chunks[:top_n]


# Cache global do BGE reranker (carrega 1x, custoso). Lock evita 2 downloads paralelos.
_bge_reranker_cache = None
_bge_reranker_lock = _threading.Lock()


def get_bge_reranker():
    global _bge_reranker_cache
    if _bge_reranker_cache is not None:
        return _bge_reranker_cache
    with _bge_reranker_lock:
        if _bge_reranker_cache is not None:
            return _bge_reranker_cache
        try:
            from sentence_transformers import CrossEncoder
            _module_jlog.log("bge_loading", model="BAAI/bge-reranker-v2-m3", max_length=256)
            print("  [bge] carregando BAAI/bge-reranker-v2-m3 (1ª vez ~3min download)...", file=sys.stderr)
            # max_length=256 (em vez de 512) — 2x mais rápido sem perda significativa de qualidade
            # nossa text_embed média é 1037 chars (~260 tokens), bem coberta por 256
            _bge_reranker_cache = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=256)
            _module_jlog.log("bge_loaded", model="BAAI/bge-reranker-v2-m3", max_length=256)
            print("  [bge] reranker carregado (max_length=256)", file=sys.stderr)
            return _bge_reranker_cache
        except Exception as e:
            _module_jlog.log("bge_load_failed",
                             error_type=type(e).__name__, error=str(e)[:300])
            print(f"  [bge] indisponível ({type(e).__name__}: {e})", file=sys.stderr)
            return None


def rerank_bge(query: str, chunks: list[RetrievedChunk],
               top_n=RERANK_TOP_N) -> list[RetrievedChunk]:
    """Rerank local usando bge-reranker-v2-m3 (multilíngue SOTA open-source)."""
    if not chunks:
        return []
    model = get_bge_reranker()
    if model is None:
        return chunks[:top_n]
    try:
        pairs = [[query, c.text_embed[:1500]] for c in chunks]
        scores = model.predict(pairs, show_progress_bar=False)
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:top_n]
        out = []
        for idx, score in ranked:
            c = chunks[idx]
            c.rerank_score = float(score)
            out.append(c)
        return out
    except Exception as e:
        _module_jlog.log("rerank_bge_failed",
                         error_type=type(e).__name__, error=str(e)[:300],
                         fallback="fusion_score", chunks_returned=min(top_n, len(chunks)))
        print(f"  [bge rerank falhou: {type(e).__name__}] usando fusion score", file=sys.stderr)
        return chunks[:top_n]


# -------- PROMPT / LLM --------

SYSTEM_PROMPT = """Você é um assistente especializado em legislação da ANEEL (Agência Nacional de Energia Elétrica, Brasil), com foco em resoluções, portarias, despachos, ofícios e notas técnicas dos anos 2016, 2021 e 2022.

PERFIL DE RESPOSTA:
- Tom técnico e formal, mas NÃO mecânico. Você é um especialista jurídico que SINTETIZA o que a regulação diz, não um copiador de texto legal.
- Cite SEMPRE a fonte de cada afirmação no formato [FONTE: doc_info, pg. X].
- Para qualquer pergunta com pelo menos um trecho relevante ao tema (mesmo que não responda exatamente o que foi perguntado), você DEVE responder explicando o que a regulação cobre sobre aquele assunto. Não recuse só porque o usuário usou palavras diferentes das do texto legal.

EXEMPLOS DE BOAS RESPOSTAS:

Pergunta: Quem preside a Comissão Especial de Licitação?
Resposta: Romário de Oliveira Batista é o Presidente da Comissão Especial de Licitação (CEL) da ANEEL. [FONTE: PRT - PORTARIA 4177/2016, pg.2]

Pergunta: Qual a definição de microgeração distribuída?
Resposta: Microgeração distribuída é a central geradora de energia elétrica com potência instalada menor ou igual a 75 kW, conectada na rede de distribuição por meio de instalações de unidades consumidoras [FONTE: REN 1000/2021, pg.275]. Pode utilizar fontes renováveis (solar, eólica, hídrica, biomassa) ou cogeração qualificada [FONTE: REN 1000/2021, pg.275].

Pergunta: Quem é o presidente da ANEEL?
Resposta: A ANEEL é dirigida por um Diretor-Geral (cargo equivalente a "presidente" no organograma da agência). Conforme decreto de 18 de abril de 2022, Sandoval de Araújo Feitosa Neto foi nomeado Diretor-Geral da ANEEL com mandato até 13 de agosto de 2027, em sucessão a André Pepitone da Nóbrega [FONTE: DEC - DECRETO/2022, pg.1].

Pergunta: Minha conta de luz veio errada, o que fazer?
Resposta (estrutura exemplificativa — use SEMPRE a fonte real do trecho que você recebeu):
A regulação ANEEL prevê procedimento para revisão de faturas pelo consumidor. O consumidor pode solicitar à distribuidora a revisão da fatura quando identificar inconsistências, e a distribuidora deve apurar a reclamação em prazo regulamentado [FONTE: <documento real do trecho, ex: REN 414/2010 ou DSP XXXX/2022, pg.<número real>>]. Caso a resposta da distribuidora não seja satisfatória, é possível registrar reclamação na ANEEL como instância recursal [FONTE: <documento real, pg.<número real>>]. Observação: o agente não tem acesso à sua conta específica — a análise concreta do erro depende do canal de revisão da distribuidora.

Pergunta: Vale a pena instalar painel solar?
Resposta (estrutura exemplificativa — substitua placeholders por dados reais dos trechos):
A análise financeira de instalação fotovoltaica está fora do escopo regulatório. Pelo lado da regulação ANEEL: a microgeração e a minigeração distribuídas são modalidades regulamentadas que permitem ao consumidor gerar a própria energia e participar do Sistema de Compensação de Energia Elétrica (SCEE), com créditos pelos excedentes injetados na rede [FONTE: <doc real, pg. real>]. A Lei nº 14.300/2022 estabeleceu o marco legal e regras de transição [FONTE: <doc real, pg. real>]. A decisão de viabilidade econômica depende de fatores fora da legislação (custo do equipamento, perfil de consumo, preço da tarifa local).


INSTRUÇÕES:

1. Leia TODOS os trechos de contexto antes de responder. Sintetize o que eles dizem em conjunto, não cite isoladamente.

2. **Regra de cobertura (importante):** se algum trecho aborda o tema da pergunta — mesmo que com vocabulário técnico diferente do usado pelo usuário, ou mesmo que apenas parte da pergunta — RESPONDA com base nesse trecho. Use a expressão "a regulação prevê", "o procedimento é", "a norma estabelece" para ancorar a resposta no que está nos trechos. Só recuse a responder quando NENHUM trecho aborda nem tangencialmente o tema.

3. **Perguntas pessoais ou avaliativas** ("é justo?", "vale a pena?", "minha conta veio errada", "quanto vou pagar"): você não dá conselho financeiro, jurídico individual nem analisa caso específico. Mas você DEVE explicar (a) o que a regulação cobre sobre o tema e (b) qual o procedimento ou caminho previsto na norma. Termine deixando explícito o que está fora do seu escopo.

4. Seja específico: cite números, datas, nomes, prazos e valores EXATOS como aparecem nos trechos. Não arredonde.

5. SEMPRE cite a fonte ao final de cada afirmação no formato [FONTE: <doc>, pg. <número>]. Cada parágrafo deve ter pelo menos uma citação. CRÍTICO: a fonte citada deve ser EXATAMENTE um dos documentos listados nos TRECHOS DE CONTEXTO que você recebeu (use o "Documento:" e a "pg." informados em cada trecho). NUNCA copie literalmente os exemplos deste prompt ("REN 1000/2021", "REN 414/2010", "pg.X", etc) — esses são apenas modelos de formato; a fonte real vem dos trechos abaixo. Nunca escreva "pg.X" ou "pg.<número real>" literalmente — use sempre o número concreto da página do trecho.

6. **Documentos compostos:** PDFs de publicação oficial (ex: Diário Oficial) podem conter MÚLTIPLOS atos normativos. Se o trecho menciona "Esta Portaria", "Este Decreto", "Esta Resolução", entenda que o trecho se refere AO documento mencionado no próprio texto, mesmo que o "Documento:" do cabeçalho do trecho seja um ofício ou publicação guarda-chuva.

7. **Cargos de liderança da ANEEL:** "presidente", "chefe", "líder" ou "diretor" da ANEEL = **Diretor-Geral** (ou Diretores) da ANEEL. Não confunda com o "Presidente da República" que assina decretos de nomeação.

8. **Decretos de nomeação:** começam com "O PRESIDENTE DA REPÚBLICA... resolve: NOMEAR/RECONDUZIR ... para exercer o cargo de Diretor/Diretor-Geral da ANEEL". O nome relevante NÃO é o signatário (Presidente da República), e sim o NOMEADO/RECONDUZIDO. Extraia nome, cargo e período de mandato.

9. **Quando recusar:** apenas se nenhum dos trechos sequer tangencia o tema. Nesse caso, diga "Não encontrei na minha base trechos que abordem este tema" — e SUGIRA reformulação ou tema correlato dentro do escopo. Nunca diga "Não encontrei informação suficiente" quando há trechos relevantes — sintetize com o que tem.

10. NÃO invente informações que não estão nos trechos. Se uma informação esperada não está no contexto, diga explicitamente "este aspecto específico não consta nos trechos disponíveis".

11. NÃO responda sobre temas fora de legislação ANEEL do setor elétrico.

12. Português brasileiro formal. Sintetize, não copie literal — explique com suas palavras o que a norma diz, mantendo tom técnico-jurídico.
"""


def build_user_prompt(query: str, chunks: list[RetrievedChunk],
                      history: list[dict] | None = None) -> str:
    parts = []
    if history:
        recent = history[-(MAX_HISTORY_TURNS * 2):]
        if recent:
            parts.append("### HISTÓRICO DA CONVERSA (para manter coerência):\n")
            for m in recent:
                role = m.get("role", "").upper()
                content = (m.get("content", "") or "")[:400]
                parts.append(f"{role}: {content}\n")
            parts.append("\n")

    parts.append("### TRECHOS DE CONTEXTO:\n")
    used_chars = 0
    for i, c in enumerate(chunks, start=1):
        snippet = c.text_raw.strip()
        doc_info = f"Documento: {c.registro_titulo}" if c.registro_titulo else f"Documento: {c.pdf_id}"
        header = (f"\n[{i}] {doc_info} | Seção: {c.breadcrumb} | "
                  f"pg.{c.page_start} | ano={c.ano}\n")
        chunk_str = header + snippet + "\n"
        if used_chars + len(chunk_str) > MAX_CONTEXT_CHARS:
            break
        parts.append(chunk_str)
        used_chars += len(chunk_str)
    parts.append(f"\n### PERGUNTA ATUAL:\n{query}\n")
    parts.append("\n### RESPOSTA (em português, concisa, com [FONTE: ...] em cada afirmação):\n")
    return "".join(parts)


def llm_generate_cohere(inf, model_id, tenancy, system: str, user: str,
                       temperature: float = 0.2, max_tokens: int = 800) -> str:
    """Cohere Command via OCI GenAI chat API (síncrono)."""
    from oci.generative_ai_inference.models import (
        ChatDetails, OnDemandServingMode, CohereChatRequest
    )
    req = ChatDetails(
        compartment_id=tenancy,
        serving_mode=OnDemandServingMode(model_id=model_id),
        chat_request=CohereChatRequest(
            message=user,
            preamble_override=system,
            temperature=temperature,
            max_tokens=max_tokens,
            is_stream=False,
        ),
    )
    resp = inf.chat(req)
    chat_response = resp.data.chat_response
    if hasattr(chat_response, "text"):
        return chat_response.text
    return str(chat_response)


def llm_generate_cohere_stream(inf, model_id, tenancy, system: str, user: str,
                                temperature: float = 0.2, max_tokens: int = 800):
    """Cohere Command streaming. Yields text chunks como aparecem."""
    import json
    from oci.generative_ai_inference.models import (
        ChatDetails, OnDemandServingMode, CohereChatRequest
    )
    req = ChatDetails(
        compartment_id=tenancy,
        serving_mode=OnDemandServingMode(model_id=model_id),
        chat_request=CohereChatRequest(
            message=user,
            preamble_override=system,
            temperature=temperature,
            max_tokens=max_tokens,
            is_stream=True,
        ),
    )
    resp = inf.chat(req)
    # Cada event é um SSE com payload JSON. Eventos finais ecoam o texto completo — ignorar.
    for event in resp.data.events():
        try:
            data = json.loads(event.data)
            if data.get("finishReason") or data.get("isFinished"):
                continue
            text = data.get("text", "")
            if text:
                yield text
        except (ValueError, AttributeError):
            continue


def has_citation(answer: str) -> bool:
    return bool(re.search(r"\[FONTE:", answer, re.IGNORECASE) or re.search(r"\bpg\.\s*\d+", answer))


# Regex pra capturar fontes citadas no padrão [FONTE: TIPO NUM/ANO, pg.N]
# Tipo: REN, REH, DSP, PRT, DEC, ACP, ECP, etc (2-4 letras maiúsculas)
# Numero: \d+/\d+
_CITATION_RE = re.compile(
    r"\[FONTE:\s*([^\]]+?)\]",
    re.IGNORECASE,
)
_DOC_PATTERN_RE = re.compile(
    r"\b([A-Z]{2,4})\s*[-–—:]?\s*[A-Za-zÀ-ÿ\s]*?\s*(\d+/\d{4})\b",
)


def validate_citations(answer: str, sources: list[RetrievedChunk]) -> list[str]:
    """Verifica se cada [FONTE: ...] da resposta corresponde a um chunk realmente
    recuperado. Retorna lista de citações 'fantasmas' (citadas mas não nas sources).

    Tolerante a variações: 'REN 1000/2021' bate com 'REN - RESOLUÇÃO NORMATIVA 1000/2021'.
    Citações sem padrão TIPO NUM/ANO reconhecível são ignoradas (não validáveis)."""
    if not answer or not sources:
        return []

    # Constrói pool de identificadores normalizados das sources
    source_keys = set()
    for c in sources:
        for field_val in (c.registro_titulo or "", c.pdf_id or ""):
            for m in _DOC_PATTERN_RE.finditer(field_val):
                tipo, numero = m.group(1).upper(), m.group(2)
                source_keys.add(f"{tipo}|{numero}")

    invalid = []
    seen_invalid = set()
    for cite_match in _CITATION_RE.finditer(answer):
        cite_text = cite_match.group(1).strip()
        m = _DOC_PATTERN_RE.search(cite_text)
        if not m:
            continue  # citação sem padrão reconhecível — não posso validar
        tipo, numero = m.group(1).upper(), m.group(2)
        key = f"{tipo}|{numero}"
        if key not in source_keys and key not in seen_invalid:
            invalid.append(cite_text)
            seen_invalid.add(key)
    return invalid


# -------- AGENT --------

class RAGAgent:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.cfg = oci.config.from_file()
        self.cfg["region"] = "sa-saopaulo-1"
        self.tenancy = self.cfg["tenancy"]

        self.mgmt = oci.generative_ai.GenerativeAiClient(self.cfg)
        self.inf = oci.generative_ai_inference.GenerativeAiInferenceClient(self.cfg)

        self.embed_model = find_model(self.mgmt, self.tenancy, _is_embed_multi_v3)
        self.llm_model = find_model(self.mgmt, self.tenancy, _is_command_r_plus) \
                         or find_model(self.mgmt, self.tenancy, _is_any_chat)
        if not self.embed_model:
            sys.exit("Modelo de embedding não encontrado")
        if not self.llm_model:
            sys.exit("Nenhum modelo de chat encontrado")

        # Rerank é opcional
        self.rerank_model = find_model(
            self.mgmt, self.tenancy,
            lambda m: "rerank" in (m.display_name or "").lower() and m.lifecycle_state == "ACTIVE"
        )

        self.conn = connect_db()

        # Logger estruturado JSONL (1 linha por requisição) — antes de qualquer log
        self._jlog = JsonLogger(ROOT / "logs" / "agent.jsonl")
        self._jlog.log("agent_init",
                       embed_model=self.embed_model.display_name,
                       llm_model=self.llm_model.display_name,
                       rerank_model=self.rerank_model.display_name if self.rerank_model else None)

        # Cache LRU de respostas (query+history → AgentResponse)
        # Acelera re-execução da mesma query (warmup, demos, debug repetitivo)
        from collections import OrderedDict
        self._response_cache: OrderedDict = OrderedDict()
        self._response_cache_lock = _threading.Lock()
        self._response_cache_max = 64

        # Pré-computa embeddings dos exemplos canônicos pra classificação semântica
        # em background (não bloqueia init / 1ª query). Se falhar, regex-only.
        self._intent_centroids: dict = {}

        def _load_centroids():
            try:
                import numpy as np
                centroids = {}
                for category, examples in _INTENT_EXAMPLES.items():
                    vectors = []
                    for ex in examples:
                        try:
                            v = embed_query(self.inf, self.embed_model.id, self.tenancy, ex)
                            vectors.append(np.array(v, dtype="float32"))
                        except Exception:
                            continue
                    if vectors:
                        centroid = np.mean(np.stack(vectors), axis=0)
                        norm = np.linalg.norm(centroid)
                        if norm > 0:
                            centroid = centroid / norm
                        centroids[category] = centroid
                self._intent_centroids = centroids
                self._jlog.log("intent_centroids_loaded",
                               categories=list(centroids.keys()),
                               total_examples=sum(len(v) for v in _INTENT_EXAMPLES.values()))
            except Exception as e:
                self._jlog.log("intent_centroids_failed",
                               error_type=type(e).__name__, error=str(e)[:300])

        self._centroids_thread = _threading.Thread(target=_load_centroids, daemon=True)
        self._centroids_thread.start()

        # Warm-up do bge em BACKGROUND (não bloqueia init do agent / startup da UI)
        import threading
        self._bge_warmup_thread = threading.Thread(target=get_bge_reranker, daemon=True)
        self._bge_warmup_thread.start()

        if verbose:
            print(f"  [init] embed={self.embed_model.display_name}")
            print(f"  [init] llm={self.llm_model.display_name}")
            print(f"  [init] rerank={self.rerank_model.display_name if self.rerank_model else 'NONE'}")

    def _classify_intent_semantic(self, query: str, has_history: bool) -> tuple[str, list | None]:
        """Classificação híbrida: regex curtíssima primeiro, embedding similarity depois.

        Retorna (intent, embedding_or_None). Embedding é retornado quando precisou
        ser computado — pra ser reusado pelo retrieval (evita embeddar 2x).

        Princípio:
        - Casos triviais ('oi', 'obrigado', 'tchau', 'ok') → regex 0ms.
        - Pra resto, embedda a query e compara cosine similarity contra centróides
          pré-computados. Captura SIGNIFICADO ('bom demais' ≈ 'muito bom' ≈ 'irado'
          ≈ centróide chitchat) sem precisar listar cada variação.
        - Pergunta real (a maioria das queries longas/com termo de domínio) bate
          forte no centróide real_question — e o embedding feito aqui é REUSADO no
          retrieval, custo zero extra.
        """
        q = query.strip()

        # 1) Regex barata pra casos triviais
        if len(q.split()) <= 6:
            for p in _CHITCHAT_PATTERNS:
                if p.match(q):
                    return "chitchat", None

        # Meta-conversa (referência ao histórico) — só faz sentido com history
        if has_history:
            for p in _META_PATTERNS:
                if p.search(q):
                    if has_domain_terms(q) and len(q.split()) > 5:
                        # query menciona domínio + é longa → real_question
                        break
                    return "meta", None

        # 2) Sem centróides (init falhou) → fallback regex-only = real_question
        if not self._intent_centroids:
            return "real_question", None

        # 3) Embedding similarity pra capturar coloquiais novos
        try:
            import numpy as np
            qvec = embed_query(self.inf, self.embed_model.id, self.tenancy, query)
            qarr = np.array(qvec, dtype="float32")
            qnorm = np.linalg.norm(qarr)
            if qnorm == 0:
                return "real_question", qvec
            qarr = qarr / qnorm

            # cosine similarity contra cada centróide
            scores = {cat: float(np.dot(qarr, centroid))
                      for cat, centroid in self._intent_centroids.items()}
            best_cat = max(scores, key=scores.get)
            best_score = scores[best_cat]
            real_score = scores.get("real_question", 0.0)

            # Heurística: precisa ser significativamente melhor que real_question
            # pra reclassificar (evita falso positivo em pergunta legítima).
            # Threshold absoluto: 0.55 mínimo (Cohere v3 normalizada).
            # Margin: 0.05 acima de real_question pra reclassificar.
            if best_cat != "real_question":
                if best_score > 0.55 and best_score > real_score + 0.05:
                    self._jlog.log("intent_semantic", query=query[:100],
                                   intent=best_cat, scores={k: round(v, 3) for k, v in scores.items()})
                    return best_cat, qvec

            self._jlog.log("intent_semantic", query=query[:100],
                           intent="real_question",
                           scores={k: round(v, 3) for k, v in scores.items()})
            return "real_question", qvec
        except Exception as e:
            self._jlog.log("intent_semantic_failed",
                           error_type=type(e).__name__, error=str(e)[:200])
            return "real_question", None

    def _cache_key(self, query: str, history: list[dict] | None) -> str:
        """Chave do cache: query normalizada + hash dos últimos 4 turnos."""
        import hashlib
        q_norm = query.lower().strip()
        if history:
            recent = history[-8:]  # últimos 4 turnos = 8 mensagens
            h_str = "|".join(f"{m.get('role','')}:{(m.get('content','') or '')[:200]}" for m in recent)
            h_hash = hashlib.md5(h_str.encode("utf-8")).hexdigest()[:8]
        else:
            h_hash = "none"
        return f"{q_norm}|{h_hash}"

    def _cache_get(self, key: str):
        with self._response_cache_lock:
            if key in self._response_cache:
                self._response_cache.move_to_end(key)  # LRU: marca como recente
                return self._response_cache[key]
            return None

    def _cache_set(self, key: str, resp) -> None:
        # Não cacheia respostas com erro/refusal pra não fixar comportamento ruim
        if resp.refused and resp.refusal_reason not in ("chitchat", "meta_conversa",
                                                        "fora_escopo_temporal", "fora_escopo_tematico"):
            return
        with self._response_cache_lock:
            self._response_cache[key] = resp
            self._response_cache.move_to_end(key)
            while len(self._response_cache) > self._response_cache_max:
                self._response_cache.popitem(last=False)  # remove mais antigo

    def answer_stream(self, query: str, history: list[dict] | None = None):
        """Wrapper: instrumenta com logging JSON. Delega pra _do_answer_stream."""
        rid = new_request_id()
        self._jlog.log("stream_received", request_id=rid, query=query[:200],
                       has_history=bool(history))
        # Detecta se a resposta veio do cache (pra não re-cachear nem mentir no log)
        cache_key = self._cache_key(query, history)
        was_cached_before = cache_key in self._response_cache
        try:
            for evt, payload in self._do_answer_stream(query, history=history):
                yield (evt, payload)
                if evt == "done":
                    resp = payload
                    top1_dist = resp.sources[0].vector_dist if resp.sources else None
                    top1_rerank = resp.sources[0].rerank_score if resp.sources else None
                    self._jlog.log("stream_complete", request_id=rid,
                                   refused=resp.refused,
                                   refusal_reason=resp.refusal_reason,
                                   elapsed_ms=resp.elapsed_ms,
                                   confidence=round(resp.confidence, 3),
                                   sources=len(resp.sources),
                                   top1_dist=round(top1_dist, 3) if top1_dist is not None else None,
                                   top1_rerank=round(top1_rerank, 3) if top1_rerank is not None else None,
                                   has_citation="[FONTE:" in (resp.answer or ""),
                                   from_cache=was_cached_before)
                    # Salva no cache se for resposta nova (não veio do cache)
                    if not was_cached_before:
                        self._cache_set(cache_key, resp)
        except Exception as e:
            self._jlog.log("stream_error", request_id=rid,
                           error_type=type(e).__name__, error=str(e)[:300])
            raise

    def _do_answer_stream(self, query: str, history: list[dict] | None = None):
        """Yields (event_type, payload). event_type ∈ {'phase', 'meta', 'token', 'done'}.
        - phase: payload = string ('embedding'|'retrieval'|'rerank'|'expanding'|'generating')
                 — usado pela UI pra mostrar progresso granular
        - meta:  payload = AgentResponse parcial (sem answer ainda)
        - token: payload = string (chunk de texto)
        - done:  payload = AgentResponse final (com answer completo)
        """
        t0 = time.time()
        resp = AgentResponse(query=query, answer="")

        # Cache LRU — replay rápido de queries repetidas (warmup, demos)
        cache_key = self._cache_key(query, history)
        cached = self._cache_get(cache_key)
        if cached is not None:
            import copy
            resp_copy = copy.deepcopy(cached)
            resp_copy.elapsed_ms = int((time.time() - t0) * 1000)
            yield ("meta", resp_copy)
            # Replay tokens em chunks de ~80 chars pra UI mostrar streaming
            text = resp_copy.answer or ""
            chunk_size = 80
            for i in range(0, len(text), chunk_size):
                yield ("token", text[i:i+chunk_size])
            yield ("done", resp_copy)
            return

        # Camada 0 — classificador de intenção (chitchat / meta / real_question).
        # Híbrido: regex barata + embedding semântico. Reusa embedding no retrieval.
        intent, classified_embedding = self._classify_intent_semantic(query, has_history=bool(history))
        yield ("intent", intent)  # informa UI antes do pipeline pesado
        if intent == "chitchat":
            resp.answer = chitchat_response(query)
            resp.refused = False
            resp.confidence = 1.0
            resp.refusal_reason = "chitchat"  # marcador, não é refusal real
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            yield ("meta", resp)
            for tok in resp.answer.split(" "):
                yield ("token", tok + " ")
            yield ("done", resp)
            return

        if intent == "meta":
            # Meta-conversa: LLM com history, SEM retrieval/rerank/fontes
            yield ("meta", resp)
            meta_prompt = build_meta_prompt(query, history)
            full_text_parts = []
            try:
                for chunk in llm_generate_cohere_stream(
                    self.inf, self.llm_model.id, self.tenancy,
                    META_SYSTEM_PROMPT, meta_prompt,
                    temperature=0.2, max_tokens=600,
                ):
                    full_text_parts.append(chunk)
                    yield ("token", chunk)
            except Exception as e:
                err = f"\n\n[ERRO LLM: {type(e).__name__}: {e}]"
                full_text_parts.append(err)
                yield ("token", err)
            resp.answer = "".join(full_text_parts).strip()
            resp.refused = False
            resp.confidence = 0.9
            resp.refusal_reason = "meta_conversa"  # marcador
            resp.sources = []
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            yield ("done", resp)
            return

        # Pré-checagens (mesmas)
        if history:
            effective_query, was_rewritten = self._maybe_rewrite_query(query, history)
            if was_rewritten:
                resp.rewritten_query = effective_query
            query_for_search = effective_query
        else:
            query_for_search = query

        ok, reason = check_temporal(query_for_search)
        if not ok:
            resp.refused = True
            resp.refusal_reason = "fora_escopo_temporal"
            resp.answer = reason
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            yield ("done", resp)
            return

        ok, reason = check_scope(query_for_search)
        if not ok:
            resp.refused = True
            resp.refusal_reason = "fora_escopo_tematico"
            resp.answer = reason
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            yield ("done", resp)
            return

        filters = extract_filters_from_query(query_for_search)
        query_expanded = expand_query(query_for_search)

        # HyDE pra queries vagas
        embed_text = query_expanded
        if is_query_vague(query_for_search):
            hyde_resp = hyde_generate(self.inf, self.llm_model.id, self.tenancy, query_expanded)
            if hyde_resp and len(hyde_resp) > 30:
                embed_text = f"{query_expanded}\n\n{hyde_resp}"

        yield ("phase", "embedding")
        # Reusa embedding já computado pelo classificador semântico (se a query
        # final for igual à classificada). Caso expand_query/HyDE tenha alterado,
        # o LRU cache de embed_query economiza qualquer trabalho duplicado.
        if classified_embedding is not None and embed_text.strip() == query.strip():
            qvec = classified_embedding
        else:
            qvec = embed_query(self.inf, self.embed_model.id, self.tenancy, embed_text)
        cur = self.conn.cursor()
        try:
            yield ("phase", "retrieval")
            vec_res = vector_search(cur, qvec, k=VECTOR_K, filters=filters)
            bm25_res = bm25_search(cur, query_expanded, k=BM25_K, filters=filters)
            fused = rrf_fuse(vec_res, bm25_res)

            if not fused:
                resp.refused = True
                resp.refusal_reason = "zero_resultados"
                resp.answer = "Não encontrei nenhum trecho relevante na minha base."
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                yield ("done", resp)
                return

            # Early-exit por gap: só dispara se BGE não está disponível.
            # Com BGE, deixamos o reranker decidir (mais preciso) — o pós-rerank early-exit (linha ~913)
            # cobre off-topic via score absoluto. Esta heurística estava recusando queries legítimas
            # onde o vector é mediano mas o rerank salvaria (ex: "vale a pena painel solar?").
            if _bge_reranker_cache is None and vec_res and len(vec_res) >= 5:
                top1 = vec_res[0].vector_dist or 1.0
                topN = vec_res[min(9, len(vec_res)-1)].vector_dist or 1.0
                gap = topN - top1
                is_offtopic = (top1 > DIST_TOP1_OFFTOPIC and gap < GAP_THRESHOLD)
                if is_offtopic:
                    resp.confidence = 0.0
                    resp.refused = True
                    resp.refusal_reason = "off_topic_provavel"
                    resp.answer = (
                        "Sua pergunta parece estar fora do meu domínio. "
                        "Eu só conheço a legislação da ANEEL (Agência Nacional de Energia Elétrica) "
                        "dos anos 2016, 2021 e 2022. Tente reformular para um tema do setor elétrico — "
                        "por exemplo: tarifas, geração distribuída, resoluções normativas, concessões."
                    )
                    resp.sources = vec_res[:3]
                    resp.elapsed_ms = int((time.time() - t0) * 1000)
                    yield ("done", resp)
                    return

            yield ("phase", "rerank")
            query_for_rerank = expand_query_for_rerank(query_for_search)
            bge = get_bge_reranker()
            if bge is not None:
                top = rerank_bge(query_for_rerank, fused[:RERANK_INPUT_K], top_n=RERANK_TOP_N)
            elif self.rerank_model:
                top = rerank_cohere(self.inf, self.rerank_model.id, self.tenancy,
                                   query_for_rerank, fused[:RERANK_INPUT_K], top_n=RERANK_TOP_N)
            else:
                top = fused[:RERANK_TOP_N]

            # Early-exit pós-rerank
            top1_rerank = top[0].rerank_score if top[0].rerank_score is not None else None
            if top1_rerank is not None and top1_rerank < RERANK_OFFTOPIC_THRESHOLD:
                resp.confidence = 0.0
                resp.refused = True
                resp.refusal_reason = "off_topic_rerank"
                resp.answer = (
                    "Sua pergunta provavelmente está fora do meu domínio. "
                    "Eu só conheço a legislação da ANEEL de 2016, 2021 e 2022. "
                    "Tente reformular usando termos do setor elétrico."
                )
                resp.sources = top[:3]
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                yield ("done", resp)
                return

            # BM25-only chunk (sem vector_dist) com rerank alto = relevante mesmo. Alinhado com :1172-1178.
            if top[0].vector_dist is None:
                if top[0].rerank_score is not None and top[0].rerank_score > 0.2:
                    top1_dist = 0.45
                else:
                    top1_dist = 1.0
            else:
                top1_dist = top[0].vector_dist
            if top1_dist > DIST_THRESHOLD_NO_CONFIDENCE:
                resp.confidence = max(0.0, 1.0 - top1_dist)
                resp.refused = True
                resp.refusal_reason = "baixa_confianca"
                resp.answer = ("Não encontrei trechos suficientemente relevantes na minha base. "
                              "Tente reformular a pergunta ou verifique se o assunto está dentro do "
                              "escopo (legislação ANEEL 2016, 2021, 2022).")
                resp.sources = top[:3]
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                yield ("done", resp)
                return

            resp.confidence = 1.0 - top1_dist
            resp.sources = top
            yield ("phase", "expanding")
            contexts_for_llm = self._expand_to_parents(cur, top)
        finally:
            try:
                cur.close()
            except Exception:
                pass
        user_prompt = build_user_prompt(query_for_search, contexts_for_llm, history=history)

        # Sinaliza meta (UI pode mostrar "Buscando concluído, gerando resposta…")
        yield ("meta", resp)
        yield ("phase", "generating")

        # Stream da geração
        full_text_parts = []
        try:
            for chunk in llm_generate_cohere_stream(
                self.inf, self.llm_model.id, self.tenancy,
                SYSTEM_PROMPT, user_prompt,
                temperature=0.2, max_tokens=1000,
            ):
                full_text_parts.append(chunk)
                yield ("token", chunk)
        except Exception as e:
            err = f"\n\n[ERRO LLM: {type(e).__name__}: {e}]"
            full_text_parts.append(err)
            yield ("token", err)

        resp.answer = "".join(full_text_parts).strip()
        # Só adiciona aviso de "sem citação" se o LLM realmente respondeu —
        # respostas que começam com "Não encontrei..." não precisam validar fontes.
        if not has_citation(resp.answer) and not resp.answer.lower().startswith("não encontrei"):
            resp.answer += "\n\n[AVISO: resposta sem citação explícita — verifique fontes.]"
        # Validador de citações: detecta citações fantasmas (citou doc não recuperado)
        resp.invalid_citations = validate_citations(resp.answer, resp.sources)
        resp.elapsed_ms = int((time.time() - t0) * 1000)
        yield ("done", resp)

    def _maybe_rewrite_query(self, query: str, history: list[dict]) -> tuple[str, bool]:
        """Reescreve query como autocontida se detectar follow-up. Retorna (query_final, foi_reescrita)."""
        if not history:
            return query, False
        if not FOLLOW_UP_SIGNALS.search(query):
            return query, False

        recent = history[-(MAX_HISTORY_TURNS * 2):]
        hist_str = "\n".join(
            f"{m['role'].upper()}: {m['content'][:500]}"
            for m in recent
        )
        prompt = REWRITE_PROMPT.format(history=hist_str, question=query)
        try:
            rewritten = llm_generate_cohere(
                self.inf, self.llm_model.id, self.tenancy,
                system="Você reformula perguntas de follow-up em perguntas autocontidas para busca.",
                user=prompt, temperature=0.0, max_tokens=150,
            ).strip().strip('"').strip("'")
            if rewritten and rewritten.lower() != query.lower() and len(rewritten) < 500:
                return rewritten, True
        except Exception:
            pass
        return query, False

    def answer_with_history(self, query: str, history: list[dict] | None = None) -> AgentResponse:
        """answer() com contexto de conversa: reescrita de follow-up + histórico no prompt."""
        if not history:
            return self.answer(query)

        effective_query, was_rewritten = self._maybe_rewrite_query(query, history)
        resp = self.answer(effective_query, history=history, original_query=query)
        if was_rewritten:
            resp.rewritten_query = effective_query
            resp.query = query
        return resp

    def _expand_to_parents(self, cur, children: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Small-to-Big: substitui cada child pelo seu parent pra mais contexto ao LLM.
        Dedupe por parent_id (vários children do mesmo parent → 1 parent só).
        Otimização: 1 query batch com IN, em vez de N queries."""
        # 1ª passagem: identifica parent_ids únicos a buscar (preservando ordem
        # do primeiro child que referencia cada parent)
        unique_parent_ids = []
        seen_parents = set()
        for c in children:
            if c.parent_chunk_id and c.parent_chunk_id not in seen_parents:
                seen_parents.add(c.parent_chunk_id)
                unique_parent_ids.append(c.parent_chunk_id)

        # 1 query batch (em vez de N) — ganho ~200-400ms quando children compartilham parents
        parent_rows = {}
        if unique_parent_ids:
            placeholders = ",".join(f":p{i}" for i in range(len(unique_parent_ids)))
            params = {f"p{i}": pid for i, pid in enumerate(unique_parent_ids)}
            cur.execute(f"""
                SELECT p.chunk_id, p.pdf_id, p.breadcrumb, p.chunk_type, p.page_start,
                       p.text_raw, p.ano, p.tipo_canonico, m.registro_titulo, m.url
                FROM chunks p LEFT JOIN manifest m ON m.pdf_id = p.pdf_id
                WHERE p.chunk_id IN ({placeholders})
            """, params)
            for row in cur.fetchall():
                pid, pdfid, bc, ctype, pg, raw_clob, ano, tipo, titulo, url = row
                raw = raw_clob.read() if raw_clob and hasattr(raw_clob, "read") else (raw_clob or "")
                parent_rows[pid] = (pdfid, bc, ctype, pg, raw, ano, tipo, titulo, url)

        # 2ª passagem: monta saída na ordem original dos children, deduplicando parents
        added_parents = set()
        out = []
        for c in children:
            if not c.parent_chunk_id:
                out.append(c)
                continue
            if c.parent_chunk_id in added_parents:
                continue
            added_parents.add(c.parent_chunk_id)
            row = parent_rows.get(c.parent_chunk_id)
            if row is None:
                # Parent não encontrado no banco — fallback pro child
                out.append(c)
                continue
            pdfid, bc, ctype, pg, raw, ano, tipo, titulo, url = row
            out.append(RetrievedChunk(
                chunk_id=c.parent_chunk_id, parent_chunk_id=None, pdf_id=pdfid,
                breadcrumb=bc or "", chunk_type=ctype, page_start=pg,
                text_raw=raw, text_embed="",
                ano=ano, tipo_canonico=tipo, registro_titulo=titulo, pdf_url=url,
                vector_dist=c.vector_dist,
            ))
        return out

    def answer(self, query: str, history: list[dict] | None = None,
               original_query: str | None = None) -> AgentResponse:
        rid = new_request_id()
        self._jlog.log("query_received", request_id=rid, query=query[:200],
                       has_history=bool(history))
        # Cache LRU — replay rápido de queries repetidas
        cache_key = self._cache_key(query, history)
        cached = self._cache_get(cache_key)
        if cached is not None:
            import copy
            resp = copy.deepcopy(cached)
            resp.elapsed_ms = 0  # cache hit é instantâneo
            self._jlog.log("query_complete", request_id=rid, from_cache=True,
                           refused=resp.refused, refusal_reason=resp.refusal_reason,
                           sources=len(resp.sources), elapsed_ms=0)
            return resp
        try:
            resp = self._do_answer(query, history=history, original_query=original_query)
        except Exception as e:
            self._jlog.log("query_error", request_id=rid,
                           error_type=type(e).__name__, error=str(e)[:300])
            raise
        top1_dist = resp.sources[0].vector_dist if resp.sources else None
        top1_rerank = resp.sources[0].rerank_score if resp.sources else None
        self._jlog.log("query_complete", request_id=rid,
                       refused=resp.refused,
                       refusal_reason=resp.refusal_reason,
                       elapsed_ms=resp.elapsed_ms,
                       confidence=round(resp.confidence, 3),
                       sources=len(resp.sources),
                       top1_dist=round(top1_dist, 3) if top1_dist is not None else None,
                       top1_rerank=round(top1_rerank, 3) if top1_rerank is not None else None,
                       has_citation="[FONTE:" in (resp.answer or ""),
                       from_cache=False)
        # Salva no cache se for resposta nova
        self._cache_set(cache_key, resp)
        return resp

    def _do_answer(self, query: str, history: list[dict] | None = None,
                   original_query: str | None = None) -> AgentResponse:
        t0 = time.time()
        resp = AgentResponse(query=original_query or query, answer="")

        # Camada 0 — classificador de intenção (híbrido: regex + embedding semântico).
        intent, _ = self._classify_intent_semantic(query, has_history=bool(history))
        if intent == "chitchat":
            resp.answer = chitchat_response(query)
            resp.refused = False
            resp.confidence = 1.0
            resp.refusal_reason = "chitchat"
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            return resp
        if intent == "meta" and history:
            meta_prompt = build_meta_prompt(query, history)
            try:
                answer = llm_generate_cohere(
                    self.inf, self.llm_model.id, self.tenancy,
                    META_SYSTEM_PROMPT, meta_prompt,
                    temperature=0.2, max_tokens=600,
                )
                resp.answer = answer.strip()
                resp.refused = False
                resp.confidence = 0.9
                resp.refusal_reason = "meta_conversa"
            except Exception as e:
                resp.refused = True
                resp.refusal_reason = "erro_llm"
                resp.answer = f"Erro ao gerar resposta meta: {type(e).__name__}: {e}"
            resp.sources = []
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            return resp

        # Camada 1 — temporal
        ok, reason = check_temporal(query)
        if not ok:
            resp.refused = True
            resp.refusal_reason = "fora_escopo_temporal"
            resp.answer = reason
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            return resp

        # Camada 2 — escopo
        ok, reason = check_scope(query)
        if not ok:
            resp.refused = True
            resp.refusal_reason = "fora_escopo_tematico"
            resp.answer = reason
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            return resp

        # Camada 3 — retrieval híbrido com query expansion + HyDE
        filters = extract_filters_from_query(query)
        query_expanded = expand_query(query)

        # HyDE desativado por padrão (ganho marginal vs custo de latência)
        # Pra ativar: setar HYDE_ENABLED=1 no env
        embed_text = query_expanded
        hyde_used = False
        if os.environ.get("HYDE_ENABLED") == "1" and is_query_vague(query):
            hyde_resp = hyde_generate(self.inf, self.llm_model.id, self.tenancy, query_expanded)
            if hyde_resp and len(hyde_resp) > 30:
                embed_text = f"{query_expanded}\n\n{hyde_resp}"
                hyde_used = True

        qvec = embed_query(self.inf, self.embed_model.id, self.tenancy, embed_text)

        cur = self.conn.cursor()
        try:
            vec_res = vector_search(cur, qvec, k=VECTOR_K, filters=filters)
            bm25_res = bm25_search(cur, query_expanded, k=BM25_K, filters=filters)
            fused = rrf_fuse(vec_res, bm25_res)

            if self.verbose:
                print(f"  [retrieve] vec={len(vec_res)} bm25={len(bm25_res)} fused={len(fused)} "
                      f"hyde={hyde_used}")

            if not fused:
                resp.refused = True
                resp.refusal_reason = "zero_resultados"
                resp.answer = "Não encontrei nenhum trecho relevante na minha base para essa pergunta."
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                return resp

            # Early-exit por gap: só dispara se BGE não está disponível (ver comentário no streaming path).
            if _bge_reranker_cache is None and vec_res and len(vec_res) >= 5:
                top1 = vec_res[0].vector_dist or 1.0
                topN = vec_res[min(9, len(vec_res)-1)].vector_dist or 1.0
                gap = topN - top1
                if top1 > DIST_TOP1_OFFTOPIC and gap < GAP_THRESHOLD:
                    resp.confidence = 0.0
                    resp.refused = True
                    resp.refusal_reason = "off_topic_provavel"
                    resp.answer = (
                        "Sua pergunta parece estar fora do meu domínio. "
                        "Eu só conheço a legislação da ANEEL (Agência Nacional de Energia Elétrica) "
                        "dos anos 2016, 2021 e 2022. Tente reformular para um tema do setor elétrico — "
                        "por exemplo: tarifas, geração distribuída, resoluções normativas, concessões."
                    )
                    resp.sources = vec_res[:3]
                    resp.elapsed_ms = int((time.time() - t0) * 1000)
                    return resp

            # Camada 4 — rerank (preferência: bge local; fallback Cohere; senão fused)
            query_for_rerank = expand_query_for_rerank(query)
            bge = get_bge_reranker()
            if bge is not None:
                top = rerank_bge(query_for_rerank, fused[:RERANK_INPUT_K], top_n=RERANK_TOP_N)
            elif self.rerank_model:
                top = rerank_cohere(self.inf, self.rerank_model.id, self.tenancy,
                                   query_for_rerank, fused[:RERANK_INPUT_K], top_n=RERANK_TOP_N)
            else:
                top = fused[:RERANK_TOP_N]

            # Early-exit pós-rerank: se reranker (semântica fina) deu score muito baixo, é off-topic
            top1_rerank = top[0].rerank_score if top[0].rerank_score is not None else None
            if top1_rerank is not None and top1_rerank < RERANK_OFFTOPIC_THRESHOLD:
                resp.confidence = 0.0
                resp.refused = True
                resp.refusal_reason = "off_topic_rerank"
                resp.answer = (
                    "Sua pergunta provavelmente está fora do meu domínio. "
                    "Eu só conheço a legislação da ANEEL de 2016, 2021 e 2022. "
                    "Tente reformular usando termos do setor elétrico."
                )
                resp.sources = top[:3]
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                return resp

            # Check confiança via vector distance
            # Se top-1 não tem vector_dist (veio só de BM25), MAS rerank score é alto, é relevante mesmo
            if top[0].vector_dist is None:
                if top[0].rerank_score is not None and top[0].rerank_score > 0.2:
                    top1_dist = 0.45  # estimativa: BM25 + rerank alto = relevante
                else:
                    top1_dist = 1.0
            else:
                top1_dist = top[0].vector_dist
            if top1_dist > DIST_THRESHOLD_NO_CONFIDENCE:
                resp.confidence = max(0.0, 1.0 - top1_dist)
                resp.refused = True
                resp.refusal_reason = "baixa_confianca"
                resp.answer = ("Não encontrei trechos suficientemente relevantes na minha base. "
                              "Tente reformular a pergunta ou verifique se o assunto está dentro do "
                              "escopo (legislação ANEEL 2016, 2021, 2022).")
                resp.sources = top[:3]
                resp.elapsed_ms = int((time.time() - t0) * 1000)
                return resp

            resp.confidence = 1.0 - top1_dist

            # Small-to-Big: buscar os parents dos top children pra dar MAIS contexto ao LLM
            contexts_for_llm = self._expand_to_parents(cur, top)
        finally:
            try:
                cur.close()
            except Exception:
                pass

        # Camada 5 — generate + validate
        user_prompt = build_user_prompt(query, contexts_for_llm, history=history)
        try:
            answer = llm_generate_cohere(
                self.inf, self.llm_model.id, self.tenancy,
                SYSTEM_PROMPT, user_prompt,
                temperature=0.2, max_tokens=1000,
            )
        except Exception as e:
            resp.refused = True
            resp.refusal_reason = "erro_llm"
            resp.answer = f"Erro ao gerar resposta: {type(e).__name__}: {e}"
            resp.sources = top
            resp.elapsed_ms = int((time.time() - t0) * 1000)
            return resp

        resp.answer = answer.strip()
        resp.sources = top

        if not has_citation(answer) and not resp.answer.lower().startswith("não encontrei"):
            resp.answer += "\n\n[AVISO: Resposta sem citação explícita — valide as fontes abaixo.]"

        resp.elapsed_ms = int((time.time() - t0) * 1000)
        return resp


# -------- CLI --------

def format_response(resp: AgentResponse) -> str:
    lines = []
    if resp.refused:
        lines.append(f"[RECUSADO — {resp.refusal_reason}]\n")
    lines.append(resp.answer)
    if resp.sources and not resp.refused:
        lines.append("\n\n=== Fontes consultadas ===")
        for i, s in enumerate(resp.sources, start=1):
            score = f"rerank={s.rerank_score:.3f}" if s.rerank_score is not None else f"dist={s.vector_dist:.3f}"
            lines.append(f"  [{i}] {s.breadcrumb}  pg.{s.page_start}  {score}  ({s.chunk_type})")
    lines.append(f"\n[confidence={resp.confidence:.2f} | elapsed={resp.elapsed_ms}ms]")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    agent = RAGAgent(verbose=args.verbose)

    if args.interactive:
        print("Digite 'sair' para encerrar.\n")
        while True:
            try:
                q = input(">> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("sair", "exit", "quit"):
                break
            resp = agent.answer(q)
            print(format_response(resp))
            print()
    elif args.query:
        resp = agent.answer(args.query)
        print(format_response(resp))
    else:
        # Smoke test: 5 queries
        tests = [
            "Quem preside a Comissão Especial de Licitação?",
            "Qual o prazo de vigência da Portaria 3.700?",
            "O que aconteceu em 2027?",
            "Qual a taxa Selic?",
            "Quais tipos de leilões a CEL coordena?",
        ]
        for q in tests:
            print(f"\n{'='*72}\n  Q: {q}\n{'='*72}")
            resp = agent.answer(q)
            print(format_response(resp))


if __name__ == "__main__":
    main()
