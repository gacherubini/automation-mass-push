"""Textos prontos exibidos no dashboard (mensagens e ritmos).

So dados para a tela — o disparo nao importa daqui.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mensagens: packs de variacoes separadas por ---
# ---------------------------------------------------------------------------

MODELOS_IA_PEQUENOS_NEGOCIOS: tuple[str, ...] = (
    (
        "Oi, espero que esteja tudo bem. Trabalho com automações de IA e consultoria para "
        "pequenos negócios, com foco em reduzir tarefas manuais e melhorar o "
        "atendimento. Gostaria de marcar uma conversa de 15 minutos com o "
        "responsável da {nome}. Você consegue me indicar com quem falar?"
    ),
    (
        "Olá! Meu trabalho é ajudar pequenos negócios a simplificar atendimento "
        "e rotinas com automações de IA. Queria entender se isso faz sentido "
        "para a {nome} e, se sim, agendar uma conversa breve com o responsável. "
        "Você é a pessoa certa?"
    ),
    (
        "Bom dia! Trabalho com consultoria e automações de IA para pequenas "
        "empresas. Queria conversar com quem cuida de atendimento e processos "
        "na {nome} para identificar se há algo que valha automatizar. Com quem "
        "posso falar?"
    ),
    (
        "Oi! Encontrei a {nome} pesquisando empresas de {categoria}. Trabalho "
        "com automações de IA para pequenos negócios e gostaria de apresentar "
        "algumas possibilidades em uma reunião curta. Posso falar com o "
        "responsável por essa área?"
    ),
)

PRESETS_MENSAGEM: list[dict[str, str]] = [
    {
        "id": "ia_pequenos_negocios",
        "titulo": "IA para pequenos negócios (recomendado)",
        "descricao": "Apresenta o serviço e pede o contato do responsável.",
        "texto": "\n---\n".join(MODELOS_IA_PEQUENOS_NEGOCIOS),
    },
    {
        "id": "pet_maps",
        "titulo": "Pet / loja no Maps",
        "descricao": "Natural, cita {nome}. Três variações — bom para ~40 lojas.",
        "texto": (
            "Oi! Vi a {nome} no Google Maps e achei interessante. "
            "Trabalho com soluções para o segmento e queria saber se faz sentido uma conversa rápida.\n"
            "---\n"
            "Olá! Estava pesquisando {categoria} na região e achei a {nome}. "
            "Posso te mandar uma ideia objetiva em 2 minutos?\n"
            "---\n"
            "Bom dia! Vi a {nome} ({endereco}) no Maps. Se fizer sentido pro negócio de vocês, "
            "me conta que eu te explico sem enrolação."
        ),
    },
    {
        "id": "curto",
        "titulo": "Curto e direto",
        "descricao": "Mensagens curtas. Menos formal.",
        "texto": (
            "Oi, tudo bem? Vi a {nome} no Maps e achei o trabalho de vocês legal. "
            "Posso te contar uma ideia rápida?\n"
            "---\n"
            "Olá! Sou de [sua empresa]. Vi a {nome} e queria saber se dá pra bater um papo curto sobre [assunto].\n"
            "---\n"
            "Fala! Vi a {nome} no Google. Se tiver 2 min, te mostro algo que tem funcionado com lojas de {categoria}."
        ),
    },
    {
        "id": "formal",
        "titulo": "Mais formal",
        "descricao": "Tom profissional, bom para B2B.",
        "texto": (
            "Olá, tudo bem? Meu nome é [seu nome], da [empresa]. Encontrei a {nome} no Google Maps "
            "e gostaria de apresentar uma proposta objetiva para o segmento de {categoria}.\n"
            "---\n"
            "Bom dia. Vi a {nome} em {endereco} e acredito que nosso trabalho possa agregar. "
            "Posso enviar um resumo de 3 linhas?\n"
            "---\n"
            "Olá! Pesquisa por {busca} me trouxe até a {nome}. Se fizer sentido, "
            "combinamos um horário breve para conversar."
        ),
    },
    {
        "id": "teste",
        "titulo": "Só teste (1 número)",
        "descricao": "Para validar se o disparo chega. Não use em lista real.",
        "texto": (
            "Oi! Mensagem de teste do sistema de prospecção. "
            "Pode ignorar — é só pra confirmar que o envio funciona."
        ),
    },
]


# ---------------------------------------------------------------------------
# Ritmo: presets com os campos do form
# ---------------------------------------------------------------------------

PRESETS_RITMO: list[dict] = [
    {
        "id": "teste",
        "titulo": "Teste (1–3 lojas)",
        "descricao": (
            "Intervalo curto e janela larga só para validar. "
            "Não use com lista grande — aumenta risco de ban."
        ),
        "quando": "Primeiro disparo do dia, números seus.",
        "teto_diario": 5,
        "intervalo_min_seg": 30,
        "intervalo_max_seg": 60,
        "hora_inicio": "08:00",
        "hora_fim": "22:00",
        "dias_uteis_apenas": False,
        "respeitar_aquecimento": True,
        "resumo_40": "Não serve para 40 lojas.",
    },
    {
        "id": "padrao",
        "titulo": "Padrão seguro (recomendado)",
        "descricao": (
            "2–5 min entre mensagens, 40/dia, horário comercial. "
            "É o equilíbrio entre volume e risco."
        ),
        "quando": "Uso normal, conexão já com alguns dias.",
        "teto_diario": 40,
        "intervalo_min_seg": 120,
        "intervalo_max_seg": 300,
        "hora_inicio": "09:00",
        "hora_fim": "18:00",
        "dias_uteis_apenas": True,
        "respeitar_aquecimento": True,
        "resumo_40": (
            "40 lojas ≈ 2h–3h30 de envio no mesmo dia útil "
            "(se o aquecimento deixar)."
        ),
    },
    {
        "id": "conservador",
        "titulo": "Conservador (conexão nova)",
        "descricao": (
            "Mais devagar, menos por dia. Melhor nos primeiros dias "
            "depois de escanear o QR."
        ),
        "quando": "Conexão com 0–7 dias de vida.",
        "teto_diario": 20,
        "intervalo_min_seg": 180,
        "intervalo_max_seg": 360,
        "hora_inicio": "09:00",
        "hora_fim": "17:00",
        "dias_uteis_apenas": True,
        "respeitar_aquecimento": True,
        "resumo_40": "40 lojas em ~2 dias úteis (20 + 20).",
    },
    {
        "id": "espalhado",
        "titulo": "Espalhado (vários dias)",
        "descricao": "Volume baixo por dia, parece uso humano diluído.",
        "quando": "Listas grandes ou número que você não quer forçar.",
        "teto_diario": 15,
        "intervalo_min_seg": 240,
        "intervalo_max_seg": 420,
        "hora_inicio": "10:00",
        "hora_fim": "16:00",
        "dias_uteis_apenas": True,
        "respeitar_aquecimento": True,
        "resumo_40": "40 lojas em ~3 dias úteis (15+15+10).",
    },
]


EXPLICACAO_RITMO = {
    "titulo": "Como o ritmo funciona (exemplo: 40 lojas)",
    "paragrafos": [
        "O sistema manda UMA mensagem por vez. Depois espera um intervalo "
        "sorteado entre o mínimo e o máximo. Não é rajada de 40 de uma vez.",
        "A primeira mensagem sai assim que a campanha inicia (se estiver "
        "dentro do horário). As outras esperam 2–5 minutos no padrão seguro.",
        "Se bater o teto do dia ou fechar a janela de horário, para e continua "
        "no próximo dia útil com os leads que ainda estão pendentes. "
        "Nunca reenvia o que já saiu.",
        "Aquecimento: conexão nova tem teto menor nos primeiros dias. "
        "O sistema usa o menor entre o seu teto e o do aquecimento.",
    ],
    "passos": [
        "Iniciar disparo",
        "Pega o próximo lead pendente",
        "Confere se tem WhatsApp",
        "Envia o texto (sorteia uma variação)",
        "Espera o intervalo",
        "Repete até acabar a lista, bater o teto ou sair da janela",
    ],
}


EXPLICACAO_MENSAGEM = {
    "titulo": "Como escrever o modelo",
    "paragrafos": [
        "Você escreve o texto; o sistema só preenche as lacunas com dados da planilha.",
        "Use várias variações separadas por uma linha com apenas --- . "
        "Texto idêntico em dezenas de números é sinal de spam.",
        "Depois de editar, clique em Salvar modelos. Sem salvar, o botão "
        "Iniciar continua bloqueado.",
    ],
    "lacunas": [
        ("{nome}", "Nome da loja (ex.: Bicho Mania)"),
        ("{categoria}", "Segmento no Maps (ex.: Pet shop)"),
        ("{endereco}", "Endereço capturado"),
        ("{busca}", "Termo que você buscou no scraper"),
    ],
    "dicas": [
        "Troque [seu produto], [sua empresa] e [seu nome] pelos dados reais.",
        "Não use {telefone} nem {link} — soa invasivo e o sistema bloqueia.",
        "Se a planilha não tiver categoria, o sistema coloca um termo neutro "
        "para a frase não ficar com buraco.",
    ],
}
