"""Contrato do cliente Gemini sem acessar a internet."""

from __future__ import annotations

import json

import httpx
import pytest

from app.gemini import ContextoIA, ErroGemini, Gemini


def _contexto() -> ContextoIA:
    return ContextoIA(
        nome_lead="Bicho Mania",
        categoria="Pet shop",
        objetivo="Descobrir quem decide sobre marketing.",
        historico=(("lead", "Oi, sou atendente. Do que se trata?"),),
    )


def _resposta(dado: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(dado)}]}}
            ]
        },
    )


class TestGemini:
    def test_envia_schema_e_interpreta_decisao(self):
        capturado = {}

        def responder(request: httpx.Request) -> httpx.Response:
            capturado["request"] = request
            return _resposta(
                {
                    "resposta": "Quem cuida dessa parte por ai?",
                    "papel_contato": "atendente",
                    "intencao": "encaminhar",
                    "etapa": "buscando_decisor",
                    "precisa_humano": False,
                    "encerrar": False,
                    "confianca": 0.93,
                    "resumo": "Atendente pediu contexto.",
                }
            )

        cliente = httpx.Client(transport=httpx.MockTransport(responder))
        gemini = Gemini("segredo", "gemini-teste", "https://gemini.test/v1beta", cliente=cliente)
        decisao = gemini.decidir(_contexto())

        assert decisao.papel_contato == "atendente"
        assert decisao.etapa == "buscando_decisor"
        request = capturado["request"]
        assert request.headers["x-goog-api-key"] == "segredo"
        assert request.url.path.endswith("/models/gemini-teste:generateContent")
        corpo = json.loads(request.content)
        assert corpo["generationConfig"]["responseFormat"]["text"]["mimeType"] == "APPLICATION_JSON"

    def test_sem_chave_falha_antes_da_rede(self):
        gemini = Gemini("", "modelo", "https://gemini.test")
        with pytest.raises(ErroGemini, match="GEMINI_API_KEY"):
            gemini.decidir(_contexto())

    def test_limite_da_api_vira_erro_claro(self):
        cliente = httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(429))
        )
        gemini = Gemini("x", "modelo", "https://gemini.test", cliente=cliente)
        with pytest.raises(ErroGemini, match="Limite"):
            gemini.decidir(_contexto())

    def test_classificacao_fora_do_contrato_e_rejeitada(self):
        cliente = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: _resposta(
                    {
                        "resposta": "oi",
                        "papel_contato": "presidente_do_mundo",
                        "intencao": "x",
                        "etapa": "identificando",
                        "precisa_humano": False,
                        "encerrar": False,
                        "confianca": 1,
                        "resumo": "x",
                    }
                )
            )
        )
        gemini = Gemini("x", "modelo", "https://gemini.test", cliente=cliente)
        with pytest.raises(ErroGemini, match="classificacoes"):
            gemini.decidir(_contexto())

    def test_resposta_grande_demais_e_rejeitada(self):
        dado = {
            "resposta": "x" * 501,
            "papel_contato": "desconhecido",
            "intencao": "x",
            "etapa": "identificando",
            "precisa_humano": False,
            "encerrar": False,
            "confianca": 0.5,
            "resumo": "x",
        }
        cliente = httpx.Client(
            transport=httpx.MockTransport(lambda _: _resposta(dado))
        )
        gemini = Gemini("x", "modelo", "https://gemini.test", cliente=cliente)
        with pytest.raises(ErroGemini, match="500 caracteres"):
            gemini.decidir(_contexto())
