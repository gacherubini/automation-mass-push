# automation-mass-push

Plataforma de prospecção por WhatsApp. Recebe a planilha de lojas gerada pelo
scraper do Google Maps, dispara uma primeira mensagem em ritmo controlado e
acompanha quem respondeu.

Cada usuário loga no dashboard e conecta o **próprio** WhatsApp lendo um QR
code. As mensagens saem do número de quem escaneou.

> **Status:** em construção. O núcleo de decisão (normalização de telefone e
> política de ritmo) está pronto e testado. Dashboard, banco e integração com a
> Evolution API ainda não.

---

## O problema real deste projeto

Disparar primeira mensagem para quem nunca te escreveu é o gatilho mais comum
de banimento no WhatsApp. A Evolution API — que é o que conecta o número — **não
resolve isso**: ela não tem rate limiter, fila nem retry. A issue que pedia
exatamente esses recursos foi [fechada como *not planned*][issue-2538].

Ou seja: o controle de ritmo não é um detalhe do projeto, **é o projeto**.

### Os números que guiam o sistema

Levantados de fontes brasileiras e internacionais que convergem (ver
[Referências](#referências)):

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

Uma contradição aparece nas fontes: várias recomendam "~1 mensagem por minuto",
o que dá 60/hora e estoura o teto de 30/hora. O teto horário é o que a
fiscalização observa, então ele vence — o padrão usa intervalo de 120 a 300
segundos, e há um teste que trava essa invariante.

### O que o sistema faz por você

1. **Fila com ritmo humano** — intervalo sorteado dentro de uma faixa. Intervalo
   fixo é assinatura de robô.
2. **Aquecimento automático** — o teto do dia sobe conforme a conexão amadurece,
   e o sistema recusa passar dele.
3. **Janela de horário comercial** — 9h às 18h, dias úteis. Volume concentrado
   em ~8h parece humano; espalhado por 24h não.
4. **Freio de reputação** — se o bloqueio passa de 2% ou a resposta cai abaixo
   de 15%, a campanha **pausa sozinha**. É o aviso de que o texto está ruim
   antes de o número morrer.
5. **Descarte de telefone fixo** — boa parte dos telefones do Maps é fixo e não
   tem WhatsApp. Mandar para número inexistente é mais um sinal de spam.
6. **Opt-out permanente** — quem pede para parar entra numa lista global e não
   recebe mais nada, em nenhuma campanha.
7. **Nunca repetir número** — a lista de já-contatados vale entre campanhas e
   entre planilhas diferentes.

### O que o sistema deliberadamente NÃO faz

Rotação de proxy/IP para mascarar fingerprint, e re-registro de número banido.
Isso não é ser um remetente melhor, é driblar a punição — e não funciona de
forma confiável, porque o vínculo é reconstruído pelo padrão de comportamento,
não só pelo IP.

### Aviso

Nenhuma dessas medidas torna a operação segura. Automação não-oficial para
mensagem não solicitada viola os termos do WhatsApp, e ferramentas que simulam o
WhatsApp Web são apontadas como a causa número um de banimento em massa. O que
está aqui reduz risco; não elimina.

A alavanca mais forte não é técnica: é mandar menos, para quem tem chance real
de se interessar, e sumir na primeira negativa.

---

## Estrutura

```
app/
  telefone.py   normalização BR -> E.164, classifica celular/fixo/inválido
  ritmo.py      política de ritmo: aquecimento, tetos, janela, freio
tests/
```

`telefone.py` e `ritmo.py` são funções puras — sem banco, sem rede, sem relógio
global. A hora entra como parâmetro justamente para o freio e a janela serem
testáveis.

## Testes

```
python -m pytest tests/ -q
```

## Referências

- [Evolution API #2538 — bulk messaging, rate limiting e risco de ban][issue-2538]
  (fechada como *not planned*)
- [Como evitar banimento no WhatsApp em 2026 — whapi.cloud](https://whapi.cloud/blog/pt/how-to-avoid-whatsapp-ban-2026)
- [Como Evitar Banimento WhatsApp em 2026 — Unred](https://unred.com.br/blog/como-evitar-banimento-whatsapp)
- [WhatsApp bloqueado por spam — Blip](https://www.blip.ai/blog/whatsapp/whatsapp-bloqueado-por-spam/)
- [Aquecimento de chip e disparo em massa — blü](https://blu.direct/blog/o-que-fazer-sobre-whatsapp-restringido)

[issue-2538]: https://github.com/evolution-foundation/evolution-api/issues/2538
