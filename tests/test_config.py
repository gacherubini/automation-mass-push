"""Configuracao lida do ambiente."""

from __future__ import annotations

import pytest

from app import config as modulo_config
from app.config import (
    CHAVE_EVOLUTION_PADRAO,
    SECRET_KEY_PADRAO,
    URL_BANCO_PADRAO,
    configuracao,
    recarregar,
)


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch):
    """Cada teste comeca sem variaveis herdadas e com cache zerado."""
    for nome in (
        "DATABASE_URL",
        "SECRET_KEY",
        "EVOLUTION_URL",
        "EVOLUTION_API_KEY",
        "DEBUG_SQL",
        "POOL_SIZE",
        "POOL_MAX_OVERFLOW",
        "POOL_RECICLAR_SEG",
        "AMBIENTE",
    ):
        monkeypatch.delenv(nome, raising=False)
    modulo_config.configuracao.cache_clear()
    yield
    modulo_config.configuracao.cache_clear()


class TestDefaults:
    def test_roda_sem_env_com_defaults_locais(self):
        cfg = configuracao()
        assert cfg.database_url == URL_BANCO_PADRAO
        assert cfg.secret_key == SECRET_KEY_PADRAO
        assert cfg.evolution_api_key == CHAVE_EVOLUTION_PADRAO
        assert cfg.evolution_url == "http://localhost:8080"
        assert cfg.ambiente == "desenvolvimento"
        assert cfg.producao is False
        assert cfg.avisos == ()

    def test_string_vazia_conta_como_ausente(self, monkeypatch):
        # No compose e facil deixar `EVOLUTION_API_KEY=` sem valor.
        monkeypatch.setenv("EVOLUTION_API_KEY", "   ")
        monkeypatch.setenv("SECRET_KEY", "")
        cfg = recarregar()
        assert cfg.evolution_api_key == CHAVE_EVOLUTION_PADRAO
        assert cfg.secret_key == SECRET_KEY_PADRAO

    def test_barra_final_da_url_da_evolution_e_removida(self, monkeypatch):
        monkeypatch.setenv("EVOLUTION_URL", "http://evolution:8080/")
        cfg = recarregar()
        assert cfg.evolution_url == "http://evolution:8080"


class TestTipos:
    def test_inteiro_malformado_levanta_erro_claro(self, monkeypatch):
        monkeypatch.setenv("POOL_SIZE", "dez")
        with pytest.raises(ValueError, match="POOL_SIZE"):
            recarregar()

    def test_booleano_aceita_variantes_comuns(self, monkeypatch):
        monkeypatch.setenv("DEBUG_SQL", "SIM")
        assert recarregar().debug_sql is True
        monkeypatch.setenv("DEBUG_SQL", "0")
        assert recarregar().debug_sql is False


class TestProducao:
    def test_defaults_inseguros_viram_aviso_em_producao(self, monkeypatch):
        monkeypatch.setenv("AMBIENTE", "producao")
        cfg = recarregar()
        assert cfg.producao is True
        assert any("SECRET_KEY" in a for a in cfg.avisos)
        assert any("EVOLUTION_API_KEY" in a for a in cfg.avisos)

    def test_segredos_proprios_em_producao_nao_avisam(self, monkeypatch):
        monkeypatch.setenv("AMBIENTE", "producao")
        monkeypatch.setenv("SECRET_KEY", "chave-secreta-de-verdade")
        monkeypatch.setenv("EVOLUTION_API_KEY", "chave-evolution-de-verdade")
        cfg = recarregar()
        assert cfg.avisos == ()
