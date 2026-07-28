"""Dashboard FastAPI: login, conexao WhatsApp, campanhas, disparo e webhook.

A tela de conexao e o motor falam com a Evolution de verdade; se ela nao
estiver no ar, o erro aparece em portugues em vez de estourar 500.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app import disparo as mod_disparo
from app import mensagem as mod_mensagem
from app import planilha as mod_planilha
from app.auth import (
    autenticar,
    csrf_token,
    csrf_valido,
    encerrar_sessao,
    hash_senha,
    iniciar_sessao,
    normalizar_email,
    usuario_atual,
)
from app.config import configuracao
from app.db import fabrica_de_sessao, get_sessao
from app.disparo import GerenciadorDisparo, retomar_campanhas_rodando
from app.templates_presets import (
    EXPLICACAO_MENSAGEM,
    EXPLICACAO_RITMO,
    PRESETS_MENSAGEM,
    PRESETS_RITMO,
)
from app.evolution import (
    CONECTADA,
    Evolution,
    ErroEvolution,
    InstanciaNaoEncontrada,
    JaConectado,
    RespostaInesperada,
    interpretar_webhook,
)
from app.models import (
    Campanha,
    Conexao,
    Lead,
    OptOut,
    StatusCampanha,
    StatusConexao,
    StatusEntrega,
    StatusLead,
    Usuario,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)

# Nome da instancia Evolution: so [a-zA-Z0-9_-], ate 100 chars (models).
_RE_INSTANCIA = re.compile(r"[^a-zA-Z0-9_-]+")


def criar_app(
    *,
    fabrica_evolution: Callable[[], Evolution] | None = None,
    gerenciador_disparo: GerenciadorDisparo | None = None,
    retomar_no_startup: bool = True,
) -> FastAPI:
    """Monta a aplicacao. Fabricas injetaveis existem para o teste."""
    cfg = configuracao()

    def _evolution() -> Evolution:
        if fabrica_evolution is not None:
            return fabrica_evolution()
        atual = configuracao()
        return Evolution(atual.evolution_url, atual.evolution_api_key)

    if gerenciador_disparo is None:
        gerenciador_disparo = GerenciadorDisparo(
            fabrica_sessao=lambda: fabrica_de_sessao()(),
            fabrica_evolution=_evolution,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Thread do worker some no restart do uvicorn; campanhas RODANDO no
        # banco precisam de alguem mandando de novo.
        if retomar_no_startup:
            try:
                retomar_campanhas_rodando(
                    app.state.disparo,  # type: ignore[attr-defined]
                    lambda: fabrica_de_sessao()(),
                )
            except Exception:
                logger.exception("falha ao retomar campanhas no startup")
        yield

    app = FastAPI(
        title="Prospeccao WhatsApp",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # https_only=False: roda local em http. SameSite=lax cobre o CSRF basico
    # junto com o token do form.
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie="mass_push_sessao",
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 24 * 14,
    )

    static_dir = BASE_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.state.fabrica_evolution = _evolution  # type: ignore[attr-defined]
    app.state.disparo = gerenciador_disparo  # type: ignore[attr-defined]

    # -- helpers de request -------------------------------------------------

    def _usuario(
        request: Request, sessao: Session = Depends(get_sessao)
    ) -> Usuario | None:
        return usuario_atual(request, sessao)

    def _exigir_usuario(
        request: Request, sessao: Session = Depends(get_sessao)
    ) -> Usuario:
        usuario = usuario_atual(request, sessao)
        if usuario is None:
            # Nao e HTTPException: queremos redirect, nao JSON 401.
            raise _PrecisaLogin()
        return usuario

    def _ctx(
        request: Request,
        usuario: Usuario | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        flash = request.session.pop("flash", None)
        return {
            "request": request,
            "usuario": usuario,
            "csrf": csrf_token(request),
            "flash": flash,
            **extra,
        }

    def _flash(request: Request, tipo: str, texto: str) -> None:
        request.session["flash"] = {"tipo": tipo, "texto": texto}

    def _csrf_ou_volta(request: Request, csrf: str, volta: str) -> Response | None:
        if csrf_valido(request, csrf):
            return None
        _flash(request, "erro", "Sessao expirada. Tente de novo.")
        return RedirectResponse(volta, status_code=303)

    # -- rotas publicas -----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def raiz(
        request: Request,
        usuario: Usuario | None = Depends(_usuario),
    ) -> Response:
        if usuario:
            return RedirectResponse("/app", status_code=303)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario | None = Depends(_usuario),
    ) -> Response:
        if usuario:
            return RedirectResponse("/app", status_code=303)
        total = sessao.scalar(select(func.count()).select_from(Usuario)) or 0
        return templates.TemplateResponse(
            request,
            "login.html",
            _ctx(request, precisa_bootstrap=total == 0),
        )

    @app.post("/login")
    def login(
        request: Request,
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
    ) -> Response:
        if not csrf_valido(request, csrf):
            return templates.TemplateResponse(
                request,
                "login.html",
                _ctx(request, erro="Sessao expirada. Tente de novo."),
                status_code=400,
            )
        usuario = autenticar(sessao, email, senha)
        if usuario is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                _ctx(request, erro="E-mail ou senha incorretos."),
                status_code=401,
            )
        iniciar_sessao(request, usuario)
        return RedirectResponse("/app", status_code=303)

    @app.post("/logout")
    def logout(
        request: Request,
        csrf: Annotated[str, Form()] = "",
    ) -> Response:
        if csrf_valido(request, csrf):
            encerrar_sessao(request)
        return RedirectResponse("/login", status_code=303)

    @app.get("/bootstrap", response_class=HTMLResponse)
    def bootstrap_form(
        request: Request,
        sessao: Session = Depends(get_sessao),
    ) -> Response:
        total = sessao.scalar(select(func.count()).select_from(Usuario)) or 0
        if total > 0:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request, "bootstrap.html", _ctx(request)
        )

    @app.post("/bootstrap")
    def bootstrap(
        request: Request,
        nome: Annotated[str, Form()],
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
    ) -> Response:
        total = sessao.scalar(select(func.count()).select_from(Usuario)) or 0
        if total > 0:
            return RedirectResponse("/login", status_code=303)
        if not csrf_valido(request, csrf):
            return templates.TemplateResponse(
                request,
                "bootstrap.html",
                _ctx(request, erro="Sessao expirada. Tente de novo."),
                status_code=400,
            )
        nome = nome.strip()
        email_n = normalizar_email(email)
        if not nome or not email_n or len(senha) < 8:
            return templates.TemplateResponse(
                request,
                "bootstrap.html",
                _ctx(
                    request,
                    erro="Nome, e-mail e senha com pelo menos 8 caracteres.",
                    nome=nome,
                    email=email_n,
                ),
                status_code=400,
            )
        usuario = Usuario(
            nome=nome,
            email=email_n,
            senha_hash=hash_senha(senha),
        )
        sessao.add(usuario)
        sessao.commit()
        iniciar_sessao(request, usuario)
        _flash(request, "ok", "Conta criada. Conecte o WhatsApp para comecar.")
        return RedirectResponse("/app", status_code=303)

    # -- area logada --------------------------------------------------------

    @app.get("/app", response_class=HTMLResponse)
    def home(
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        campanhas = sessao.scalars(
            select(Campanha)
            .where(Campanha.usuario_id == usuario.id)
            .order_by(Campanha.id.desc())
        ).all()
        conexoes = sessao.scalars(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
        ).all()
        return templates.TemplateResponse(
            request,
            "home.html",
            _ctx(request, usuario, campanhas=campanhas, conexoes=conexoes),
        )

    # -- conexao WhatsApp ---------------------------------------------------

    @app.get("/conexao", response_class=HTMLResponse)
    def conexao_tela(
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        conexao = sessao.scalar(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
            .limit(1)
        )
        qr_data_uri = ""
        erro_evolution = ""
        estado_api = None

        if conexao is not None:
            try:
                with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                    estado_api = evo.estado_conexao(conexao.nome_instancia)
                    if estado_api.estado == CONECTADA:
                        _sincronizar_conexao(sessao, conexao, estado_api)
                    elif conexao.status != StatusConexao.CONECTADA:
                        try:
                            # Poucas tentativas na tela: o botao "Gerar QR" ja
                            # fez o trabalho pesado; aqui so atualiza a imagem.
                            qr = evo.obter_qrcode(
                                conexao.nome_instancia,
                                tentativas=3,
                                espera_entre=1.5,
                            )
                            qr_data_uri = qr.imagem
                            conexao.status = StatusConexao.AGUARDANDO_QR
                            sessao.commit()
                        except JaConectado:
                            estado_api = evo.estado_conexao(conexao.nome_instancia)
                            _sincronizar_conexao(sessao, conexao, estado_api)
            except ErroEvolution as erro:
                erro_evolution = erro.mensagem
                logger.info("evolution na tela de conexao: %s", erro)

        return templates.TemplateResponse(
            request,
            "conexao.html",
            _ctx(
                request,
                usuario,
                conexao=conexao,
                qr_data_uri=qr_data_uri,
                erro_evolution=erro_evolution,
                estado_api=estado_api,
            ),
        )

    @app.get("/conexao/status")
    def conexao_status(
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        """JSON para o poll da tela de QR (detecta pareamento sem F5)."""
        conexao = sessao.scalar(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
            .limit(1)
        )
        if conexao is None:
            return JSONResponse({"existe": False})

        estado = conexao.status.value
        numero = conexao.numero or ""
        try:
            with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                api = evo.estado_conexao(conexao.nome_instancia)
                if api.estado == CONECTADA:
                    _sincronizar_conexao(sessao, conexao, api)
                    estado = StatusConexao.CONECTADA.value
                    numero = conexao.numero or api.numero or ""
                else:
                    estado = api.estado
        except ErroEvolution as erro:
            return JSONResponse(
                {
                    "existe": True,
                    "status": estado,
                    "numero": numero,
                    "erro": erro.mensagem,
                }
            )

        return JSONResponse(
            {
                "existe": True,
                "status": estado,
                "numero": numero,
                "conectada": estado == StatusConexao.CONECTADA.value,
                "instancia": conexao.nome_instancia,
            }
        )

    @app.post("/conexao/criar")
    def conexao_criar(
        request: Request,
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        bloqueio = _csrf_ou_volta(request, csrf, "/conexao")
        if bloqueio:
            return bloqueio

        existente = sessao.scalar(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
            .limit(1)
        )
        if existente and existente.status == StatusConexao.CONECTADA:
            _flash(request, "erro", "Ja existe um WhatsApp conectado.")
            return RedirectResponse("/conexao", status_code=303)

        try:
            with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                if existente is None:
                    nome = _nome_instancia(usuario)
                    inst = evo.criar_instancia(nome)
                    conexao = Conexao(
                        usuario_id=usuario.id,
                        nome_instancia=nome,
                        status=StatusConexao.AGUARDANDO_QR,
                    )
                    sessao.add(conexao)
                    # Se o create nao trouxe QR, puxa com retry (Baileys demora).
                    if inst.qrcode is None or inst.qrcode.vazio:
                        evo.obter_qrcode(nome)
                else:
                    # Reusa a MESMA instancia. QR vazio nao justifica criar outra
                    # (isso gerava dezenas de orfas e o Baileys entrava em loop).
                    try:
                        evo.obter_qrcode(existente.nome_instancia)
                        existente.status = StatusConexao.AGUARDANDO_QR
                    except InstanciaNaoEncontrada:
                        nome = _nome_instancia(usuario)
                        evo.criar_instancia(nome)
                        existente.nome_instancia = nome
                        existente.status = StatusConexao.AGUARDANDO_QR
                        existente.numero = None
                        existente.conectada_em = None
                        evo.obter_qrcode(nome)
                    except RespostaInesperada:
                        # Instancia existe mas o QR nao veio: nao cria orfa.
                        existente.status = StatusConexao.AGUARDANDO_QR
                        sessao.commit()
                        _flash(
                            request,
                            "erro",
                            "A Evolution esta no ar, mas ainda nao gerou o QR. "
                            "Espere uns segundos e clique em Atualizar status. "
                            "Se persistir, reinicie o container da Evolution "
                            "(docker compose restart evolution-api).",
                        )
                        return RedirectResponse("/conexao", status_code=303)
                sessao.commit()
        except ErroEvolution as erro:
            sessao.rollback()
            _flash(request, "erro", erro.mensagem)
            return RedirectResponse("/conexao", status_code=303)

        _flash(
            request,
            "ok",
            "QR code gerado. Escaneie com o celular — o risco de banimento e do numero que escanear.",
        )
        return RedirectResponse("/conexao", status_code=303)

    @app.post("/conexao/desconectar")
    def conexao_desconectar(
        request: Request,
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        bloqueio = _csrf_ou_volta(request, csrf, "/conexao")
        if bloqueio:
            return bloqueio

        conexao = sessao.scalar(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
            .limit(1)
        )
        if conexao is None:
            return RedirectResponse("/conexao", status_code=303)

        try:
            with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                evo.desconectar(conexao.nome_instancia)
        except ErroEvolution as erro:
            _flash(request, "erro", erro.mensagem)
            return RedirectResponse("/conexao", status_code=303)

        conexao.status = StatusConexao.DESCONECTADA
        # conectada_em NAO zera: reconectar a mesma instancia nao rejuvenesce
        # o aquecimento (ver docstring de Conexao).
        sessao.commit()
        _flash(request, "ok", "WhatsApp desconectado.")
        return RedirectResponse("/conexao", status_code=303)

    @app.post("/conexao/recriar")
    def conexao_recriar(
        request: Request,
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        """Apaga a instancia na Evolution e no banco e cria uma nova limpa.

        Use quando o QR nao parea (celular recusa, count trava, Docker reiniciou
        no meio). Nao zera conectada_em de campanhas antigas — so a conexao.
        """
        bloqueio = _csrf_ou_volta(request, csrf, "/conexao")
        if bloqueio:
            return bloqueio

        conexao = sessao.scalar(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
            .limit(1)
        )
        try:
            with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                if conexao is not None:
                    try:
                        evo.remover_instancia(conexao.nome_instancia)
                    except ErroEvolution:
                        pass
                    sessao.delete(conexao)
                    sessao.commit()

                nome = _nome_instancia(usuario)
                inst = evo.criar_instancia(nome)
                nova = Conexao(
                    usuario_id=usuario.id,
                    nome_instancia=nome,
                    status=StatusConexao.AGUARDANDO_QR,
                )
                sessao.add(nova)
                sessao.commit()
                if inst.qrcode is None or inst.qrcode.vazio:
                    evo.obter_qrcode(nome, tentativas=4, espera_entre=1.5)
        except ErroEvolution as erro:
            sessao.rollback()
            _flash(request, "erro", erro.mensagem)
            return RedirectResponse("/conexao", status_code=303)

        _flash(
            request,
            "ok",
            "Conexao recriada do zero. Escaneie o QR novo agora (ele expira em ~40s).",
        )
        return RedirectResponse("/conexao", status_code=303)

    # -- campanhas ----------------------------------------------------------

    @app.get("/campanhas/nova", response_class=HTMLResponse)
    def campanha_nova_form(
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        conexoes = sessao.scalars(
            select(Conexao)
            .where(
                Conexao.usuario_id == usuario.id,
                Conexao.status == StatusConexao.CONECTADA,
            )
            .order_by(Conexao.id.desc())
        ).all()
        return templates.TemplateResponse(
            request,
            "campanhas/nova.html",
            _ctx(request, usuario, conexoes=conexoes),
        )

    @app.post("/campanhas/nova")
    async def campanha_nova(
        request: Request,
        nome: Annotated[str, Form()],
        conexao_id: Annotated[str, Form()] = "",
        csrf: Annotated[str, Form()] = "",
        arquivo: UploadFile = File(...),
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        bloqueio = _csrf_ou_volta(request, csrf, "/campanhas/nova")
        if bloqueio:
            return bloqueio

        nome = nome.strip() or "Campanha sem nome"
        conteudo = await arquivo.read()
        if not conteudo:
            _flash(request, "erro", "Envie um arquivo .xlsx.")
            return RedirectResponse("/campanhas/nova", status_code=303)

        try:
            relatorio = mod_planilha.ler(io.BytesIO(conteudo))
        except mod_planilha.PlanilhaInvalida as erro:
            _flash(request, "erro", str(erro))
            return RedirectResponse("/campanhas/nova", status_code=303)

        conexao_fk: int | None = None
        if conexao_id.strip().isdigit():
            cid = int(conexao_id)
            dona = sessao.scalar(
                select(Conexao).where(
                    Conexao.id == cid, Conexao.usuario_id == usuario.id
                )
            )
            if dona is not None:
                conexao_fk = dona.id

        campanha = Campanha(
            usuario_id=usuario.id,
            conexao_id=conexao_fk,
            nome=nome,
            modelos=[],
            status=StatusCampanha.RASCUNHO,
        )
        sessao.add(campanha)
        sessao.flush()

        for lead in relatorio.leads:
            sessao.add(
                Lead(
                    campanha_id=campanha.id,
                    nome=lead.nome,
                    telefone=lead.telefone,
                    telefone_exibicao=lead.telefone_exibicao,
                    endereco=lead.endereco or None,
                    categoria=lead.categoria or None,
                    busca=lead.busca or None,
                    link_maps=lead.link_maps or None,
                    status=StatusLead.PENDENTE,
                )
            )
        sessao.commit()

        _flash(request, "ok", relatorio.resumo())
        return RedirectResponse(f"/campanhas/{campanha.id}", status_code=303)

    @app.get("/campanhas/{campanha_id}", response_class=HTMLResponse)
    def campanha_detalhe(
        campanha_id: int,
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            _flash(request, "erro", "Campanha nao encontrada.")
            return RedirectResponse("/app", status_code=303)

        total_leads = (
            sessao.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.campanha_id == campanha.id)
            )
            or 0
        )
        por_status = dict(
            sessao.execute(
                select(Lead.status, func.count())
                .where(Lead.campanha_id == campanha.id)
                .group_by(Lead.status)
            ).all()
        )
        amostra = sessao.scalars(
            select(Lead)
            .where(Lead.campanha_id == campanha.id)
            .order_by(Lead.id)
            .limit(5)
        ).all()

        modelos = list(campanha.modelos or [])
        previas: list[mod_mensagem.MensagemPronta] = []
        aviso_modelo = ""
        aviso_diversidade = ""
        if modelos:
            try:
                mod_mensagem.validar(modelos)
                previas = mod_mensagem.previa(modelos, amostra)
                div = mod_mensagem.diversidade(modelos, total_leads)
                aviso_diversidade = div.aviso()
            except mod_mensagem.ModeloInvalido as erro:
                aviso_modelo = str(erro)

        avisos_ritmo = campanha.perfil().avisos()
        prog = mod_disparo.progresso(sessao, campanha)
        pode, motivo_inicio = mod_disparo.pode_iniciar(sessao, campanha)
        # Se ja esta rodando, a tela mostra pausar — nao o botao iniciar.
        if campanha.status == StatusCampanha.RODANDO:
            pode, motivo_inicio = False, ""
        conexoes = sessao.scalars(
            select(Conexao)
            .where(Conexao.usuario_id == usuario.id)
            .order_by(Conexao.id.desc())
        ).all()
        worker_ativo = app.state.disparo.esta_rodando(campanha.id)  # type: ignore[attr-defined]

        return templates.TemplateResponse(
            request,
            "campanhas/detalhe.html",
            _ctx(
                request,
                usuario,
                campanha=campanha,
                total_leads=total_leads,
                por_status=por_status,
                modelos_texto="\n---\n".join(modelos),
                previas=previas,
                aviso_modelo=aviso_modelo,
                aviso_diversidade=aviso_diversidade,
                avisos_ritmo=avisos_ritmo,
                progresso=prog,
                pode_iniciar=pode,
                motivo_inicio=motivo_inicio,
                conexoes=conexoes,
                worker_ativo=worker_ativo,
                presets_mensagem=PRESETS_MENSAGEM,
                presets_ritmo=PRESETS_RITMO,
                expl_msg=EXPLICACAO_MENSAGEM,
                expl_ritmo=EXPLICACAO_RITMO,
            ),
        )

    @app.post("/campanhas/{campanha_id}/modelos")
    def campanha_salvar_modelos(
        campanha_id: int,
        request: Request,
        modelos_texto: Annotated[str, Form()],
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        volta = f"/campanhas/{campanha_id}"
        bloqueio = _csrf_ou_volta(request, csrf, volta)
        if bloqueio:
            return bloqueio

        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)

        # Variacoes separadas por linha com "---" sozinha, ou por linha em
        # branco dupla. Uma linha por modelo tambem vale se so houver um.
        modelos = _partir_modelos(modelos_texto)
        try:
            if modelos:
                mod_mensagem.validar(modelos)
        except mod_mensagem.ModeloInvalido as erro:
            _flash(request, "erro", str(erro))
            return RedirectResponse(volta, status_code=303)

        campanha.modelos = modelos
        sessao.commit()
        _flash(request, "ok", f"{len(modelos)} variacao(oes) salva(s).")
        return RedirectResponse(volta, status_code=303)

    @app.post("/campanhas/{campanha_id}/ritmo")
    def campanha_salvar_ritmo(
        campanha_id: int,
        request: Request,
        teto_diario: Annotated[int, Form()],
        intervalo_min_seg: Annotated[int, Form()],
        intervalo_max_seg: Annotated[int, Form()],
        hora_inicio: Annotated[str, Form()] = "09:00",
        hora_fim: Annotated[str, Form()] = "18:00",
        dias_uteis_apenas: Annotated[str, Form()] = "",
        respeitar_aquecimento: Annotated[str, Form()] = "",
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        from datetime import time as time_cls

        from app import ritmo

        volta = f"/campanhas/{campanha_id}"
        bloqueio = _csrf_ou_volta(request, csrf, volta)
        if bloqueio:
            return bloqueio

        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)

        def _hora(texto: str, padrao: time_cls) -> time_cls:
            try:
                h, m = texto.strip().split(":")[:2]
                return time_cls(int(h), int(m))
            except (ValueError, TypeError):
                return padrao

        perfil = ritmo.Perfil(
            teto_diario=max(1, teto_diario),
            intervalo_min_seg=max(1, intervalo_min_seg),
            intervalo_max_seg=max(intervalo_min_seg, intervalo_max_seg),
            hora_inicio=_hora(hora_inicio, time_cls(9, 0)),
            hora_fim=_hora(hora_fim, time_cls(18, 0)),
            dias_uteis_apenas=bool(dias_uteis_apenas),
            respeitar_aquecimento=bool(respeitar_aquecimento),
        )
        campanha.aplicar_perfil(perfil)
        sessao.commit()

        avisos = perfil.avisos()
        if avisos:
            _flash(request, "aviso", " ".join(avisos))
        else:
            _flash(request, "ok", "Ritmo atualizado.")
        return RedirectResponse(volta, status_code=303)

    @app.post("/campanhas/{campanha_id}/conexao")
    def campanha_vincular_conexao(
        campanha_id: int,
        request: Request,
        conexao_id: Annotated[str, Form()] = "",
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        volta = f"/campanhas/{campanha_id}"
        bloqueio = _csrf_ou_volta(request, csrf, volta)
        if bloqueio:
            return bloqueio
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)
        if not conexao_id.strip().isdigit():
            _flash(request, "erro", "Escolha uma conexao.")
            return RedirectResponse(volta, status_code=303)
        cid = int(conexao_id)
        conexao = sessao.scalar(
            select(Conexao).where(
                Conexao.id == cid, Conexao.usuario_id == usuario.id
            )
        )
        if conexao is None:
            _flash(request, "erro", "Conexao nao encontrada.")
            return RedirectResponse(volta, status_code=303)
        campanha.conexao_id = conexao.id
        sessao.commit()
        _flash(request, "ok", "Conexao vinculada a campanha.")
        return RedirectResponse(volta, status_code=303)

    @app.post("/campanhas/{campanha_id}/iniciar")
    def campanha_iniciar(
        campanha_id: int,
        request: Request,
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        volta = f"/campanhas/{campanha_id}"
        bloqueio = _csrf_ou_volta(request, csrf, volta)
        if bloqueio:
            return bloqueio
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)

        ok, motivo = mod_disparo.pode_iniciar(sessao, campanha)
        if not ok:
            _flash(request, "erro", motivo)
            return RedirectResponse(volta, status_code=303)

        campanha.status = StatusCampanha.RODANDO
        campanha.motivo_pausa = None
        sessao.commit()
        app.state.disparo.iniciar(campanha.id)  # type: ignore[attr-defined]
        _flash(
            request,
            "ok",
            "Disparo iniciado. As mensagens saem no ritmo configurado — "
            "acompanhe o progresso abaixo.",
        )
        return RedirectResponse(volta, status_code=303)

    @app.post("/campanhas/{campanha_id}/pausar")
    def campanha_pausar(
        campanha_id: int,
        request: Request,
        csrf: Annotated[str, Form()] = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        volta = f"/campanhas/{campanha_id}"
        bloqueio = _csrf_ou_volta(request, csrf, volta)
        if bloqueio:
            return bloqueio
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)
        if campanha.status == StatusCampanha.RODANDO:
            campanha.status = StatusCampanha.PAUSADA
            campanha.motivo_pausa = "Pausada manualmente."
            sessao.commit()
            _flash(request, "ok", "Campanha pausada. Pode retomar sem reenviar o que ja saiu.")
        return RedirectResponse(volta, status_code=303)

    @app.get("/campanhas/{campanha_id}/progresso")
    def campanha_progresso(
        campanha_id: int,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return JSONResponse({"erro": "nao encontrada"}, status_code=404)
        dados = mod_disparo.progresso(sessao, campanha)
        dados["worker_ativo"] = app.state.disparo.esta_rodando(campanha.id)  # type: ignore[attr-defined]
        return JSONResponse(dados)

    @app.get("/campanhas/{campanha_id}/leads", response_class=HTMLResponse)
    def campanha_leads(
        campanha_id: int,
        request: Request,
        status: str = "",
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        campanha = _campanha_do_usuario(sessao, usuario, campanha_id)
        if campanha is None:
            return RedirectResponse("/app", status_code=303)

        consulta = select(Lead).where(Lead.campanha_id == campanha.id)
        if status:
            try:
                consulta = consulta.where(Lead.status == StatusLead(status))
            except ValueError:
                pass
        leads = sessao.scalars(consulta.order_by(Lead.id).limit(500)).all()

        return templates.TemplateResponse(
            request,
            "campanhas/leads.html",
            _ctx(
                request,
                usuario,
                campanha=campanha,
                leads=leads,
                status_filtro=status,
                status_opcoes=list(StatusLead),
            ),
        )

    # -- opt-out ------------------------------------------------------------

    @app.get("/optouts", response_class=HTMLResponse)
    def optouts(
        request: Request,
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        lista = sessao.scalars(
            select(OptOut)
            .where(OptOut.usuario_id == usuario.id)
            .order_by(OptOut.id.desc())
        ).all()
        return templates.TemplateResponse(
            request,
            "optouts.html",
            _ctx(request, usuario, optouts=lista),
        )

    @app.get("/optouts.csv")
    def optouts_csv(
        sessao: Session = Depends(get_sessao),
        usuario: Usuario = Depends(_exigir_usuario),
    ) -> Response:
        lista = sessao.scalars(
            select(OptOut)
            .where(OptOut.usuario_id == usuario.id)
            .order_by(OptOut.id.desc())
        ).all()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["telefone", "motivo", "criado_em"])
        for o in lista:
            writer.writerow(
                [
                    o.telefone,
                    o.motivo or "",
                    o.criado_em.isoformat() if o.criado_em else "",
                ]
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="optouts.csv"'
            },
        )

    # -- webhook Evolution (sem sessao de usuario) --------------------------

    @app.post("/webhook/evolution")
    async def webhook_evolution(
        request: Request,
        sessao: Session = Depends(get_sessao),
    ) -> Response:
        """Recebe eventos da Evolution: resposta de lead e (se vier) entrega.

        Sem autenticacao de cookie: a Evolution chama de dentro da rede Docker.
        Em producao, exponha so na rede interna ou coloque um segredo na URL.
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "erro": "json invalido"}, status_code=400)

        if not isinstance(payload, dict):
            return JSONResponse({"ok": True, "ignorado": True})

        resposta = interpretar_webhook(payload)
        if resposta is not None and resposta.e_resposta_de_lead:
            mod_disparo.processar_resposta_recebida(
                sessao,
                nome_instancia=resposta.instancia,
                numero=resposta.numero,
                texto=resposta.texto,
            )
            return JSONResponse({"ok": True, "tipo": "resposta"})

        evento = str(payload.get("event") or "").lower().replace("_", ".").replace("-", ".")

        # Pareamento do QR: Evolution avisa connection.update com state=open.
        if "connection.update" in evento:
            instancia = str(payload.get("instance") or "")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            estado_bruto = str(data.get("state") or data.get("status") or "").lower()
            if not estado_bruto and isinstance(data.get("instance"), dict):
                estado_bruto = str(data["instance"].get("state") or "").lower()
            if instancia and estado_bruto in {"open", "conectada", "connected"}:
                conexao = sessao.scalar(
                    select(Conexao).where(Conexao.nome_instancia == instancia)
                )
                if conexao is not None:
                    # Puxa numero/perfil com uma consulta dedicada.
                    try:
                        with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                            api = evo.estado_conexao(instancia)
                            _sincronizar_conexao(sessao, conexao, api)
                    except ErroEvolution:
                        conexao.status = StatusConexao.CONECTADA
                        if conexao.conectada_em is None:
                            from datetime import datetime, timezone

                            conexao.conectada_em = datetime.now(timezone.utc)
                        sessao.commit()
                return JSONResponse({"ok": True, "tipo": "conexao"})

        # Atualizacao de entrega / bloqueio, quando o payload carrega key.id.
        if "messages.update" in evento or "send.message" in evento:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            chave = data.get("key") if isinstance(data.get("key"), dict) else {}
            id_ext = str(chave.get("id") or data.get("idMessage") or "")
            status_bruto = str(
                data.get("status") or data.get("messageStatus") or ""
            ).lower()
            mapa = {
                "delivery_ack": StatusEntrega.ENTREGUE,
                "delivered": StatusEntrega.ENTREGUE,
                "read": StatusEntrega.LIDA,
                "played": StatusEntrega.LIDA,
                "server_ack": StatusEntrega.ENVIADA,
                "error": StatusEntrega.FALHOU,
                "blocked": StatusEntrega.BLOQUEADA,
            }
            if id_ext and status_bruto in mapa:
                mod_disparo.atualizar_entrega_por_id_externo(
                    sessao, id_ext, mapa[status_bruto]
                )
                return JSONResponse({"ok": True, "tipo": "entrega"})

        return JSONResponse({"ok": True, "ignorado": True})

    # -- erros de auth via exception ----------------------------------------

    @app.exception_handler(_PrecisaLogin)
    async def _redir_login(request: Request, _exc: _PrecisaLogin) -> Response:
        return RedirectResponse("/login", status_code=303)

    return app


# ---------------------------------------------------------------------------
# Auxiliares de modulo
# ---------------------------------------------------------------------------


class _PrecisaLogin(Exception):
    """Sinal interno: dependencia de usuario logado falhou."""


def _campanha_do_usuario(
    sessao: Session, usuario: Usuario, campanha_id: int
) -> Campanha | None:
    return sessao.scalar(
        select(Campanha).where(
            Campanha.id == campanha_id, Campanha.usuario_id == usuario.id
        )
    )


def _nome_instancia(usuario: Usuario) -> str:
    base = _RE_INSTANCIA.sub("-", usuario.email.split("@")[0].lower()).strip("-")
    base = (base or "user")[:40]
    sufixo = secrets.token_hex(3)
    return f"{base}-{usuario.id}-{sufixo}"


def _partir_modelos(texto: str) -> list[str]:
    bruto = texto.replace("\r\n", "\n").strip()
    if not bruto:
        return []
    if "\n---\n" in bruto:
        partes = bruto.split("\n---\n")
    else:
        partes = re.split(r"\n\s*\n", bruto)
    return [p.strip() for p in partes if p.strip()]


def _sincronizar_conexao(sessao: Session, conexao: Conexao, estado_api: Any) -> None:
    from datetime import datetime, timezone

    if estado_api.estado == CONECTADA:
        conexao.status = StatusConexao.CONECTADA
        if estado_api.numero:
            conexao.numero = estado_api.numero
        # So grava a primeira vez: reconectar nao zera o aquecimento.
        if conexao.conectada_em is None:
            conexao.conectada_em = datetime.now(timezone.utc)
    elif estado_api.estado == "aguardando_qr":
        conexao.status = StatusConexao.AGUARDANDO_QR
    elif estado_api.estado == "desconectada":
        conexao.status = StatusConexao.DESCONECTADA
    sessao.commit()


# Instancia padrao para `uvicorn app.main:app`.
app = criar_app()
