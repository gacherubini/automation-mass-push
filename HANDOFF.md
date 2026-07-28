# HANDOFF

Documento de passagem de bastão. Se você é um agente/pessoa assumindo este
projeto do zero, **leia este arquivo inteiro antes de escrever qualquer linha**.
Ele existe para você não re-decidir o que já foi decidido nem repetir erro já
cometido.

Última atualização: 2026-07-28 — sessão encerrada pelo dono. Fase 1 utilizável
localmente; um envio real chegou a ser entregue. Próximo passo é estabilizar o
uso diário (Docker/app) e só depois Fase 2.

---

## 0. Estado ao parar (leia primeiro)

**O que está pronto e no `main`:** núcleo puro, planilha, mensagem, models,
Alembic, docker-compose, cliente Evolution, auth, dashboard, motor de disparo,
webhook, presets de ritmo/mensagem na UI, `start-local.ps1`.

**Prova de envio real (nesta máquina):**

- WhatsApp conectado via QR (Evolution)
- Campanha com 1 lead enviou `oiiiiiiiiiiii` para `5519998469808`
- `Mensagem.status_entrega = entregue`, `id_externo` preenchido
- Lead foi a `respondeu` depois

**O que ainda dói no dia a dia (Windows):**

1. **Docker Desktop cai sozinho** → Evolution some → QR e disparo “quebram”
2. **uvicorn some** se o processo background morre → `127.0.0.1 refused`
3. Campanha `rodando` no banco **sem worker** se o app reiniciou (corrigido com
   `retomar_campanhas_rodando` no lifespan — validar em uso real)

**Como subir de novo:**

```powershell
cd C:\Users\guilh\Documents\codigo\automation-mass-push
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
# ou: Docker Desktop aberto + docker compose up -d
#     .\.venv\Scripts\python -m alembic upgrade head
#     .\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dashboard: http://127.0.0.1:8000  
Evolution: http://localhost:8080  

`.env` local (não versionado): precisa de `SECRET_KEY` e `EVOLUTION_API_KEY`.

---

## 1. O que é o projeto

Plataforma de prospecção por WhatsApp.

Fluxo do usuário, em 8 passos:

1. Loga no dashboard
2. *Conectar WhatsApp* → aparece um QR code → escaneia com o celular
3. Cria uma campanha e sobe o `.xlsx` gerado pelo scraper do Google Maps
4. O sistema mostra o relatório: *"40 lojas lidas, 39 com telefone, 31 com
   WhatsApp válido, 8 são fixo"*
5. Escreve a primeira mensagem, com lacunas: `Oi! Vi a {nome} no Google Maps...`
6. Vê a prévia preenchida com lojas reais
7. Dispara. As mensagens saem devagar; a tela mostra progresso ao vivo
8. Quem responde vira status *Respondeu*, e o usuário assume a conversa no
   WhatsApp normal

A planilha de entrada vem do projeto irmão
`C:\Users\guilh\Documents\codigo\scrapping` (extensão de Chrome que raspa o
Google Maps). Colunas, na linha 1:

```
Nome | Telefone | Endereco | Categoria | Nota | Avaliacoes | Anuncio | Link Maps | Busca | Capturado em
```

---

## 2. Checklist

Marque o item **só quando tiver teste passando ou verificação real** — não
quando o arquivo existir. Se um item ficar pela metade, deixe desmarcado e
escreva embaixo dele o que falta.

Rodar os testes antes de marcar qualquer coisa:

```bash
cd C:\Users\guilh\Documents\codigo\automation-mass-push
python -m pytest tests/ -q
```

### Núcleo de decisão (funções puras, sem banco nem rede)

- [x] `app/telefone.py` — normalização BR → E.164
- [x] `app/ritmo.py` — política anti-ban
  - [x] janela usa fuso **America/Sao_Paulo** quando o datetime é aware (UTC do
        worker não descola 9h–18h do usuário)
- [x] `app/planilha.py` — import do `.xlsx` do scraper
- [x] `app/mensagem.py` — modelo com lacunas

### Infraestrutura

- [x] `app/config.py` — env + **carrega `.env` da raiz** (sem isso alembic/uvicorn
      usavam senha default e o Postgres do compose recusava)
- [x] `app/db.py`, `app/models.py`, Alembic, `Dockerfile`
- [x] `docker-compose.yml` — Postgres + Evolution (`evoapicloud/evolution-api:latest`)
  - [x] `extra_hosts: host.docker.internal:host-gateway` (webhook no host)
  - [x] `CONFIG_SESSION_PHONE_*` documentado no `.env.example`
- [x] `start-local.ps1` — sobe Docker + migrate + uvicorn

### Integração com o WhatsApp

- [x] `app/evolution.py`
  - [x] `obter_qrcode` com **retry** quando a API devolve `{"count":0}`
  - [x] mensagens claras se Evolution estiver offline (connection refused)
- [x] Imagem Docker: **não** usar `atendai/evolution-api` (sumiu do Hub) —
      usar `evoapicloud/evolution-api`

### Dashboard

- [x] auth, login, bootstrap, home, conexão (QR + aviso de ban)
- [x] campanha: upload, modelos, ritmo, leads, opt-outs CSV
- [x] progresso ao vivo (poll JSON)
- [x] **presets** de mensagem e ritmo + textos de ajuda na campanha
      (`app/templates_presets.py` + `campanhas/detalhe.html`)
- [x] conexão: poll de status, botão **Recomeçar do zero**

### Motor de disparo

- [x] `app/disparo.py` — laço, freio, WA check, JaContatado, OptOut, sem retry de envio
- [x] worker em thread; **`retomar_campanhas_rodando` no startup** do FastAPI
- [x] webhook `/webhook/evolution` (resposta, opt-out, connection.update, entrega)

### Fechamento da Fase 1

- [x] Testes automatizados (suite ~200+; rodar `pytest tests/ -q`)
- [x] Um disparo real de baixo volume chegou a **entregue** (ver §0)
- [x] README + este HANDOFF atualizados
- [ ] Uso estável no dia a dia (Docker Desktop no Windows ainda é o elo fraco)
- [ ] Volume real (dezenas de lojas) com acompanhamento humano

### Fase 2 — decidida para depois, não comece sem combinar

- [ ] IA (Gemini via n8n) respondendo o lead
- [ ] Regras de quando a IA passa a conversa para humano
- [ ] Deploy em nuvem (Fly.io), para funcionar com o PC desligado

---

## 3. Decisões já tomadas — NÃO reabra sem motivo novo

Estas foram discutidas com o dono do projeto. Mudá-las por conta própria é
retrabalho.

**Cada usuário conecta o próprio WhatsApp por QR code.** Não é um número
central da plataforma. O risco de banimento é do número de quem escaneou, e a
tela do QR precisa avisar isso.

**Fase 1 é só o disparo.** A IA (Gemini via n8n) respondendo o lead ficou
explicitamente para depois. O desenho não deve fechar a porta: disparo e
conversa são módulos separados, e a Fase 1 apenas registra "este lead
respondeu".

**A primeira mensagem é escrita pelo usuário**, não gerada por IA. Modelo com
lacunas, com várias variações que o sistema sorteia. Templates na UI são
atalhos, não substituem o texto do usuário.

**Roda local primeiro** (Docker na máquina do dono), nuvem depois. Limitação
aceita: com o PC desligado, o WhatsApp desconecta.

**Os controles de ritmo ficam expostos na tela**, com padrão conservador e
aviso quando o usuário passa dos limites da pesquisa. O sistema avisa, não
bloqueia. Presets (teste / padrão / conservador / espalhado) só preenchem o
form — o usuário ainda salva.

**Nada de rotação de proxy/IP nem re-registro de número banido.**

**Commits sem `Co-Authored-By`.**

---

## 4. O coração do projeto: por que `ritmo.py` existe

Disparar primeira mensagem para quem nunca te escreveu é o gatilho mais comum de
banimento. **A Evolution API não resolve isso**: não tem rate limiter, fila nem
retry. A issue que pedia exatamente esses recursos foi
[fechada como *not planned*](https://github.com/evolution-foundation/evolution-api/issues/2538).

Ou seja: **o controle de ritmo não é um detalhe, é o produto.**

| Limite | Valor |
|---|---|
| Teto por hora | < 30 mensagens |
| Aquecimento dias 1-3 / 4-7 / 8-14 | 50 / 100 / 200 por dia |
| Conta madura | < 200/dia |
| Taxa de bloqueio | > 2% |
| Taxa de resposta | < 15% |
| Texto idêntico | máx. ~15 destinatários/hora |

Padrão: intervalo **120–300s**. Janela 9h–18h em **horário de Brasília**.

**Exemplo 40 lojas (padrão seguro):** ~2h–3h30 no mesmo dia útil se teto 40 e
aquecimento permitir. Conservador (20/dia) = ~2 dias.

---

## 5. Convenções de código

Siga o que já está em `app/telefone.py`, `app/ritmo.py`, `app/disparo.py`:

- Nomes, docstrings e comentários **em português**; identificadores **sem
  acento**
- Funções puras onde der; hora como parâmetro (testável)
- `dataclass(frozen=True)` para resultados
- SQLAlchemy 2: `Mapped[...]`, `mapped_column(...)`
- Testes: classes por comportamento, dados realistas (telefones de Canoas)

### Git

Mensagens em português, motivação no corpo. Sem `Co-Authored-By`.

---

## 6. Próximos passos (quando retomar)

1. **Rotina estável no Windows:** sempre `start-local.ps1` ou Docker Desktop
   aberto + uvicorn em janela própria (não depender de processo de agente).
2. **Validar retomar worker** após kill do uvicorn: campanha `rodando` deve
   voltar a enviar sozinha no startup.
3. **Campanha de volume baixo real** (5–10 lojas conhecidas) com preset
   conservador, acompanhar entrega/resposta/opt-out.
4. Só então **Fase 2** (IA / nuvem).

Não reabrir: infra, models, cliente Evolution, motor, UI base.

---

## 7. Armadilhas conhecidas

- **Docker Desktop no Windows morre sem aviso.** Sintoma: QR vazio, “connection
  refused”, dashboard ok mas Evolution morta (ou os dois). Abrir Docker e
  `docker compose up -d`.
- **Imagem Evolution:** `evoapicloud/evolution-api:latest` (v2.3.x). A antiga
  `atendai/evolution-api` não puxa mais. v2.2.3 gerava QR `count:0` com Baileys
  desatualizado.
- **QR `count:0`:** retry no `obter_qrcode`; não criar instância nova a cada
  falha (gerava dezenas de órfãs e o Baileys entrava em loop). Botão
  “Recomeçar do zero” apaga e recria uma.
- **Modelo na caixa ≠ modelo salvo.** Sem “Salvar modelos”, Iniciar fica
  bloqueado (“Salve pelo menos um modelo”).
- **Worker some com o processo.** Status `rodando` no banco não garante thread
  viva. Lifespan chama `retomar_campanhas_rodando`.
- **Janela de horário em SP.** Datetime UTC do servidor convertido em
  `America/Sao_Paulo` em `ritmo.dentro_da_janela` / contadores do dia.
- **Telefone fixo / nono dígito / sem retry de envio / freio com amostra
  mínima / lacunas / float do Excel** — ver também README e §7 histórico:
  - fixo → descartar (`provavel_whatsapp`)
  - nono dígito → `telefone.variantes` + checagem Evolution
  - timeout de envio → falha, **não** reenvia
  - freio só com amostra ≥ 20
  - lacuna vazia → `SUBSTITUTOS`, não buraco
  - `{telefone}`/`{link}` proibidos
  - Excel float → `planilha._texto()`
- **Webhook** precisa alcançar o app (`WEBHOOK_GLOBAL_URL` com
  `host.docker.internal` se uvicorn está no host).

---

## 8. O aviso que não pode sumir do produto

Nenhuma dessas medidas torna a operação segura. Automação não-oficial para
mensagem não solicitada viola os termos do WhatsApp, e ferramentas que simulam o
WhatsApp Web são apontadas como a causa número um de banimento em massa. O que
está aqui reduz risco; não elimina.

A alavanca mais forte não é técnica: é mandar menos, para quem tem chance real
de se interessar, e sumir na primeira negativa. Isso deve continuar visível na
interface, não escondido na documentação.

---

## 9. Mapa rápido de arquivos

```
app/
  telefone.py ritmo.py planilha.py mensagem.py
  config.py db.py models.py
  evolution.py auth.py disparo.py
  templates_presets.py   # textos da UI (mensagem + ritmo)
  main.py                # FastAPI + lifespan retoma workers
  templates/ campanhas/detalhe.html  # presets + ajuda
  templates/ conexao.html            # QR + poll + recomeçar
start-local.ps1
docker-compose.yml
HANDOFF.md README.md
tests/
```
