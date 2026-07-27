# automation-mass-push

Plataforma de prospecção por WhatsApp. Recebe a planilha de lojas gerada pelo
scraper do Google Maps, dispara uma primeira mensagem em ritmo controlado e
acompanha quem respondeu.

Cada usuário loga no dashboard e conecta o **próprio** WhatsApp lendo um QR
code. As mensagens saem do número de quem escaneou.

> **Status Fase 1:** código completo e testado (212 testes). Falta só o
> disparo real no seu número (QR + volume baixo) para validar em produção
> local — isso não roda sozinho no CI.

---

## O problema real deste projeto

Disparar primeira mensagem para quem nunca te escreveu é o gatilho mais comum
de banimento no WhatsApp. A Evolution API — que é o que conecta o número — **não
resolve isso**: ela não tem rate limiter, fila nem retry. A issue que pedia
exatamente esses recursos foi [fechada como *not planned*][issue-2538].

Ou seja: o controle de ritmo não é um detalhe do projeto, **é o projeto**.

### Os números que guiam o sistema

| Limite | Valor |
|---|---|
| Teto por hora | **< 30 mensagens** (acima de 60/h dispara fiscalização) |
| Aquecimento, dias 1-3 | 20-50/dia |
| Dias 4-7 | 50-100/dia |
| Dias 8-14 | 100-200/dia |
| Conta madura | < 200/dia |
| Taxa de bloqueio | acima de **2%** derruba a reputação do número |
| Taxa de resposta | abaixo de **15%** é zona de perigo |
| Mensagem idêntica | no máximo ~15 destinatários por hora |

O padrão usa intervalo de **120 a 300 segundos**. Há testes travando as
invariantes do ritmo.

### O que o sistema faz por você

1. **Fila com ritmo humano** — intervalo sorteado; intervalo fixo é assinatura de robô.
2. **Aquecimento automático** — teto do dia sobe com a idade da conexão.
3. **Janela comercial** — 9h–18h, dias úteis (configurável).
4. **Freio de reputação** — bloqueio >2% ou resposta <15% **pausa** a campanha.
5. **Descarte de telefone fixo** na importação da planilha.
6. **Opt-out permanente** global por usuário.
7. **Nunca repetir número** entre campanhas (`JaContatado`).
8. **Sem retry de envio** — timeout grava falha e segue (reenviar pode duplicar).

### Aviso

Nenhuma dessas medidas torna a operação segura. Automação não-oficial para
mensagem não solicitada viola os termos do WhatsApp. O que está aqui reduz
risco; não elimina. A alavanca mais forte: mandar menos, para quem tem chance
real de se interessar, e sumir na primeira negativa.

---

## Instalação (para você e para os amigos)

### Requisitos

- Python 3.12+ (3.14 também funciona nos testes)
- Docker Desktop (Postgres + Evolution API)
- Git

### 1. Clonar e dependências

```bash
git clone <url-deste-repo>
cd automation-mass-push
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configurar o ambiente

```bash
cp .env.example .env
```

Edite o `.env` e **preencha obrigatoriamente**:

```bash
# Gere com:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=...cole-aqui...

#   python -c "import secrets; print(secrets.token_urlsafe(32))"
EVOLUTION_API_KEY=...cole-aqui...
```

Sem essas duas o `docker compose` recusa subir (de propósito).

### 3. Subir banco + Evolution

```bash
docker compose up -d
# sobe postgres (:5432) e evolution-api (:8080)
```

Aguarde o healthcheck do Postgres (~10s) e confira:

```bash
docker compose ps
```

### 4. Criar as tabelas

```bash
alembic upgrade head
```

### 5. Rodar o dashboard

```bash
uvicorn app.main:app --reload --port 8000
```

Abra http://localhost:8000

1. Na **primeira vez**, use **Criar conta inicial** (`/bootstrap`)
2. Vá em **Conexão** → gere o QR → escaneie com o WhatsApp do celular  
   (leia o aviso de risco de ban — o risco é do número que escanear)
3. **Nova campanha** → suba o `.xlsx` do scraper do Maps
4. Escreva os **modelos** com lacunas `{nome}` `{categoria}` `{endereco}` `{busca}`  
   (separe variações com uma linha `---`)
5. Ajuste o **ritmo** se quiser (o padrão é conservador)
6. **Iniciar disparo** e acompanhe o progresso na própria tela

### 6. Webhook (respostas e freio)

Com app e Evolution no mesmo `docker compose --profile app`, o webhook já aponta
para `http://app:8000/webhook/evolution`.

Se o app roda no host (`uvicorn` local) e a Evolution no Docker, configure no
`.env` antes de recriar a Evolution:

```bash
WEBHOOK_GLOBAL_ENABLED=true
# No Windows/Mac Docker Desktop, host.docker.internal alcança o host:
WEBHOOK_GLOBAL_URL=http://host.docker.internal:8000/webhook/evolution
```

Depois: `docker compose up -d evolution-api`

Sem webhook, respostas e bloqueios não voltam — o freio de reputação fica cego.

### 7. Testes

```bash
python -m pytest tests/ -q
```

Tudo que não precisa de WhatsApp real está coberto (telefone, ritmo, planilha,
mensagem, models, Evolution com mock, auth, rotas, motor de disparo, webhook).

### Disparo real de baixo volume (checklist manual)

1. Conecte um número **descartável / de teste**, não o pessoal principal.
2. Crie campanha com **2–3 números seus** (amigos/SIMs que você controla).
3. Intervalo alto (ex.: 180–300s), teto diário baixo (5–10).
4. Dispare, confirme entrega no celular, responda de um número e veja o status
   *respondeu*; responda "para" e confira opt-out.
5. Só então aumente volume devagar, respeitando aquecimento.

---

## Estrutura

```
app/
  telefone.py    normalização BR → E.164
  ritmo.py       aquecimento, tetos, janela, freio
  planilha.py    import .xlsx do scraper
  mensagem.py    modelos com lacunas + prévia
  config.py      variáveis de ambiente
  db.py          engine / sessão
  models.py      7 entidades
  evolution.py   cliente HTTP Evolution API v2
  auth.py        argon2 + sessão/CSRF
  disparo.py     motor (ritmo + WA check + envio + worker)
  main.py        dashboard + webhook
docker-compose.yml
alembic/
tests/
HANDOFF.md       decisões e checklist para o próximo dev
```

---

## Docker: só infra ou app completo

```bash
docker compose up -d                     # postgres + evolution
docker compose --profile app up -d       # + app (precisa Dockerfile ok)
```

O serviço `app` usa a imagem construída do `Dockerfile` e aponta
`DATABASE_URL` / `EVOLUTION_URL` para os hosts internos da rede compose.

---

## Referências

- [Evolution API #2538 — bulk messaging, rate limiting e risco de ban][issue-2538]
- [Como evitar banimento no WhatsApp em 2026 — whapi.cloud](https://whapi.cloud/blog/pt/how-to-avoid-whatsapp-ban-2026)
- [Como Evitar Banimento WhatsApp em 2026 — Unred](https://unred.com.br/blog/como-evitar-banimento-whatsapp)
- [WhatsApp bloqueado por spam — Blip](https://www.blip.ai/blog/whatsapp/whatsapp-bloqueado-por-spam/)

[issue-2538]: https://github.com/evolution-foundation/evolution-api/issues/2538
