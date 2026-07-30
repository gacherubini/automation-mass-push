"""Tabelas do sistema.

O desenho segue a arvore de posse: um usuario tem conexoes (WhatsApps ligados
por QR) e campanhas; a campanha tem leads (as lojas da planilha) e mensagens.
Cada lead pode ter uma conversa, formada pelas interacoes recebidas, sugeridas
pela IA ou enviadas por uma pessoa.
Fora dessa arvore ficam duas listas GLOBAIS por usuario - OptOut e JaContatado -
que valem entre campanhas e entre planilhas, porque "nao mandar de novo" e uma
promessa do usuario para o mercado dele, nao um detalhe de uma campanha.

De onde `ritmo.Situacao` sai:

  idade_conexao_dias    Conexao.idade_dias (a partir de conectada_em)
  enviadas_hoje         COUNT(Mensagem) da conexao com enviada_em >= inicio do dia
  enviadas_ultima_hora  COUNT(Mensagem) da conexao com enviada_em >= agora - 1h
  total_entregues       COUNT(Mensagem) com status_entrega em ENTREGUES
  total_respostas       COUNT(Lead) com status = respondeu
  total_bloqueios       COUNT(Mensagem) com status_entrega = bloqueada

Os dois primeiros contadores sao por CONEXAO, nao por campanha: o teto diario e
o ban pertencem ao numero, e duas campanhas no mesmo numero somam. Como Mensagem
so guarda campanha_id, a consulta passa por Campanha - por isso existe indice em
Campanha(conexao_id, status).
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as EnumSQL,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app import ritmo

# Nomes deterministicos para indices e constraints. Sem isso o Postgres inventa
# o nome, e uma migracao futura que precise remover uma constraint nao tem como
# se referir a ela.
CONVENCAO_DE_NOMES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB no Postgres (indexavel, sem reparse a cada leitura); JSON generico em
# qualquer outro banco, para o SQLite dos testes continuar funcionando.
JSON_PORTATIL = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_DE_NOMES)


def _coluna_enum(tipo: type[StrEnum], nome: str) -> EnumSQL:
    """Enum guardado como texto + CHECK, nao como tipo nativo do Postgres.

    Tipo ENUM nativo exige ALTER TYPE para ganhar um valor novo, o que nao roda
    dentro de transacao em versoes antigas do Postgres e complica rollback de
    migracao. Texto com CHECK aceita o mesmo controle e a migracao vira um
    ALTER de constraint comum.

    `values_callable` faz gravar o VALOR do membro ("aguardando_qr") e nao o
    NOME ("AGUARDANDO_QR"), que e o default do SQLAlchemy e deixaria o banco
    ilegivel a olho nu.
    """
    return EnumSQL(
        tipo,
        name=nome,
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [membro.value for membro in e],
    )


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class StatusConexao(StrEnum):
    DESCONECTADA = "desconectada"
    AGUARDANDO_QR = "aguardando_qr"
    CONECTADA = "conectada"
    BANIDA = "banida"


class StatusCampanha(StrEnum):
    RASCUNHO = "rascunho"
    RODANDO = "rodando"
    PAUSADA = "pausada"
    CONCLUIDA = "concluida"


class StatusLead(StrEnum):
    PENDENTE = "pendente"
    CHECANDO = "checando"
    SEM_WHATSAPP = "sem_whatsapp"
    ENVIADO = "enviado"
    FALHOU = "falhou"
    RESPONDEU = "respondeu"
    OPTOUT = "optout"


class StatusEntrega(StrEnum):
    PENDENTE = "pendente"
    ENVIADA = "enviada"
    ENTREGUE = "entregue"
    LIDA = "lida"
    FALHOU = "falhou"
    # O destinatario bloqueou o remetente. E o sinal que alimenta o freio de
    # reputacao do ritmo, entao merece status proprio em vez de virar "falhou".
    BLOQUEADA = "bloqueada"


class ModoIA(StrEnum):
    """Quanto a IA pode fazer em uma campanha."""

    DESLIGADA = "desligada"
    RASCUNHO = "rascunho"
    AUTOMATICA = "automatica"


class StatusConversa(StrEnum):
    ABERTA = "aberta"
    AGUARDANDO_HUMANO = "aguardando_humano"
    ENCERRADA = "encerrada"
    ERRO = "erro"


class PapelContato(StrEnum):
    DESCONHECIDO = "desconhecido"
    ATENDENTE = "atendente"
    DECISOR = "decisor"


class EtapaConversa(StrEnum):
    IDENTIFICANDO = "identificando"
    BUSCANDO_DECISOR = "buscando_decisor"
    QUALIFICANDO = "qualificando"
    TRANSFERINDO = "transferindo"
    ENCERRADA = "encerrada"


class AutorInteracao(StrEnum):
    LEAD = "lead"
    IA = "ia"
    HUMANO = "humano"


class StatusInteracao(StrEnum):
    RECEBIDA = "recebida"
    RASCUNHO = "rascunho"
    ENVIADA = "enviada"
    FALHOU = "falhou"


# Chegou ao aparelho. E o denominador das taxas do ritmo: mensagem que nao
# chegou nao diz nada sobre a qualidade do texto.
ENTREGUES = (StatusEntrega.ENTREGUE, StatusEntrega.LIDA, StatusEntrega.BLOQUEADA)

# Leads que a fila ainda pode pegar.
ABERTOS = (StatusLead.PENDENTE, StatusLead.CHECANDO)


class Usuario(Base):
    """Quem loga no dashboard e conecta o proprio WhatsApp."""

    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Hash argon2 completo, no formato "$argon2id$v=19$m=...$...", que ja carrega
    # os parametros e o sal. 255 sobra com folga.
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    conexoes: Mapped[list[Conexao]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )
    campanhas: Mapped[list[Campanha]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Usuario {self.id} {self.email}>"


class Conexao(Base):
    """Um WhatsApp ligado por QR code, do lado da Evolution API.

    `conectada_em` e o marco do aquecimento: e dele que sai idade_conexao_dias,
    e por consequencia o teto do dia. Reconectar a MESMA instancia nao deve
    zerar esse campo - o numero nao rejuvenesce por ter caido a sessao.
    """

    __tablename__ = "conexao"
    __table_args__ = (
        # Nome da instancia e a chave da Evolution; repetir cria colisao de
        # sessao entre usuarios diferentes.
        UniqueConstraint("nome_instancia", name="uq_conexao_nome_instancia"),
        # Composto, e nao um indice so em usuario_id: o Postgres usa o prefixo
        # de um indice composto para a consulta que filtra so por usuario, entao
        # um segundo indice seria peso morto na escrita.
        Index("ix_conexao_usuario_status", "usuario_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    nome_instancia: Mapped[str] = mapped_column(String(100), nullable=False)
    # E.164 sem "+", igual ao que telefone.normalizar devolve. Nulo enquanto o
    # QR nao foi lido: so depois de conectar a Evolution informa o numero.
    numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[StatusConexao] = mapped_column(
        _coluna_enum(StatusConexao, "status_conexao"),
        nullable=False,
        default=StatusConexao.DESCONECTADA,
        server_default=StatusConexao.DESCONECTADA.value,
    )
    conectada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    usuario: Mapped[Usuario] = relationship(back_populates="conexoes")
    campanhas: Mapped[list[Campanha]] = relationship(back_populates="conexao")

    @property
    def idade_dias(self) -> int:
        """Dias desde a primeira conexao, para `ritmo.Situacao.idade_conexao_dias`.

        Sem `conectada_em` a conexao e tratada como recem-nascida (0), que e o
        degrau mais restritivo do aquecimento. Errar para o lado conservador
        custa mensagens; errar para o outro custa o numero.
        """
        if self.conectada_em is None:
            return 0
        referencia = self.conectada_em
        if referencia.tzinfo is None:
            referencia = referencia.replace(tzinfo=timezone.utc)
        return max(0, (_agora() - referencia).days)

    @property
    def pode_enviar(self) -> bool:
        return self.status == StatusConexao.CONECTADA

    def __repr__(self) -> str:
        return f"<Conexao {self.id} {self.nome_instancia} {self.status}>"


class Campanha(Base):
    """Uma planilha + um texto + um perfil de ritmo, disparando por uma conexao.

    Os campos de ritmo sao copias dos de `ritmo.Perfil`. Ficam achatados em
    colunas, e nao num JSON, porque o painel filtra e ordena por eles (quem tem
    teto alto demais, quem esta fora da janela) e porque coluna tipada pega erro
    de escrita que JSON engole.
    """

    __tablename__ = "campanha"
    __table_args__ = (
        # A contagem de enviadas do dia e da hora e por conexao e passa por
        # aqui. Sem este indice, cada decisao de envio varre a tabela.
        Index("ix_campanha_conexao_status", "conexao_id", "status"),
        # Serve tambem a listagem "minhas campanhas" pelo prefixo usuario_id.
        Index("ix_campanha_usuario_status", "usuario_id", "status"),
        CheckConstraint("intervalo_min_seg > 0", name="intervalo_min_positivo"),
        CheckConstraint("intervalo_max_seg >= intervalo_min_seg", name="intervalo_coerente"),
        CheckConstraint("teto_diario > 0", name="teto_positivo"),
        CheckConstraint(
            "limite_respostas_ia > 0 AND limite_respostas_ia <= 20",
            name="limite_respostas_ia_valido",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, e nao CASCADE: apagar uma conexao nao pode apagar o historico das
    # campanhas que sairam por ela. A campanha sobrevive orfa, sem poder
    # disparar, e o painel mostra "conexao removida".
    conexao_id: Mapped[int | None] = mapped_column(
        ForeignKey("conexao.id", ondelete="SET NULL"), nullable=True
    )
    nome: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[StatusCampanha] = mapped_column(
        _coluna_enum(StatusCampanha, "status_campanha"),
        nullable=False,
        default=StatusCampanha.RASCUNHO,
        server_default=StatusCampanha.RASCUNHO.value,
    )

    # Variacoes do texto. Mandar a mesma mensagem para mais de ~15 numeros por
    # hora e um dos sinais de spam mais faceis de detectar, entao a campanha
    # guarda varias e o envio sorteia. O texto REAL sorteado fica em Mensagem.
    modelos: Mapped[list[str]] = mapped_column(JSON_PORTATIL, nullable=False, default=list)

    # A IA so entra depois que o lead responde. RASCUNHO e o modo de validacao:
    # gera a sugestao, mas uma pessoa precisa aprovar na inbox.
    modo_ia: Mapped[ModoIA] = mapped_column(
        _coluna_enum(ModoIA, "modo_ia"),
        nullable=False,
        default=ModoIA.DESLIGADA,
        server_default=ModoIA.DESLIGADA.value,
    )
    prompt_ia: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    case_ia: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    link_agendamento: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    limite_respostas_ia: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )

    # --- perfil de ritmo (espelha ritmo.Perfil) ---
    teto_diario: Mapped[int] = mapped_column(Integer, nullable=False, default=40, server_default="40")
    intervalo_min_seg: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120"
    )
    intervalo_max_seg: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    hora_inicio: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(9, 0), server_default="09:00:00"
    )
    hora_fim: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(18, 0), server_default="18:00:00"
    )
    dias_uteis_apenas: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    respeitar_aquecimento: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Preenchido pelo freio automatico com o texto de `ritmo.Decisao.motivo`. E
    # o unico lugar onde o usuario descobre POR QUE a campanha parou sozinha, e
    # e Text porque a mensagem e uma frase inteira, nao um codigo.
    motivo_pausa: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    usuario: Mapped[Usuario] = relationship(back_populates="campanhas")
    conexao: Mapped[Conexao | None] = relationship(back_populates="campanhas")
    leads: Mapped[list[Lead]] = relationship(
        back_populates="campanha", cascade="all, delete-orphan", passive_deletes=True
    )
    mensagens: Mapped[list[Mensagem]] = relationship(
        back_populates="campanha", cascade="all, delete-orphan", passive_deletes=True
    )

    def perfil(self) -> ritmo.Perfil:
        """Monta o `ritmo.Perfil` desta campanha.

        A politica de ritmo continua sendo funcao pura: quem conversa com o
        banco e este metodo, nao o modulo `ritmo`.
        """
        return ritmo.Perfil(
            teto_diario=self.teto_diario,
            intervalo_min_seg=self.intervalo_min_seg,
            intervalo_max_seg=self.intervalo_max_seg,
            hora_inicio=self.hora_inicio,
            hora_fim=self.hora_fim,
            dias_uteis_apenas=self.dias_uteis_apenas,
            respeitar_aquecimento=self.respeitar_aquecimento,
        )

    def aplicar_perfil(self, perfil: ritmo.Perfil) -> None:
        """Grava de volta o perfil escolhido na tela."""
        self.teto_diario = perfil.teto_diario
        self.intervalo_min_seg = perfil.intervalo_min_seg
        self.intervalo_max_seg = perfil.intervalo_max_seg
        self.hora_inicio = perfil.hora_inicio
        self.hora_fim = perfil.hora_fim
        self.dias_uteis_apenas = perfil.dias_uteis_apenas
        self.respeitar_aquecimento = perfil.respeitar_aquecimento

    def __repr__(self) -> str:
        return f"<Campanha {self.id} {self.nome!r} {self.status}>"


class Lead(Base):
    """Uma loja vinda da planilha do scraper do Google Maps."""

    __tablename__ = "lead"
    __table_args__ = (
        # A consulta mais quente do sistema: "proximo lead pendente desta
        # campanha". Roda a cada disparo, ou seja, o dia inteiro.
        Index("ix_lead_campanha_status", "campanha_id", "status"),
        # A mesma loja aparece duas vezes numa planilha com frequencia (duas
        # buscas que se sobrepoem). Barrar no banco e mais confiavel que
        # confiar na deduplicacao do importador.
        UniqueConstraint("campanha_id", "telefone", name="uq_lead_campanha_telefone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campanha_id: Mapped[int] = mapped_column(
        ForeignKey("campanha.id", ondelete="CASCADE"), nullable=False
    )
    nome: Mapped[str] = mapped_column(String(255), nullable=False)
    # E.164 sem "+", como `telefone.normalizar` devolve. Nulo quando a planilha
    # trouxe algo que nao vira telefone: NULL nao colide na unique acima, entao
    # varias linhas sem telefone convivem em vez de derrubar a importacao.
    telefone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Como estava escrito na planilha, "(51) 99898-4086". Existe para a tela e
    # para o usuario reconhecer a loja; nunca para discar.
    telefone_exibicao: Mapped[str | None] = mapped_column(String(40), nullable=True)
    endereco: Mapped[str | None] = mapped_column(Text, nullable=True)
    categoria: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # O termo buscado no Maps que trouxe esta loja. Serve para o usuario
    # descobrir qual busca rende resposta e qual so gasta cota.
    busca: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_maps: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[StatusLead] = mapped_column(
        _coluna_enum(StatusLead, "status_lead"),
        nullable=False,
        default=StatusLead.PENDENTE,
        server_default=StatusLead.PENDENTE.value,
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    campanha: Mapped[Campanha] = relationship(back_populates="leads")
    mensagens: Mapped[list[Mensagem]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", passive_deletes=True
    )
    conversa: Mapped[Conversa | None] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Lead {self.id} {self.nome!r} {self.status}>"


class Mensagem(Base):
    """Um envio. Uma linha por tentativa, inclusive as que falharam.

    Guarda o texto REAL que saiu, ja sorteado entre os modelos da campanha, e
    nao o indice do modelo: os modelos podem ser editados depois, e o registro
    do que o destinatario recebeu nao pode mudar junto.
    """

    __tablename__ = "mensagem"
    __table_args__ = (
        # Contagem de janela ("quantas hoje", "quantas na ultima hora"), que
        # roda antes de cada disparo.
        Index("ix_mensagem_campanha_enviada_em", "campanha_id", "enviada_em"),
        # O webhook da Evolution chega dizendo "a mensagem X foi entregue"; a
        # unica pista e o id externo.
        Index("ix_mensagem_id_externo", "id_externo"),
        Index("ix_mensagem_campanha_status", "campanha_id", "status_entrega"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campanha_id: Mapped[int] = mapped_column(
        ForeignKey("campanha.id", ondelete="CASCADE"), nullable=False
    )
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot da variacao da primeira mensagem. O indice ajuda a interface; o
    # texto-base mantem a metrica correta mesmo se os modelos forem editados.
    variante_indice: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variante_texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    enviada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )
    status_entrega: Mapped[StatusEntrega] = mapped_column(
        _coluna_enum(StatusEntrega, "status_entrega"),
        nullable=False,
        default=StatusEntrega.PENDENTE,
        server_default=StatusEntrega.PENDENTE.value,
    )
    # Id que a Evolution devolve no aceite do envio. Nulo quando a chamada nem
    # chegou a ser aceita.
    id_externo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Mensagem de erro crua da Evolution, para o usuario entender a falha sem
    # abrir log de servidor.
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="mensagens")
    campanha: Mapped[Campanha] = relationship(back_populates="mensagens")

    def __repr__(self) -> str:
        return f"<Mensagem {self.id} lead={self.lead_id} {self.status_entrega}>"


class Conversa(Base):
    """Estado conversacional de um lead depois da primeira resposta."""

    __tablename__ = "conversa"
    __table_args__ = (
        UniqueConstraint("lead_id", name="uq_conversa_lead_id"),
        Index("ix_conversa_status_atualizada", "status", "atualizada_em"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[StatusConversa] = mapped_column(
        _coluna_enum(StatusConversa, "status_conversa"),
        nullable=False,
        default=StatusConversa.ABERTA,
        server_default=StatusConversa.ABERTA.value,
    )
    papel_contato: Mapped[PapelContato] = mapped_column(
        _coluna_enum(PapelContato, "papel_contato"),
        nullable=False,
        default=PapelContato.DESCONHECIDO,
        server_default=PapelContato.DESCONHECIDO.value,
    )
    etapa: Mapped[EtapaConversa] = mapped_column(
        _coluna_enum(EtapaConversa, "etapa_conversa"),
        nullable=False,
        default=EtapaConversa.IDENTIFICANDO,
        server_default=EtapaConversa.IDENTIFICANDO.value,
    )
    resumo: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_respostas_ia: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )
    atualizada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="conversa")
    interacoes: Mapped[list[Interacao]] = relationship(
        back_populates="conversa",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Interacao.id",
    )


class Interacao(Base):
    """Uma fala do lead, uma sugestao da IA ou uma resposta humana."""

    __tablename__ = "interacao"
    __table_args__ = (
        UniqueConstraint("id_externo", name="uq_interacao_id_externo"),
        UniqueConstraint(
            "origem_interacao_id", name="uq_interacao_origem_interacao_id"
        ),
        Index("ix_interacao_conversa_criada", "conversa_id", "criada_em"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversa_id: Mapped[int] = mapped_column(
        ForeignKey("conversa.id", ondelete="CASCADE"), nullable=False
    )
    # Liga a sugestao/resposta automatica a mensagem recebida que a originou.
    # A unique impede dois workers de responderem o mesmo webhook.
    origem_interacao_id: Mapped[int | None] = mapped_column(
        ForeignKey("interacao.id", ondelete="SET NULL"), nullable=True
    )
    autor: Mapped[AutorInteracao] = mapped_column(
        _coluna_enum(AutorInteracao, "autor_interacao"), nullable=False
    )
    status: Mapped[StatusInteracao] = mapped_column(
        _coluna_enum(StatusInteracao, "status_interacao"), nullable=False
    )
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    id_externo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    conversa: Mapped[Conversa] = relationship(back_populates="interacoes")


class OptOut(Base):
    """Quem pediu para parar. Vale para o usuario inteiro, para sempre.

    Nao tem campanha_id de proposito: opt-out que valesse so numa campanha nao
    seria opt-out. A checagem antes de cada envio e por (usuario_id, telefone).
    """

    __tablename__ = "optout"
    __table_args__ = (
        # A unique ja cria o indice que a checagem usa - um indice separado em
        # (usuario_id, telefone) seria duplicata pura, ocupando escrita a toa.
        UniqueConstraint("usuario_id", "telefone", name="uq_optout_usuario_telefone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    telefone: Mapped[str] = mapped_column(String(20), nullable=False)
    # O que a pessoa escreveu, ou "pedido manual". Fica guardado porque um dia
    # alguem vai perguntar por que aquele numero parou de receber.
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<OptOut {self.usuario_id} {self.telefone}>"


class JaContatado(Base):
    """Memoria global de quem ja foi abordado, entre campanhas e planilhas.

    `campanha_id` e SET NULL, e nao CASCADE, e essa e a decisao mais importante
    desta tabela: se apagar a campanha apagasse o registro, o sistema esqueceria
    quem ja abordou e mandaria a mesma primeira mensagem de novo para a mesma
    loja. A origem e informacao util, mas a memoria e que nao pode sumir.
    """

    __tablename__ = "ja_contatado"
    __table_args__ = (
        # Idem OptOut: a unique e o indice da checagem pre-envio.
        UniqueConstraint("usuario_id", "telefone", name="uq_ja_contatado_usuario_telefone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False
    )
    telefone: Mapped[str] = mapped_column(String(20), nullable=False)
    campanha_id: Mapped[int | None] = mapped_column(
        ForeignKey("campanha.id", ondelete="SET NULL"), nullable=True, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_agora, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<JaContatado {self.usuario_id} {self.telefone}>"


__all__ = [
    "ABERTOS",
    "ENTREGUES",
    "AutorInteracao",
    "Base",
    "Campanha",
    "Conexao",
    "Conversa",
    "EtapaConversa",
    "Interacao",
    "JaContatado",
    "Lead",
    "Mensagem",
    "ModoIA",
    "OptOut",
    "PapelContato",
    "StatusCampanha",
    "StatusConexao",
    "StatusConversa",
    "StatusEntrega",
    "StatusInteracao",
    "StatusLead",
    "Usuario",
]
