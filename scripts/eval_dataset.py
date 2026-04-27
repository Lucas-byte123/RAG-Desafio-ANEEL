"""Dataset de queries-gabarito pra avaliar o RAG.

Cada item tem:
  - query: pergunta natural
  - category: tipo de query
  - should_refuse: bool — se o agente DEVE recusar (off-topic, fora escopo)
  - expected_doc_pattern: regex que deve aparecer no breadcrumb dos top-K (None se off-topic)
  - expected_keywords: termos que DEVEM aparecer na resposta correta (None se off-topic)
  - reference_chunk_id: chunk concreto que sabidamente responde (gabarito)

Curado a partir de chunks reais do corpus em 2026-04-24.
"""

EVAL_DATASET = [
    # ─────────── CATEGORIA 1: FACTUAL NUMÉRICA (artigos específicos) ───────────
    {
        "id": "fn01",
        "category": "factual_numerica",
        "query": "Em atendimento por sistemas MIGDI ou SIGFI, como a distribuidora pode cobrar?",
        "should_refuse": False,
        "expected_doc_pattern": "REN 1000/2021",
        "expected_keywords": ["carnê", "fatura", "ano", "antecipada"],
        "reference_chunk_id": "2021-12-20_47_textointegral_e5fb7e53_c0859",
    },
    {
        "id": "fn02",
        "category": "factual_numerica",
        "query": "Quais informações a AIR — Análise de Impacto Regulatório — deve conter?",
        "should_refuse": False,
        "expected_doc_pattern": "REN 756/2016",
        "expected_keywords": ["alterações", "revogações", "regulamento"],
        "reference_chunk_id": "2016-12-29_15_voto_7c93e1d2_c0025",
    },
    {
        "id": "fn03",
        "category": "factual_numerica",
        "query": "Cabe pedido de impugnação à Diretoria da ANEEL contra decisões da CCEE?",
        "should_refuse": False,
        "expected_doc_pattern": "REN 957/2021",
        "expected_keywords": ["impugnação", "Diretoria", "CCEE"],
        "reference_chunk_id": "2021-12-17_42_textointegral_7fc4e757_c0120",
    },
    {
        "id": "fn04",
        "category": "factual_numerica",
        "query": "Como são homologadas as metas do Programa Mais Luz para a Amazônia?",
        "should_refuse": False,
        "expected_doc_pattern": "REN 940/2021",
        "expected_keywords": ["ANEEL", "demanda", "distribuidoras"],
        "reference_chunk_id": "2021-07-16_38_textointegral_7f1a0aab_c0005",
    },
    {
        "id": "fn05",
        "category": "factual_numerica",
        "query": "Em atendimentos temporários com prazo menor que 90 dias, a instalação da medição é obrigatória?",
        "should_refuse": False,
        "expected_doc_pattern": "REN 1000/2021",
        "expected_keywords": ["opcional", "90 dias", "estimad"],
        "reference_chunk_id": "2021-12-20_47_textointegral_e5fb7e53_c0825",
    },

    # ─────────── CATEGORIA 2: DEFINIÇÕES E CONCEITOS ───────────
    {
        "id": "df01",
        "category": "definicao",
        "query": "O que é a área da prestação do serviço público de distribuição de energia elétrica?",
        "should_refuse": False,
        "expected_doc_pattern": "PRT 442/2016",
        "expected_keywords": ["área", "distribuição", "serviço"],
        "reference_chunk_id": "2016-08-24_31_textointegral_4b772e1f_c0010",
    },
    {
        "id": "df02",
        "category": "definicao",
        "query": "Qual a definição de microgeração distribuída?",
        "should_refuse": False,
        "expected_doc_pattern": "REN",
        "expected_keywords": ["central", "geradora", "energia elétrica"],
        "reference_chunk_id": None,
    },
    {
        "id": "df03",
        "category": "definicao",
        "query": "O que são as bandeiras tarifárias?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["bandeira", "tarifa", "consumidor"],
        "reference_chunk_id": None,
    },
    {
        "id": "df04",
        "category": "definicao",
        "query": "Como funciona o sistema de compensação de energia elétrica?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["compensação", "energia"],
        "reference_chunk_id": None,
    },
    {
        "id": "df05",
        "category": "definicao",
        "query": "Qual a diferença entre microgeração e minigeração distribuída?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["microgeração", "minigeração", "potência"],
        "reference_chunk_id": None,
    },

    # ─────────── CATEGORIA 3: PROCESSO / INSTITUCIONAL ───────────
    {
        "id": "pr01",
        "category": "processo",
        "query": "Como funciona a ANEEL?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["agência", "regulamentar", "fiscaliz"],
        "reference_chunk_id": None,
    },
    {
        "id": "pr02",
        "category": "processo",
        "query": "Quem preside a Comissão Especial de Licitação?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["Romário", "Batista", "Presidente"],
        "reference_chunk_id": None,
    },
    {
        "id": "pr03",
        "category": "processo",
        "query": "Qual o papel da CCEE no setor elétrico?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["CCEE", "comercialização", "energia"],
        "reference_chunk_id": None,
    },
    {
        "id": "pr04",
        "category": "processo",
        "query": "Quais são os tipos de leilões coordenados pela CEL?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["leilão", "energia", "transmissão"],
        "reference_chunk_id": None,
    },
    {
        "id": "pr05",
        "category": "processo",
        "query": "Quais são os deveres das distribuidoras de energia elétrica?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["distribuidora", "consumidor"],
        "reference_chunk_id": None,
    },

    # ─────────── CATEGORIA 4: OFF-TOPIC (DEVE RECUSAR) ───────────
    {
        "id": "ot01",
        "category": "off_topic",
        "query": "Qual a altura do Neymar?",
        "should_refuse": True,
        "expected_doc_pattern": None,
        "expected_keywords": None,
        "reference_chunk_id": None,
    },
    {
        "id": "ot02",
        "category": "off_topic",
        "query": "Quem ganhou a Copa do Mundo de 2022?",
        "should_refuse": True,
        "expected_doc_pattern": None,
        "expected_keywords": None,
        "reference_chunk_id": None,
    },
    {
        "id": "ot03",
        "category": "off_topic",
        "query": "Como fazer brigadeiro?",
        "should_refuse": True,
        "expected_doc_pattern": None,
        "expected_keywords": None,
        "reference_chunk_id": None,
    },
    {
        "id": "ot04",
        "category": "off_topic",
        "query": "Qual a taxa Selic atual?",
        "should_refuse": True,
        "expected_doc_pattern": None,
        "expected_keywords": None,
        "reference_chunk_id": None,
    },
    {
        "id": "ot05",
        "category": "off_topic",
        "query": "O que aconteceu na ANEEL em 2027?",
        "should_refuse": True,
        "expected_doc_pattern": None,
        "expected_keywords": None,
        "reference_chunk_id": None,
    },

    # ─────────── CATEGORIA 5: BORDERLINE / DESAFIO ───────────
    {
        "id": "bl01",
        "category": "borderline",
        "query": "Como é calculada a tarifa de energia elétrica?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["tarifa", "energia"],
        "reference_chunk_id": None,
    },
    {
        "id": "bl02",
        "category": "borderline",
        "query": "Quais resoluções normativas foram publicadas em 2022?",
        "should_refuse": False,
        "expected_doc_pattern": "2022",
        "expected_keywords": ["REN", "2022"],
        "reference_chunk_id": None,
    },
    {
        "id": "bl03",
        "category": "borderline",
        "query": "Qual o prazo de vigência da Portaria 3700?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["um ano", "vigência"],
        "reference_chunk_id": None,
    },
    {
        "id": "bl04",
        "category": "borderline",
        "query": "Como funciona o leilão de energia A-5?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["leilão", "energia"],
        "reference_chunk_id": None,
    },
    {
        "id": "bl05",
        "category": "borderline",
        "query": "Em qual situação cabe revogação de uma resolução normativa?",
        "should_refuse": False,
        "expected_doc_pattern": None,
        "expected_keywords": ["revogação", "resolução"],
        "reference_chunk_id": None,
    },
]


CATEGORIES = ["factual_numerica", "definicao", "processo", "off_topic", "borderline"]


if __name__ == "__main__":
    print(f"Total queries: {len(EVAL_DATASET)}")
    from collections import Counter
    cats = Counter(q["category"] for q in EVAL_DATASET)
    for c, n in cats.items():
        print(f"  {c}: {n}")
    refusas = sum(1 for q in EVAL_DATASET if q["should_refuse"])
    print(f"  should_refuse: {refusas}")
    com_chunk = sum(1 for q in EVAL_DATASET if q["reference_chunk_id"])
    print(f"  com chunk gabarito: {com_chunk}")
