# 🤖 Bot de Atendimento IA para WhatsApp

Bot de atendimento ao cliente para WhatsApp, com FAQ inteligente, camada de IA
(OpenAI/Ollama) com contexto, escalonamento para atendente humano, painel
administrativo e persistência em SQLite. Usa a **API oficial do WhatsApp
Business (Meta Cloud API)**.

```
mensagem → webhook → dedupe → rate limit → estado (bot/humano)
                                    ↓
                       FAQ (exato) → IA (com contexto) → fallback
                                    ↓
                             resposta no WhatsApp
```

## ✨ Recursos

| Recurso | Descrição |
|---|---|
| **FAQ inteligente** | Casamento por palavras-chave + similaridade difusa (`rapidfuzz`), com ranking de confiança. Normaliza acentos, caixa e pontuação. |
| **Camada de IA** | OpenAI (ou compatível) e Ollama local. A IA recebe os trechos relevantes do FAQ como contexto (RAG leve) + histórico da conversa. |
| **FAQ primeiro, IA depois** | Resposta exata da base vence sempre; a IA só entra quando o FAQ não tem certeza. Sem IA configurada, cai no fallback. |
| **Escalonamento humano** | Detecta pedido de atendente, marca a conversa como `human`, para de responder automaticamente e notifica o admin. |
| **Painel admin** | `/dashboard`: KPIs, conversas, eventos, CRUD do FAQ e simulador de respostas. |
| **Persistência** | SQLite com WAL: conversas, mensagens, itens do FAQ, eventos e dedupe. |
| **Anti-duplicata** | Descarta reentregas do webhook por `wa_message_id`. |
| **Rate limiting** | Limite por contato/minuto, configurável. |
| **Segurança** | Verificação `X-Hub-Signature-256` (HMAC-SHA256) e token de admin nas rotas `/api/*`. |
| **Robustez** | Retry com backoff para 429/5xx, split automático de mensagens longas, nunca devolve 5xx para a Meta. |
| **Mensagens interativas** | Botões de resposta rápida e listas; interpreta `button_reply`/`list_reply` recebidos. |
| **Horário comercial** | Aviso automático fora do horário (opcional). |
| **Docker + Makefile** | Pronto para container e para o dia a dia. |
| **53 testes** | Cobertura de FAQ, bot, segurança, webhook, API e IA contra servidor HTTP real. |

## 📋 Pré-requisitos

1. **Conta Meta Business** — [business.facebook.com](https://business.facebook.com/)
2. **Número dedicado** para o WhatsApp Business (não pode ser seu WhatsApp pessoal)
3. **App no Meta for Developers** — [developers.facebook.com](https://developers.facebook.com/)
4. **Token de acesso** e **Phone Number ID** (painel do app → WhatsApp → API Setup)

> Alternativa sem custo para testes: use `DRY_RUN=true` — o bot roda inteiro e
> registra tudo, mas não chama a API do WhatsApp.

## 🚀 Instalação

```bash
cd whatsapp-bot

make install          # cria venv e instala dependências
cp .env.example .env  # edite com suas credenciais
make test             # roda a suíte (não precisa de credenciais)
```

Manualmente:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## ⚙️ Configuração

Todas as variáveis estão documentadas em `.env.example`. As principais:

### WhatsApp (obrigatório para produção)

```env
WHATSAPP_TOKEN=EAAG...              # token de acesso
WHATSAPP_PHONE_NUMBER_ID=1234567890 # ID do número
WHATSAPP_APP_SECRET=abc123...       # usado para validar a assinatura
GRAPH_VERSION=v21.0
VERIFY_TOKEN=troque_por_token_longo # você inventa; repete no painel da Meta
VERIFY_SIGNATURE=true               # obrigatório em produção
```

### IA (opcional)

```env
AI_PROVIDER=openai        # none | openai | ollama
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# ou local, sem custo:
# AI_PROVIDER=ollama
# OLLAMA_URL=http://localhost:11434
# OLLAMA_MODEL=llama3.1:latest
```

### Comportamento

```env
COMPANY_NAME=Minha Loja
BOT_NAME=Assistente Virtual
ADMIN_TOKEN=token_para_proteger_as_apis   # vazio = APIs abertas (só dev!)
ADMIN_WHATSAPP_NUMBER=5511999999999       # recebe avisos de handoff
RATE_LIMIT_PER_MINUTE=20
BUSINESS_HOURS_ENABLED=true
FAQ_THRESHOLD=0.85                        # 0–1: quanto maior, mais conservador
```

### Webhook no Meta

1. Exponha o servidor: `make tunnel` (ngrok) ou use seu domínio com HTTPS.
2. Meta for Developers → seu app → **WhatsApp → Configuration**.
3. **Callback URL**: `https://SEU-DOMINIO/webhook`
4. **Verify Token**: o mesmo valor de `VERIFY_TOKEN`.
5. **Verify and Save**, depois assine o campo **`messages`**.

## ▶️ Execução

```bash
make dev      # desenvolvimento (debug, sem reloader duplicado)
make run      # produção (gunicorn, 4 workers)

# ou com Docker
make docker-up
```

Painel: `http://localhost:5000/dashboard`

## 📡 Endpoints

| Método | Endpoint | Auth | Descrição |
|---|---|---|---|
| GET | `/` | – | Status e versão |
| GET | `/health` | – | Saúde: banco, IA, WhatsApp, horário |
| GET | `/dashboard` | – | Painel administrativo (HTML) |
| GET | `/webhook` | – | Verificação do webhook (Meta) |
| POST | `/webhook` | assinatura | Recebe mensagens e status |
| POST | `/api/simulate` | admin | Simula mensagem recebida (não envia nada) |
| POST | `/api/send` | admin | Envia mensagem manual |
| POST | `/api/faq/test` | admin | Testa o motor + mostra ranking |
| GET | `/api/faq` | admin | Lista itens da base |
| POST | `/api/faq` | admin | Cria/atualiza item |
| DELETE | `/api/faq/<id>` | admin | Remove item |
| GET | `/api/conversations` | admin | Lista conversas (`?state=human`) |
| GET | `/api/conversations/<wa_id>/messages` | admin | Histórico do contato |
| POST | `/api/conversations/<wa_id>/release` | admin | Devolve a conversa ao bot |
| GET | `/api/stats` | admin | Métricas + últimos 7 dias |
| GET | `/api/events` | admin | Eventos (handoff, fallback, rate limit…) |
| GET | `/api/whatsapp/info` | admin | Dados do número na Meta |

Autenticação admin: header `X-Admin-Token: <ADMIN_TOKEN>` ou `Authorization: Bearer <ADMIN_TOKEN>`.

## 🧪 Testes

```bash
make test                                     # 53 testes
venv/bin/python -m pytest tests/ -v           # detalhado
```

A suíte cobre: casamento de intenção do FAQ, fallback para mensagens sem sentido,
CRUD, dedupe, handoff e devolução ao bot, rate limit, split de mensagens, HMAC,
webhook (verificação, mensagens, botões, assinatura inválida, JSON inválido,
duplicatas) e a camada de IA ponta a ponta contra um servidor HTTP real
(`tests/mock_llm_server.py`).

## 📝 Personalizar a base de conhecimento

**Opção 1 — painel:** `/dashboard` → *Adicionar item ao FAQ*.

**Opção 2 — API:**

```bash
curl -X POST http://localhost:5000/api/faq \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: SEU_TOKEN" \
  -d '{
    "id": "garantia",
    "topic": "Garantia",
    "keywords": ["garantia", "defeito de fabrica", "cobertura"],
    "question": "Garantia dos produtos",
    "answer": "Todos os produtos têm 12 meses de garantia."
  }'
```

**Opção 3 — código:** edite `DEFAULT_FAQ` em `faq_engine.py`. Os itens são
inseridos no banco apenas quando ele está vazio.

> O casamento ignora acentos e caixa, mas **prefira palavras-chave com 4+ letras**:
> termos de até 3 caracteres só casam por correspondência exata, para não gerar
> falso positivo.

## 🐛 Solução de problemas

| Sintoma | Causa provável |
|---|---|
| Webhook não verifica (403) | `VERIFY_TOKEN` diferente do configurado na Meta |
| Mensagens chegam mas nada é enviado | `WHATSAPP_TOKEN`/`WHATSAPP_PHONE_NUMBER_ID` vazios → veja `/health` |
| `http_401` ao enviar | Token expirado ou número não registrado |
| `http_470`/`http_131047` | Fora da janela de 24h — é preciso um template aprovado |
| IA nunca responde | `AI_PROVIDER=none`, ou provedor inacessível → veja `/health` |
| Respostas erradas do FAQ | Aumente `FAQ_THRESHOLD` ou refine as palavras-chave |
| `degraded` no `/health` | Provedor de IA configurado mas inacessível |

## 🔒 Segurança

- **Nunca** commite o `.env` (já está no `.gitignore`).
- Em produção: `VERIFY_SIGNATURE=true`, `ADMIN_TOKEN` forte e HTTPS.
- Sem `ADMIN_TOKEN`, as rotas `/api/*` ficam **abertas** — só use assim localmente.
- O prompt da IA proíbe pedir dados sensíveis (senha, cartão, CPF, código).
- A IA só responde a partir do contexto fornecido; o que não estiver na base é
  encaminhado para um atendente.

## 📄 Licença

MIT
