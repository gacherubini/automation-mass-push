"""Cliente Evolution API — tudo com httpx.MockTransport, zero rede."""

from __future__ import annotations

import httpx
import pytest

from app.evolution import (
    DESCONECTADA,
    CONECTADA,
    AGUARDANDO_QR,
    Evolution,
    FalhaDeAutenticacao,
    InstanciaJaExiste,
    InstanciaNaoEncontrada,
    JaConectado,
    LOTE_MAXIMO_CHECAGEM,
    NumeroInvalido,
    RequisicaoRecusada,
    RespostaInesperada,
    ServicoIndisponivel,
    interpretar_webhook,
)


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------


def _json(status: int, corpo: dict | list | None = None) -> httpx.Response:
    if corpo is None:
        return httpx.Response(status)
    return httpx.Response(status, json=corpo)


def _cliente(handler) -> Evolution:
    transport = httpx.MockTransport(handler)
    return Evolution(
        "http://evolution.test",
        "chave-teste",
        cliente=httpx.Client(transport=transport),
        # Testes nao dormem de verdade.
        dormir=lambda _s: None,
        tentativas=3,
        espera_base=0.0,
    )


# ---------------------------------------------------------------------------
# Instancia / QR / estado
# ---------------------------------------------------------------------------


class TestCriarInstancia:
    def test_cria_e_devolve_qrcode_aninhado(self):
        def handler(request: httpx.Request) -> httpx.Response:
            import json

            assert request.method == "POST"
            assert request.url.path == "/instance/create"
            assert request.headers["apikey"] == "chave-teste"
            payload = json.loads(request.content.decode())
            assert payload["instanceName"] == "dono-1"
            assert payload["integration"] == "WHATSAPP-BAILEYS"
            assert payload["qrcode"] is True
            assert payload["groupsIgnore"] is True
            return _json(
                201,
                {
                    "instance": {
                        "instanceName": "dono-1",
                        "instanceId": "uuid-1",
                        "status": "connecting",
                    },
                    "hash": "token-da-instancia",
                    "qrcode": {
                        "base64": "iVBORw0KGgo=",
                        "code": "2@abc",
                        "count": 1,
                        "pairingCode": "ABCD-1234",
                    },
                },
            )

        with _cliente(handler) as evo:
            inst = evo.criar_instancia("dono-1")

        assert inst.nome == "dono-1"
        assert inst.instancia_id == "uuid-1"
        assert inst.token == "token-da-instancia"
        assert inst.estado == AGUARDANDO_QR
        assert inst.qrcode is not None
        assert inst.qrcode.pairing_code == "ABCD-1234"
        assert inst.qrcode.imagem.startswith("data:image/png;base64,")

    def test_hash_legado_como_objeto(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(
                201,
                {
                    "instance": {"instanceName": "x", "instanceId": "1", "status": "close"},
                    "hash": {"apikey": "token-v2.0"},
                },
            )

        with _cliente(handler) as evo:
            assert evo.criar_instancia("x", com_qrcode=False).token == "token-v2.0"

    def test_nome_ja_em_uso(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(
                403,
                {
                    "status": 403,
                    "error": "Forbidden",
                    "response": {"message": "This name is already in use."},
                },
            )

        with _cliente(handler) as evo:
            with pytest.raises(InstanciaJaExiste, match="Ja existe"):
                evo.criar_instancia("dono-1")


class TestQrCode:
    def test_obtem_qr_na_raiz(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/instance/connect/dono-1"
            return _json(
                200,
                {
                    "base64": "data:image/png;base64,AAA",
                    "code": "2@xyz",
                    "count": 2,
                },
            )

        with _cliente(handler) as evo:
            qr = evo.obter_qrcode("dono-1")
        assert qr.imagem == "data:image/png;base64,AAA"
        assert qr.codigo == "2@xyz"

    def test_ja_conectado_levanta(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(200, {"instance": {"state": "open"}})

        with _cliente(handler) as evo:
            with pytest.raises(JaConectado, match="ja esta ativa"):
                evo.obter_qrcode("dono-1")

    def test_instancia_inexistente_via_erro_embutido(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(200, {"error": True, "message": "The instance does not exist"})

        with _cliente(handler) as evo:
            with pytest.raises(InstanciaNaoEncontrada):
                evo.obter_qrcode("fantasma")


class TestEstadoEDesconexao:
    def test_estado_conectada_com_numero(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/connectionState/dono-1"):
                return _json(200, {"instance": {"instanceName": "dono-1", "state": "open"}})
            if request.url.path.endswith("/fetchInstances"):
                return _json(
                    200,
                    [
                        {
                            "name": "dono-1",
                            "ownerJid": "5551998984086:12@s.whatsapp.net",
                            "profileName": "Guilherme",
                        }
                    ],
                )
            return _json(404, {"response": {"message": "not found"}})

        with _cliente(handler) as evo:
            estado = evo.estado_conexao("dono-1")
        assert estado.estado == CONECTADA
        assert estado.numero == "5551998984086"
        assert estado.perfil == "Guilherme"
        assert estado.pode_disparar is True

    def test_estado_desconectada_sem_buscar_dono(self):
        chamadas: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            chamadas.append(request.url.path)
            return _json(200, {"instance": {"state": "close"}})

        with _cliente(handler) as evo:
            estado = evo.estado_conexao("dono-1")
        assert estado.estado == DESCONECTADA
        assert estado.pode_disparar is False
        assert not any("fetchInstances" in c for c in chamadas)

    def test_desconectar_instancia_ja_caida_devolve_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(
                400,
                {
                    "status": 400,
                    "error": "Bad Request",
                    "response": {"message": "Instance is not connected"},
                },
            )

        with _cliente(handler) as evo:
            assert evo.desconectar("dono-1") is False

    def test_desconectar_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            return _json(200, {"status": "SUCCESS"})

        with _cliente(handler) as evo:
            assert evo.desconectar("dono-1") is True


# ---------------------------------------------------------------------------
# Checagem de numero
# ---------------------------------------------------------------------------


class TestChecagem:
    def test_checa_em_lote_e_preserva_ordem(self):
        def handler(request: httpx.Request) -> httpx.Response:
            import json

            corpo = json.loads(request.content.decode())
            assert corpo["numbers"] == ["5551998984086", "555130520478", "5551998581025"]
            return _json(
                200,
                [
                    {
                        "number": "5551998984086",
                        "exists": True,
                        "jid": "5551998984086@s.whatsapp.net",
                        "name": "Bicho Mania",
                    },
                    {
                        "number": "555130520478",
                        "exists": False,
                        "jid": "555130520478@s.whatsapp.net",
                    },
                    # servidor resolveu nono digito
                    {
                        "number": "5551998581025",
                        "exists": True,
                        "jid": "5551998581025@s.whatsapp.net",
                    },
                ],
            )

        # O cliente so tira pontuacao; a normalizacao E.164 e de telefone.py,
        # que roda antes de chegar aqui. Mandamos ja normalizados.
        with _cliente(handler) as evo:
            r = evo.checar_numeros(
                "dono-1",
                ["5551998984086", "555130520478", "5551998581025"],
            )
        assert [x.existe for x in r] == [True, False, True]
        assert r[0].numero == "5551998984086"
        assert r[0].nome == "Bicho Mania"
        assert r[1].existe is False

    def test_numero_ausente_na_resposta_vira_inexistente(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(200, [])

        with _cliente(handler) as evo:
            r = evo.checar_numero("dono-1", "5551997655755")
        assert r.existe is False
        assert r.consultado == "5551997655755"

    def test_lote_grande_e_partido(self):
        chamadas: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            corpo = json.loads(request.content.decode())
            chamadas.append(len(corpo["numbers"]))
            return _json(
                200,
                [
                    {
                        "number": n,
                        "exists": True,
                        "jid": f"{n}@s.whatsapp.net",
                    }
                    for n in corpo["numbers"]
                ],
            )

        muitos = [f"55519989{i:05d}" for i in range(LOTE_MAXIMO_CHECAGEM + 7)]
        with _cliente(handler) as evo:
            r = evo.checar_numeros("dono-1", muitos)
        assert len(r) == LOTE_MAXIMO_CHECAGEM + 7
        assert chamadas == [LOTE_MAXIMO_CHECAGEM, 7]


# ---------------------------------------------------------------------------
# Envio — a regra de ouro: NUNCA retry
# ---------------------------------------------------------------------------


class TestEnvio:
    def test_envio_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            import json

            assert request.url.path == "/message/sendText/dono-1"
            corpo = json.loads(request.content.decode())
            assert corpo["number"] == "5551998984086"
            assert corpo["text"] == "Oi Bicho Mania!"
            return _json(
                201,
                {
                    "key": {
                        "remoteJid": "5551998984086@s.whatsapp.net",
                        "id": "MSG3AB",
                    },
                    "status": "PENDING",
                    "messageTimestamp": 1700000000,
                },
            )

        with _cliente(handler) as evo:
            # Aceita mascara, mas o corpo da API leva so digitos.
            msg = evo.enviar_texto("dono-1", "55 51 99898-4086", "Oi Bicho Mania!")
        assert msg.id_mensagem == "MSG3AB"
        assert msg.numero == "5551998984086"
        assert msg.status == "PENDING"

    def test_timeout_no_envio_nao_repete(self):
        tentativas = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            raise httpx.ReadTimeout("timeout simulado")

        with _cliente(handler) as evo:
            with pytest.raises(ServicoIndisponivel, match="demorou demais"):
                evo.enviar_texto("dono-1", "5551998984086", "oi")
        assert tentativas["n"] == 1

    def test_5xx_no_envio_nao_repete(self):
        tentativas = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            return httpx.Response(503, text="unavailable")

        with _cliente(handler) as evo:
            with pytest.raises(ServicoIndisponivel):
                evo.enviar_texto("dono-1", "5551998984086", "oi")
        assert tentativas["n"] == 1

    def test_numero_sem_whatsapp_vira_NumeroInvalido(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(
                400,
                {
                    "status": 400,
                    "error": "Bad Request",
                    "response": {
                        "message": {
                            "exists": False,
                            "jid": "555130520478@s.whatsapp.net",
                            "number": "555130520478",
                        }
                    },
                },
            )

        with _cliente(handler) as evo:
            with pytest.raises(NumeroInvalido, match="nao tem WhatsApp"):
                evo.enviar_texto("dono-1", "555130520478", "oi")


class TestRetrySeguro:
    def test_consulta_de_estado_repete_em_5xx(self):
        tentativas = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            tentativas["n"] += 1
            if tentativas["n"] < 3:
                return httpx.Response(502, text="bad gateway")
            return _json(200, {"instance": {"state": "close"}})

        with _cliente(handler) as evo:
            estado = evo.estado_conexao("dono-1")
        assert estado.estado == DESCONECTADA
        assert tentativas["n"] == 3

    def test_401_vira_FalhaDeAutenticacao(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json(
                401,
                {
                    "status": 401,
                    "error": "Unauthorized",
                    "response": {"message": "Unauthorized"},
                },
            )

        with _cliente(handler) as evo:
            with pytest.raises(FalhaDeAutenticacao, match="Chave de API"):
                evo.estado_conexao("dono-1")


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_mensagem_de_lead(self):
        payload = {
            "event": "messages.upsert",
            "instance": "dono-1",
            "data": {
                "key": {
                    "remoteJid": "5551998984086@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG1",
                },
                "pushName": "Bicho Mania",
                "messageType": "conversation",
                "messageTimestamp": 1700000001,
                "message": {"conversation": "Oi, tenho interesse"},
            },
        }
        r = interpretar_webhook(payload)
        assert r is not None
        assert r.e_resposta_de_lead is True
        assert r.numero == "5551998984086"
        assert r.texto == "Oi, tenho interesse"
        assert r.do_proprio_dono is False
        assert r.nome_exibicao == "Bicho Mania"

    def test_fromMe_nao_conta_como_resposta(self):
        # Sem esse filtro o freio de reputacao contaria o proprio disparo.
        payload = {
            "event": "MESSAGES_UPSERT",
            "instance": "dono-1",
            "data": {
                "key": {
                    "remoteJid": "5551998984086@s.whatsapp.net",
                    "fromMe": True,
                    "id": "MSG2",
                },
                "message": {"conversation": "Oi! Vi a Bicho Mania no Maps"},
            },
        }
        r = interpretar_webhook(payload)
        assert r is not None
        assert r.do_proprio_dono is True
        assert r.e_resposta_de_lead is False

    def test_evento_que_nao_e_mensagem_devolve_none(self):
        assert interpretar_webhook({"event": "connection.update", "data": {}}) is None

    def test_extended_text_e_envelope_efemero(self):
        payload = {
            "event": "messages-upsert",
            "instance": "dono-1",
            "data": {
                "key": {
                    "remoteJid": "5551997655755@s.whatsapp.net",
                    "fromMe": False,
                    "id": "MSG3",
                },
                "message": {
                    "ephemeralMessage": {
                        "message": {
                            "extendedTextMessage": {"text": "para de mandar"}
                        }
                    }
                },
            },
        }
        r = interpretar_webhook(payload)
        assert r is not None
        assert r.texto == "para de mandar"
        assert r.e_resposta_de_lead is True

    def test_grupo_nao_conta_como_resposta_de_lead(self):
        payload = {
            "event": "messages.upsert",
            "instance": "dono-1",
            "data": {
                "key": {
                    "remoteJid": "120363@g.us",
                    "participant": "5551998984086@s.whatsapp.net",
                    "fromMe": False,
                    "id": "G1",
                },
                "message": {"conversation": "oi grupo"},
            },
        }
        r = interpretar_webhook(payload)
        assert r is not None
        assert r.e_grupo is True
        assert r.e_resposta_de_lead is False
        assert r.numero == "5551998984086"

    def test_lid_usa_remoteJidAlt(self):
        payload = {
            "event": "messages.upsert",
            "instance": "dono-1",
            "data": {
                "key": {
                    "remoteJid": "1234567890@lid",
                    "remoteJidAlt": "5551998581025@s.whatsapp.net",
                    "fromMe": False,
                    "id": "L1",
                },
                "message": {"conversation": "ola"},
            },
        }
        r = interpretar_webhook(payload)
        assert r is not None
        assert r.numero == "5551998581025"
