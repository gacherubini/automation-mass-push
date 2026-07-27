# HANDOFF

Documento de passagem de bastão. Se você é um agente/pessoa assumindo este
projeto do zero, **leia este arquivo inteiro antes de escrever qualquer linha**.
Ele existe para você não re-decidir o que já foi decidido nem repetir erro já
cometido.

Última atualização: 2026-07-27 (Fase 1 de código fechada; falta só disparo real
manual).

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
  - [x] aceita qualquer pontuação, DDI, zero do DDD
  - [x] classifica celular / fixo / inválido
  - [x] valida DDD contra a lista real
  - [x] variantes do nono dígito
  - [x] normalização estável, para deduplicação funcionar
- [x] `app/ritmo.py` — política anti-ban
  - [x] aquecimento por idade da conexão
  - [x] teto diário e teto horário
  - [x] janela de horário comercial e dias úteis
  - [x] intervalo aleatório com `random.Random` injetável
  - [x] freio de reputação por bloqueio (>2%) e resposta (<15%)
  - [x] amostra mínima antes de acionar o freio
  - [x] avisos de configuração agressiva, sem bloquear o usuário
  - [x] invariantes do padrão travadas por teste (sob 30/h, sem rajada, cabe na janela)
- [x] `app/planilha.py` — import do `.xlsx` do scraper
  - [x] mapeia colunas pelo cabeçalho, tolerante a acento/caixa/ordem
  - [x] erro claro quando falta coluna essencial
  - [x] descarta fixo e inválido, contando cada motivo
  - [x] deduplica dentro da própria planilha
  - [x] relatório de importação para a tela
  - [x] aceita caminho e file-like (upload)
  - [x] invariante travada por teste: `total = sem_telefone + invalidos +
        duplicados + fixos + prontos`. Toda linha cai em exatamente um balde,
        senão o usuário não consegue explicar as lojas que sumiram entre o
        arquivo e a fila
- [x] `app/mensagem.py` — modelo com lacunas
  - [x] lacunas `{nome}` `{categoria}` `{endereco}` `{busca}`
  - [x] múltiplas variações, sorteadas por lead
  - [x] validação do modelo antes do disparo, com erro claro
  - [x] tratamento de lacuna sem valor (nunca sai "None" nem chave crua)
  - [x] prévia preenchida com leads reais
  - [x] medida de diversidade, para avisar quando há variações de menos

### Infraestrutura

- [x] `app/config.py` — configuração por variável de ambiente, sem segredo no código
  - [x] defaults locais, string vazia = ausente, avisos em produção
  - [x] testes em `tests/test_config.py`
- [x] `app/db.py` — engine, sessionmaker, dependência FastAPI
- [x] `app/models.py` — as 7 entidades
  - [x] `Usuario`
  - [x] `Conexao` (instância WhatsApp; `conectada_em` alimenta o aquecimento)
  - [x] `Campanha` (guarda o Perfil de ritmo e os modelos de mensagem)
  - [x] `Lead`
  - [x] `Mensagem` (texto realmente enviado, id externo)
  - [x] `OptOut` (global por usuário, unique)
  - [x] `JaContatado` (global por usuário, unique; `campanha_id` SET NULL)
  - [x] índices nas consultas quentes
  - [x] testes em SQLite em `tests/test_models.py`
- [x] `alembic/` + primeira migração (`329d0dfb5ad1`)
- [x] `docker-compose.yml` — app (profile) + Postgres + Evolution API, com volumes
- [x] `.env.example` documentado
- [x] `Dockerfile` do app

### Integração com o WhatsApp

- [x] `app/evolution.py` — cliente HTTP
  - [x] criar instância
  - [x] obter QR code
  - [x] consultar estado da conexão e número conectado
  - [x] desconectar
  - [x] checar se número tem WhatsApp (em lote, partido em 50)
  - [x] enviar mensagem de texto
  - [x] interpretar payload de webhook, ignorando `fromMe`
  - [x] exceções próprias com mensagem útil em português
  - [x] retry só em operação segura — **nunca** em envio
  - [x] testes com `httpx.MockTransport`, sem rede (`tests/test_evolution.py`)

### Dashboard

- [x] `app/auth.py` — argon2 + sessão assinada com `itsdangerous`
  - [x] hash/verify, login, sessao, CSRF
  - [x] testes em `tests/test_auth.py`
- [x] `app/main.py` — rotas (login, bootstrap, home, conexão, campanhas, leads, opt-outs)
  - [x] testes de rota em `tests/test_main.py` (TestClient + SQLite)
- [x] Tela de login (+ bootstrap do primeiro usuário)
- [x] Tela de conexão com QR code — **com o aviso de risco de ban do número**
- [x] Tela de nova campanha: upload do `.xlsx` + relatório de importação
- [x] Editor de mensagem com prévia preenchida
- [x] Tela de configuração de ritmo, com os avisos de `Perfil.avisos()`
- [x] Acompanhamento do disparo ao vivo (poll JSON a cada 3s enquanto `rodando`)
- [x] Lista de leads com status e filtro
- [x] Tela de opt-outs + exportação CSV (`/optouts.csv`)

### Motor de disparo

- [x] `app/disparo.py`
  - [x] laço que consulta `ritmo.avaliar()` antes de cada envio
  - [x] pausa a campanha e grava `motivo_pausa` quando `freio_permanente`
  - [x] dorme `ritmo.proximo_intervalo()` entre envios (worker em thread)
  - [x] checa WhatsApp antes de enviar, testando as variantes do nono dígito
  - [x] grava em `JaContatado`, nunca repetindo entre campanhas
  - [x] respeita `OptOut` sempre
  - [x] registra falha sem reenviar
  - [x] retomar campanha pausada sem duplicar o que já saiu
  - [x] testes em `tests/test_disparo.py`
- [x] Webhook de resposta (`POST /webhook/evolution`)
  - [x] marca o lead como *Respondeu*
  - [x] detecta pedido de opt-out e grava em `OptOut`
  - [x] alimenta as contagens que o freio de reputação usa (entrega sobe para
        entregue quando há resposta; updates de status quando a Evolution manda)
  - [x] teste de rota em `tests/test_main.py`

### Fechamento da Fase 1

- [x] Caminho ponta a ponta coberto por testes com mocks (Evolution MockTransport
      + motor + webhook + UI). Subir Docker de verdade e escanear QR é checklist
      manual — ver README §6 e §7.
- [ ] Um disparo real, de baixo volume, para números conhecidos (**manual**, no
      número do dono — não automatizável sem WhatsApp real)
- [x] README com instruções de instalação para os amigos
- [x] Checklist deste documento revisado e atualizado

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
lacunas, com várias variações que o sistema sorteia.

**Roda local primeiro** (Docker na máquina do dono), nuvem depois. Limitação
aceita: com o PC desligado, o WhatsApp desconecta.

**Os controles de ritmo ficam expostos na tela**, com padrão conservador e
aviso quando o usuário passa dos limites da pesquisa. O sistema avisa, não
bloqueia.

**Nada de rotação de proxy/IP nem re-registro de número banido.** Isso é driblar
a punição, não ser um remetente melhor, e não funciona de forma confiável.
Decisão consciente, não esquecimento.

---

## 4. O coração do projeto: por que `ritmo.py` existe

Disparar primeira mensagem para quem nunca te escreveu é o gatilho mais comum de
banimento. **A Evolution API não resolve isso**: não tem rate limiter, fila nem
retry. A issue que pedia exatamente esses recursos foi
[fechada como *not planned*](https://github.com/evolution-foundation/evolution-api/issues/2538).

Ou seja: **o controle de ritmo não é um detalhe, é o produto.**

Números que guiam o sistema (fontes no README):

| Limite | Valor |
|---|---|
| Teto por hora | < 30 mensagens (acima de 60/h dispara fiscalização) |
| Aquecimento dias 1-3 / 4-7 / 8-14 | 50 / 100 / 200 por dia |
| Conta madura | < 200/dia |
| Taxa de bloqueio | > 2% derruba a reputação |
| Taxa de resposta | < 15% é zona de perigo |
| Texto idêntico | no máximo ~15 destinatários por hora |

**Armadilha já pisada:** as fontes se contradizem. Várias recomendam "~1
mensagem por minuto", o que dá 60/hora e estoura o próprio teto de 30/hora que
elas indicam. O teto horário vence, porque é o que a fiscalização observa. O
padrão do `Perfil` usa intervalo de **120-300s**, e há teste travando três
invariantes: o pior caso fica sob 30/h, a cota diária não sai em menos de uma
hora (rajada), e ainda cabe na janela comercial. **Se você mexer no intervalo
padrão, esses testes vão te avisar. Ouça-os.**

---

## 5. Convenções de código

Siga o que já está em `app/telefone.py` e `app/ritmo.py`:

- Nomes, docstrings e comentários **em português**; identificadores **sem
  acento** (`normalizar`, `situacao`, `enviadas_hoje`)
- Comentários explicam o **porquê**, não o quê. Densidade moderada
- Funções puras onde der: sem I/O escondido, sem relógio global — **a hora entra
  como parâmetro**, que é o que torna janela e freio testáveis
- `dataclass(frozen=True)` para resultados
- Aleatoriedade recebe um `random.Random` opcional, para o teste ser
  determinístico (ver `ritmo.proximo_intervalo`)
- SQLAlchemy 2 moderno: `DeclarativeBase`, `Mapped[...]`, `mapped_column(...)`
- Testes: classes agrupando por comportamento, nomes descritivos em português
- **Dados de teste realistas.** Use telefones e lojas de verdade capturados do
  Maps — foi assim que o descarte de telefone fixo virou um teste honesto:

  ```
  Bicho Mania          (51) 99898-4086   celular
  Agropet Tipo Bicho   (51) 99858-1025   celular
  CaoTelli             (51) 99765-5755   celular
  Petz Canoas          (51) 3052-0478    FIXO  -> descartar
  Clinica Dra. Daoia   (51) 3466-0454    FIXO  -> descartar
  ```

### Git

Commits **sem** a linha `Co-Authored-By` — o dono do projeto pediu
explicitamente que não apareça atribuição a IA. Mensagens de commit em
português, explicando a motivação, não o diff.

---

## 6. Próximos passos, em ordem

A lista granular está na [seção 2](#2-checklist). Aqui fica só a **ordem** em
que atacar, porque cada passo destrava o seguinte.

1. ~~**Integrar o que os três agentes paralelos entregaram**~~ — feito. Núcleo
   (planilha/mensagem) já estava no `main`; infra + Evolution foram revisados,
   testados (181 testes no total) e commitados. Dockerfile e compose com
   profile `app` incluídos.
2. ~~**`app/auth.py`**~~ — feito.
3. ~~**`app/main.py` + templates**~~ — feito (inclui progresso ao vivo).
4. ~~**`app/disparo.py`**~~ — feito (worker em thread, freio, opt-out,
   ja-contatado, sem retry de envio).
5. ~~**Webhook de resposta**~~ — feito (`/webhook/evolution`).
6. **Disparo real de baixo volume** — manual no número do dono (README).
7. Só então pensar na Fase 2 (IA).

---

## 7. Armadilhas conhecidas

- **Telefone fixo.** Boa parte do que o scraper captura é fixo e não tem
  WhatsApp. Mandar para número inexistente é sinal de spam. `telefone.py` já
  classifica; o motor de disparo **precisa** honrar `provavel_whatsapp`.
- **Nono dígito.** Contas anteriores a 2012 podem estar sem o `9`. Não dá para
  adivinhar: `telefone.variantes()` devolve os candidatos e quem decide é a
  checagem online da Evolution.
- **Nunca fazer retry de envio de mensagem.** Um timeout pode significar que a
  mensagem foi entregue. Reenviar duplica para o lead, o que é pior que uma
  falha visível e conta como spam. Retry só em operação segura (consultar
  status, obter QR).
- **Freio de reputação com amostra pequena.** 1 bloqueio em 5 envios dá 20%, mas
  não significa nada. `ritmo.AMOSTRA_MINIMA_PARA_FREIO` existe por isso.
- **Lacuna sem valor não pode virar buraco.** Loja sem categoria na planilha
  geraria `"Vi a Bicho Mania, de , no Maps"` — espaço duplo e artigo solto
  denunciam o molde na hora. `mensagem.SUBSTITUTOS` troca por um termo neutro
  ("sua loja", "o seu segmento"). O texto sai mais genérico, mas inteiro.
  Efeito colateral aceito: `"de {categoria}"` vira `"de o seu segmento"` em vez
  de `"do seu segmento"`. Contrair exigiria analisar a preposição anterior; o
  usuário vê o resultado na prévia antes de disparar.
- **`{telefone}` e `{link}` não são lacunas válidas, de propósito.** Mostrar
  para a loja que você já tem o número dela soa invasivo.
- **Célula de telefone pode vir como número.** Telefone digitado sem máscara no
  Excel volta como `float` e viraria `"51998581025.0"`. `planilha._texto()`
  cuida disso, e há teste.
- **Extensão Chrome do projeto irmão não recarrega sozinha.** Irrelevante aqui,
  mas se você for mexer no scraper: depois de alterar arquivos é preciso clicar
  em recarregar em `chrome://extensions` **e** dar F5 na página.

---

## 8. O aviso que não pode sumir do produto

Nenhuma dessas medidas torna a operação segura. Automação não-oficial para
mensagem não solicitada viola os termos do WhatsApp, e ferramentas que simulam o
WhatsApp Web são apontadas como a causa número um de banimento em massa. O que
está aqui reduz risco; não elimina.

A alavanca mais forte não é técnica: é mandar menos, para quem tem chance real
de se interessar, e sumir na primeira negativa. Isso deve continuar visível na
interface, não escondido na documentação.
