# HANDOFF

Documento de passagem de bastão. Se você é um agente/pessoa assumindo este
projeto do zero, **leia este arquivo inteiro antes de escrever qualquer linha**.
Ele existe para você não re-decidir o que já foi decidido nem repetir erro já
cometido.

Última atualização: 2026-07-27.

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

## 2. Estado atual

| Módulo | Estado | Observação |
|---|---|---|
| `app/telefone.py` | ✅ pronto, testado | Normalização BR → E.164, classifica celular/fixo/inválido |
| `app/ritmo.py` | ✅ pronto, testado | Política anti-ban: aquecimento, tetos, janela, freio |
| `app/planilha.py` | 🚧 em andamento | Import do `.xlsx` |
| `app/mensagem.py` | 🚧 em andamento | Modelo com lacunas + variações |
| `app/config.py`, `db.py`, `models.py` | 🚧 em andamento | Entidades e conexão |
| `alembic/`, `docker-compose.yml` | 🚧 em andamento | Migrações e os 3 containers |
| `app/evolution.py` | 🚧 em andamento | Cliente HTTP da Evolution API |
| `app/auth.py` | ❌ não começado | Login argon2 + sessão assinada |
| `app/main.py` + templates | ❌ não começado | Dashboard |
| `app/disparo.py` | ❌ não começado | Motor de fila; costura ritmo + evolution + banco |
| IA respondendo (Gemini + n8n) | ❌ fora de escopo agora | **Fase 2**, decidida para depois |

Rodar os testes:

```bash
cd C:\Users\guilh\Documents\codigo\automation-mass-push
python -m pytest tests/ -q
```

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

1. **Integrar o que os três agentes paralelos entregaram** (planilha, mensagem,
   infra, evolution). Rodar a suíte inteira, resolver conflito de import,
   commitar.
2. **`app/auth.py`** — login com argon2, sessão assinada com `itsdangerous`. O
   projeto `C:\Users\guilh\Documents\codigo\bot-whatsapp-financiamento\portal-gestao\app\auth.py`
   já resolve isso com a mesma stack; use como referência.
3. **`app/main.py` + templates Jinja2** — telas: login, conexão (QR), nova
   campanha (upload + relatório de import), editor de mensagem com prévia,
   acompanhamento do disparo, lista de leads.
4. **`app/disparo.py`** — o motor. É a peça mais delicada:
   - laço que pega o próximo lead pendente, chama `ritmo.avaliar(...)`, e só
     envia se `liberado`
   - se `decisao.freio_permanente`, **pausar a campanha** e gravar
     `motivo_pausa` — não é uma espera, é um "pare e reveja o texto"
   - dormir `ritmo.proximo_intervalo(...)` entre envios
   - antes de enviar, checar se o número tem WhatsApp (`evolution.py`) e testar
     as variantes do nono dígito (`telefone.variantes`)
   - gravar em `JaContatado` para nunca repetir entre campanhas
   - respeitar `OptOut` sempre
5. **Webhook de resposta** — recebe o payload da Evolution, marca o lead como
   *Respondeu*, e detecta pedido de opt-out ("para", "sair", "não tenho
   interesse") gravando em `OptOut`.
6. Só então pensar na Fase 2 (IA).

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
