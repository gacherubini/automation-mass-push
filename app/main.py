"""Dashboard FastAPI: login, conexao WhatsApp, campanhas e leads.

O motor de disparo (`disparo.py`) ainda nao existe — daqui so se configura e
acompanha. A tela de conexao ja fala com a Evolution de verdade; se ela nao
estiver no ar, a pagina mostra o erro em portugues em vez de estourar 500.
"""

from __future__ import annotations

import io
import logging
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

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
from app.db import get_sessao
from app.evolution import (
    CONECTADA,
    Evolution,
    ErroEvolution,
    JaConectado,
)
from app.models import (
    Campanha,
    Conexao,
    Lead,
    OptOut,
    StatusCampanha,
    StatusConexao,
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
) -> FastAPI:
    """Monta a aplicacao. `fabrica_evolution` existe para o teste injetar mock."""
    cfg = configuracao()
    app = FastAPI(title="Prospeccao WhatsApp", docs_url=None, redoc_url=None)

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

    def _evolution() -> Evolution:
        if fabrica_evolution is not None:
            return fabrica_evolution()
        atual = configuracao()
        return Evolution(atual.evolution_url, atual.evolution_api_key)

    app.state.fabrica_evolution = _evolution  # type: ignore[attr-defined]

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
                            qr = evo.obter_qrcode(conexao.nome_instancia)
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

        nome = _nome_instancia(usuario)
        try:
            with app.state.fabrica_evolution() as evo:  # type: ignore[attr-defined]
                if existente is None:
                    evo.criar_instancia(nome)
                    conexao = Conexao(
                        usuario_id=usuario.id,
                        nome_instancia=nome,
                        status=StatusConexao.AGUARDANDO_QR,
                    )
                    sessao.add(conexao)
                else:
                    # Reusa a instancia se ainda existir no servidor.
                    try:
                        evo.obter_qrcode(existente.nome_instancia)
                        existente.status = StatusConexao.AGUARDANDO_QR
                    except ErroEvolution:
                        evo.criar_instancia(nome)
                        existente.nome_instancia = nome
                        existente.status = StatusConexao.AGUARDANDO_QR
                        existente.numero = None
                        existente.conectada_em = None
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
