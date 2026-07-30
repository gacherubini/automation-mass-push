"""Textos prontos exibidos no dashboard (mensagens e ritmos).

So dados para a tela — o disparo nao importa daqui.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mensagens prontas para o usuario escolher individualmente
# ---------------------------------------------------------------------------

MODELOS_IA_PEQUENOS_NEGOCIOS: tuple[str, ...] = (
    (
        "Olá, {nome}! Meu nome é Gabriel e trabalho ajudando pequenos negócios "
        "a simplificar o atendimento e as rotinas com automações de IA. Queria "
        "entender se isso faz sentido para vocês e, se sim, agendar uma conversa "
        "breve com o responsável. Você é a pessoa certa para falar sobre isso?"
    ),
    (
        "Oi, {nome}! Aqui é o Gabriel. Eu ajudo pequenos negócios a simplificar "
        "o atendimento e as tarefas do dia a dia com automações de IA. Gostaria "
        "de entender se isso pode ajudar vocês e conversar rapidamente com o "
        "responsável. Você é a pessoa certa?"
    ),
    (
        "Olá, tudo bem. Meu nome é Gabriel e trabalho com automações de IA e "
        "consultoria para pequenos negócios. A ideia é simplificar o atendimento "
        "e reduzir tarefas repetitivas. Queria marcar uma conversa breve com o "
        "responsável da {nome}. Posso falar com você?"
    ),
    (
        "Bom dia, {nome}! Sou o Gabriel e ajudo pequenos negócios a organizar o "
        "atendimento e as rotinas usando automações de IA. Queria entender se "
        "existe algo que possamos simplificar e, se fizer sentido, agendar uma "
        "conversa curta com o responsável. É com você que devo falar?"
    ),
)

PRESETS_MENSAGEM: list[dict[str, str]] = [
    {
        "id": "gabriel_recomendada",
        "titulo": "Recomendada",
        "descricao": "Apresentação natural e pedido direto para falar com o responsável.",
        "texto": MODELOS_IA_PEQUENOS_NEGOCIOS[0],
    },
    {
        "id": "gabriel_proxima",
        "titulo": "Mais próxima",
        "descricao": "Tom leve, mantendo a mesma proposta e o mesmo objetivo.",
        "texto": MODELOS_IA_PEQUENOS_NEGOCIOS[1],
    },
    {
        "id": "gabriel_consultiva",
        "titulo": "Mais consultiva",
        "descricao": "Explica o benefício antes de pedir a conversa.",
        "texto": MODELOS_IA_PEQUENOS_NEGOCIOS[2],
    },
    {
        "id": "gabriel_profissional",
        "titulo": "Mais profissional",
        "descricao": "Objetiva e um pouco mais formal.",
        "texto": MODELOS_IA_PEQUENOS_NEGOCIOS[3],
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
    "titulo": "Como personalizar",
    "paragrafos": [
        "Escolha uma mensagem ou marque as mensagens variadas que deseja usar.",
        "Você pode editar cada texto; o sistema só preenche as lacunas com dados da planilha.",
        "Depois de editar, clique em Salvar mensagens. Sem salvar, o botão "
        "Iniciar continua bloqueado.",
    ],
    "lacunas": [
        ("{nome}", "Nome da loja (ex.: Bicho Mania)"),
        ("{categoria}", "Segmento no Maps (ex.: Pet shop)"),
        ("{endereco}", "Endereço capturado"),
        ("{busca}", "Termo que você buscou no scraper"),
    ],
    "dicas": [
        "Mantenha a apresentação verdadeira e ajuste o texto ao seu serviço.",
        "Não use {telefone} nem {link} — soa invasivo e o sistema bloqueia.",
        "Se a planilha não tiver categoria, o sistema coloca um termo neutro "
        "para a frase não ficar com buraco.",
    ],
}
