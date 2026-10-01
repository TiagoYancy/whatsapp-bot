"""Motor de FAQ: casamento de intenção por palavras-chave + similaridade difusa.

Suporta base persistida em SQLite (com seed automático dos itens padrão).
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from config import Config
from db import Database

DEFAULT_FAQ: list[dict] = [
    {
        "id": "saudacao",
        "topic": "Saudação",
        "priority": 10,
        "keywords": ["oi", "ola", "bom dia", "boa tarde", "boa noite", "hey", "hello", "eae", "opa"],
        "question": "Saudação",
        "answer": (
            "Olá! 👋 Bem-vindo(a) ao atendimento da {empresa}. Como posso ajudar?\n\n"
            "Posso responder sobre:\n"
            "• Horário de funcionamento\n• Formas de pagamento\n• Entrega e prazos\n"
            "• Trocas e devoluções\n• Suporte técnico\n• Preços e orçamentos\n\n"
            "Digite *menu* para ver as opções ou *atendente* para falar com uma pessoa."
        ),
    },
    {
        "id": "horario",
        "topic": "Horário de funcionamento",
        "priority": 20,
        "keywords": [
            "horario", "horario de funcionamento", "funcionamento", "aberto", "fecha",
            "que horas", "quando abre", "quando fecha", "expediente", "abre",
        ],
        "question": "Horário de funcionamento",
        "answer": (
            "🕐 Nosso horário de funcionamento:\n\n"
            "• Segunda a Sexta: 9h às 18h\n"
            "• Sábado: 9h às 13h\n"
            "• Domingo: Fechado\n\n"
            "Fora desse horário, deixe sua mensagem que respondemos no próximo dia útil!"
        ),
    },
    {
        "id": "pagamento",
        "topic": "Formas de pagamento",
        "priority": 30,
        "keywords": [
            "pagamento", "pagar", "cartao", "pix", "boleto", "dinheiro", "credito",
            "debito", "parcelar", "parcela", "parcelamento", "formas de pagamento",
        ],
        "question": "Formas de pagamento",
        "answer": (
            "💳 Aceitamos as seguintes formas de pagamento:\n\n"
            "• Pix (5% de desconto)\n"
            "• Cartão de crédito (até 10x sem juros)\n"
            "• Cartão de débito\n"
            "• Boleto bancário\n\n"
            "Qualquer dúvida sobre pagamentos, é só perguntar!"
        ),
    },
    {
        "id": "entrega",
        "topic": "Entrega e prazos",
        "priority": 40,
        "keywords": [
            "entrega", "entregar", "enviar", "envio", "frete", "prazo", "chegar",
            "transportadora", "correios", "rastrear", "rastreio", "prazo de entrega",
            "quando chega",
        ],
        "question": "Entrega e prazos",
        "answer": (
            "🚚 Informações sobre entrega:\n\n"
            "• Prazo: 3 a 7 dias úteis (conforme região)\n"
            "• Frete grátis para compras acima de R$ 199\n"
            "• Enviamos para todo o Brasil\n"
            "• Código de rastreamento enviado por e-mail\n\n"
            "Para rastrear seu pedido, me informe o número do pedido."
        ),
    },
    {
        "id": "troca",
        "topic": "Trocas e devoluções",
        "priority": 50,
        "keywords": [
            "troca", "trocar", "devolucao", "devolver", "reembolso", "reembolsar",
            "desistir", "arrependimento", "estorno", "cancelar", "cancelamento",
        ],
        "question": "Trocas e devoluções",
        "answer": (
            "🔄 Política de trocas e devoluções:\n\n"
            "• Prazo: 7 dias após o recebimento\n"
            "• Produto deve estar na embalagem original\n"
            "• Reembolso em até 5 dias úteis após recebimento\n\n"
            "Para iniciar uma troca, me informe o número do pedido e o motivo."
        ),
    },
    {
        "id": "suporte",
        "topic": "Suporte técnico",
        "priority": 60,
        "keywords": [
            "suporte tecnico", "problema tecnico", "defeito", "quebrado", "danificado",
            "nao funciona", "parou de funcionar", "erro", "assistencia tecnica",
        ],
        "question": "Suporte técnico",
        "answer": (
            "🔧 Para suporte técnico, por favor descreva:\n\n"
            "1. Qual produto/serviço está com problema?\n"
            "2. Qual erro está acontecendo?\n"
            "3. Quando o problema começou?\n\n"
            "Quanto mais detalhes, mais rápido conseguimos resolver!"
        ),
    },
    {
        "id": "atendente",
        "topic": "Falar com atendente",
        "priority": 5,
        "keywords": [
            "atendente", "humano", "pessoa", "operador", "falar com alguem",
            "atendimento humano", "gerente", "supervisor", "sac",
        ],
        "question": "Falar com atendente",
        "answer": (
            "👤 Sem problemas! Vou transferir você para um de nossos atendentes.\n\n"
            "Horário de atendimento humano:\n"
            "• Segunda a Sexta: 9h às 18h\n• Sábado: 9h às 13h\n\n"
            "Fora desse horário, sua mensagem fica registrada e retornamos em breve!"
        ),
    },
    {
        "id": "preco",
        "topic": "Preços e orçamentos",
        "priority": 70,
        "keywords": [
            "preco", "quanto custa", "valor", "custo", "quanto e", "tabela de precos",
            "orcamento", "proposta", "quanto fica",
        ],
        "question": "Preços e orçamentos",
        "answer": (
            "💰 Para valores e orçamentos, me informe:\n\n"
            "1. Qual produto/serviço você tem interesse?\n"
            "2. Quantidade desejada?\n"
            "3. Região de entrega?\n\n"
            "Assim consigo te passar um orçamento personalizado!"
        ),
    },
    {
        "id": "obrigado",
        "topic": "Agradecimento",
        "priority": 15,
        "keywords": ["obrigado", "obrigada", "valeu", "agradeco", "muito obrigado", "vlw"],
        "question": "Agradecimento",
        "answer": (
            "😊 Por nada! Fico feliz em ajudar.\n\n"
            "Se precisar de mais alguma coisa, é só chamar. Tenha um ótimo dia!"
        ),
    },
    {
        "id": "despedida",
        "topic": "Despedida",
        "priority": 16,
        "keywords": ["tchau", "adeus", "ate logo", "falou", "flw", "xau", "ate mais"],
        "question": "Despedida",
        "answer": "👋 Até logo! Foi um prazer ajudar. Volte sempre!",
    },
]

FALLBACK_MESSAGE = (
    "🤔 Não tenho certeza se entendi. Posso ajudar com:\n\n"
    "• Horário de funcionamento\n• Formas de pagamento\n• Entrega e prazos\n"
    "• Trocas e devoluções\n• Suporte técnico\n• Preços e orçamentos\n\n"
    "Reformule sua pergunta ou digite *atendente* para falar com uma pessoa."
)


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação e com espaços colapsados."""
    if not text:
        return ""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class FAQEngine:
    def __init__(self, db: Database | None = None, threshold: float | None = None):
        self.db = db or Database()
        self.threshold = (threshold if threshold is not None else Config.FAQ_THRESHOLD) * 100
        self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        if self.db.faq_count() > 0:
            return
        for item in DEFAULT_FAQ:
            self.db.faq_upsert(
                item_id=item["id"],
                topic=item["topic"],
                keywords=item["keywords"],
                question=item["question"],
                answer=item["answer"].format(empresa=Config.COMPANY_NAME),
                priority=item.get("priority", 100),
            )

    # ------------------------------------------------------------------ scoring
    @staticmethod
    def _contains_word(haystack: str, needle: str) -> bool:
        if not needle:
            return False
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None

    def _score_keyword(self, message: str, keyword: str) -> float:
        kw = normalize(keyword)
        if not kw:
            return 0.0
        if self._contains_word(message, kw):
            return 100.0
        # Palavras muito curtas só valem por match exato (evita falsos positivos)
        if len(kw) <= 3:
            return 0.0
        return max(fuzz.token_set_ratio(message, kw), fuzz.partial_ratio(message, kw))

    def score_item(self, message: str, item: dict) -> float:
        best = 0.0
        for kw in item.get("keywords", []):
            best = max(best, self._score_keyword(message, kw))
        best = max(best, self._score_keyword(message, item.get("question", "")))
        return best

    # ------------------------------------------------------------------ matching
    def rank(self, message: str, limit: int = 5) -> list[dict]:
        normalized = normalize(message)
        if not normalized:
            return []
        scored = []
        for item in self.db.faq_all():
            score = self.score_item(normalized, item)
            if score > 0:
                scored.append(
                    {
                        "id": item["id"],
                        "topic": item["topic"],
                        "question": item["question"],
                        "answer": item["answer"],
                        "score": score,
                        "confidence": round(score / 100, 4),
                    }
                )
        scored.sort(key=lambda x: (-x["score"], x["id"]))
        return scored[:limit]

    def find_best_match(self, message: str) -> dict | None:
        ranked = self.rank(message, limit=1)
        if ranked and ranked[0]["score"] >= self.threshold:
            return ranked[0]
        return None

    def get_response(self, message: str) -> dict:
        match = self.find_best_match(message)
        if match:
            return {
                "type": "faq",
                "answer": match["answer"],
                "confidence": match["confidence"],
                "faq_id": match["id"],
                "matched_question": match["question"],
            }
        return {
            "type": "fallback",
            "answer": FALLBACK_MESSAGE,
            "confidence": 0.0,
            "faq_id": None,
            "matched_question": None,
        }

    def search(self, message: str, limit: int = 3, min_score: float = 35.0) -> list[dict]:
        """Recupera contexto para a camada de IA (RAG leve)."""
        return [item for item in self.rank(message, limit=limit) if item["score"] >= min_score]

    # --------------------------------------------------------------------- CRUD
    def add_item(
        self,
        item_id: str,
        topic: str,
        keywords: list[str],
        question: str,
        answer: str,
        priority: int = 100,
    ) -> str:
        return self.db.faq_upsert(item_id, topic, keywords, question, answer, priority)

    def list_topics(self) -> list[dict]:
        return [
            {"id": item["id"], "topic": item["topic"], "question": item["question"]}
            for item in self.db.faq_all()
        ]

    def delete_item(self, item_id: str) -> bool:
        return self.db.faq_delete(item_id)
