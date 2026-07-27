import random
from datetime import datetime, time

import pytest

from app import ritmo


def situacao(**kwargs) -> ritmo.Situacao:
    base = dict(
        idade_conexao_dias=30,
        enviadas_hoje=0,
        enviadas_ultima_hora=0,
        total_entregues=0,
        total_respostas=0,
        total_bloqueios=0,
    )
    base.update(kwargs)
    return ritmo.Situacao(**base)


# Terca-feira, 10h - dentro de qualquer janela comercial.
TERCA_10H = datetime(2026, 7, 28, 10, 0)
SABADO_10H = datetime(2026, 8, 1, 10, 0)


class TestAquecimento:
    def test_conexao_nova_nao_passa_do_primeiro_degrau(self):
        perfil = ritmo.Perfil(teto_diario=200)
        assert ritmo.teto_do_dia(perfil, idade_conexao_dias=1) == 50
        assert ritmo.teto_do_dia(perfil, idade_conexao_dias=3) == 50

    def test_teto_sobe_conforme_a_conexao_amadurece(self):
        perfil = ritmo.Perfil(teto_diario=500)
        assert ritmo.teto_do_dia(perfil, 5) == 100
        assert ritmo.teto_do_dia(perfil, 10) == 200
        assert ritmo.teto_do_dia(perfil, 60) == ritmo.TETO_CONTA_MADURA

    def test_perfil_conservador_vence_o_aquecimento(self):
        # O aquecimento libera 200 no dia 10, mas o usuario pediu 40.
        perfil = ritmo.Perfil(teto_diario=40)
        assert ritmo.teto_do_dia(perfil, 10) == 40

    def test_usuario_pode_desligar_o_aquecimento(self):
        perfil = ritmo.Perfil(teto_diario=300, respeitar_aquecimento=False)
        assert ritmo.teto_do_dia(perfil, 1) == 300

    def test_aquecimento_ligado_barra_no_primeiro_dia(self):
        perfil = ritmo.Perfil(teto_diario=300)
        d = ritmo.avaliar(perfil, situacao(idade_conexao_dias=1, enviadas_hoje=50), TERCA_10H)
        assert not d.liberado
        assert "aquecimento" in d.motivo.lower()
        assert not d.freio_permanente, "aquecimento e espera, nao pausa da campanha"


class TestJanelaDeHorario:
    def test_horario_comercial_libera(self):
        assert ritmo.dentro_da_janela(ritmo.Perfil(), TERCA_10H)

    def test_madrugada_bloqueia(self):
        assert not ritmo.dentro_da_janela(ritmo.Perfil(), datetime(2026, 7, 28, 3, 0))

    def test_fim_de_semana_bloqueia_por_padrao(self):
        assert not ritmo.dentro_da_janela(ritmo.Perfil(), SABADO_10H)

    def test_fim_de_semana_liberado_se_o_usuario_quiser(self):
        perfil = ritmo.Perfil(dias_uteis_apenas=False)
        assert ritmo.dentro_da_janela(perfil, SABADO_10H)

    def test_limite_da_janela_e_exclusivo_no_fim(self):
        perfil = ritmo.Perfil(hora_inicio=time(9, 0), hora_fim=time(18, 0))
        assert ritmo.dentro_da_janela(perfil, datetime(2026, 7, 28, 9, 0))
        assert not ritmo.dentro_da_janela(perfil, datetime(2026, 7, 28, 18, 0))


class TestTetoPorHora:
    def test_barra_ao_encostar_no_teto_horario(self):
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(enviadas_ultima_hora=ritmo.TETO_HORA_SEGURO),
            TERCA_10H,
        )
        assert not d.liberado
        assert "hora" in d.motivo.lower()

    def test_abaixo_do_teto_horario_libera(self):
        d = ritmo.avaliar(ritmo.Perfil(), situacao(enviadas_ultima_hora=29), TERCA_10H)
        assert d.liberado


class TestFreioDeReputacao:
    def test_bloqueio_acima_de_2_por_cento_pausa_a_campanha(self):
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(total_entregues=100, total_respostas=50, total_bloqueios=3),
            TERCA_10H,
        )
        assert not d.liberado
        assert d.freio_permanente
        assert "bloqueio" in d.motivo.lower()

    def test_bloqueio_dentro_do_limite_nao_pausa(self):
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(total_entregues=100, total_respostas=50, total_bloqueios=2),
            TERCA_10H,
        )
        assert d.liberado

    def test_resposta_baixa_pausa_a_campanha(self):
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(total_entregues=100, total_respostas=10, total_bloqueios=0),
            TERCA_10H,
        )
        assert not d.liberado
        assert d.freio_permanente
        assert "resposta" in d.motivo.lower()

    def test_amostra_pequena_nao_aciona_o_freio(self):
        # 1 bloqueio em 5 envios da 20%, mas nao significa nada ainda.
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(total_entregues=5, total_respostas=0, total_bloqueios=1),
            TERCA_10H,
        )
        assert d.liberado, "freio nao pode disparar com amostra insignificante"

    def test_freio_de_reputacao_tem_prioridade_sobre_espera(self):
        # Fora da janela E com bloqueio alto: o usuario precisa ver o problema
        # real, nao "espere ate amanha".
        d = ritmo.avaliar(
            ritmo.Perfil(),
            situacao(total_entregues=100, total_respostas=50, total_bloqueios=10),
            datetime(2026, 7, 28, 3, 0),
        )
        assert d.freio_permanente
        assert "bloqueio" in d.motivo.lower()


class TestIntervaloAleatorio:
    def test_fica_dentro_da_faixa(self):
        perfil = ritmo.Perfil(intervalo_min_seg=60, intervalo_max_seg=120)
        rng = random.Random(1)
        for _ in range(200):
            assert 60 <= ritmo.proximo_intervalo(perfil, rng) <= 120

    def test_realmente_varia(self):
        # Intervalo constante e assinatura de robo; o sorteio precisa espalhar.
        perfil = ritmo.Perfil(intervalo_min_seg=60, intervalo_max_seg=120)
        rng = random.Random(7)
        valores = {ritmo.proximo_intervalo(perfil, rng) for _ in range(100)}
        assert len(valores) > 20

    def test_faixa_invertida_nao_quebra(self):
        perfil = ritmo.Perfil(intervalo_min_seg=120, intervalo_max_seg=60)
        assert 60 <= ritmo.proximo_intervalo(perfil, random.Random(3)) <= 120


class TestPadraoConservador:
    def test_padrao_nao_gera_aviso(self):
        assert ritmo.Perfil().avisos() == []

    def test_padrao_fica_abaixo_do_teto_horario_no_pior_caso(self):
        # A invariante que importa: mesmo sorteando sempre o intervalo mais
        # curto, o padrao nao pode passar de 30 mensagens/hora.
        perfil = ritmo.Perfil()
        por_hora_pior_caso = 3600 / perfil.intervalo_min_seg
        assert por_hora_pior_caso <= ritmo.TETO_HORA_SEGURO

    def test_cota_do_dia_nao_sai_numa_rajada_de_uma_hora(self):
        # Rajada e o padrao que a fiscalizacao procura. A cota diaria inteira
        # tem que levar mais de uma hora para sair.
        perfil = ritmo.Perfil()
        medio = (perfil.intervalo_min_seg + perfil.intervalo_max_seg) / 2
        horas = (perfil.teto_diario * medio) / 3600
        assert horas > 1

    def test_cota_do_dia_ainda_cabe_na_janela_comercial(self):
        # Conservador nao pode virar inutil: a cota precisa caber no expediente.
        perfil = ritmo.Perfil()
        medio = (perfil.intervalo_min_seg + perfil.intervalo_max_seg) / 2
        horas = (perfil.teto_diario * medio) / 3600
        janela = perfil.hora_fim.hour - perfil.hora_inicio.hour
        assert horas < janela


class TestAvisosDeConfiguracao:

    def test_intervalo_curto_avisa_sobre_o_teto_horario(self):
        avisos = ritmo.Perfil(intervalo_min_seg=20, intervalo_max_seg=40).avisos()
        assert any("60 mensagens/hora" in a for a in avisos)

    def test_intervalo_fixo_avisa(self):
        avisos = ritmo.Perfil(intervalo_min_seg=90, intervalo_max_seg=90).avisos()
        assert any("assinatura de automacao" in a for a in avisos)

    def test_volume_alto_avisa_sobre_api_oficial(self):
        avisos = ritmo.Perfil(teto_diario=500).avisos()
        assert any("oficial" in a for a in avisos)

    def test_janela_larga_demais_avisa(self):
        avisos = ritmo.Perfil(hora_inicio=time(0, 0), hora_fim=time(23, 59)).avisos()
        assert any("dia inteiro" in a for a in avisos)

    def test_avisos_nao_bloqueiam_o_envio(self):
        # O usuario escolheu na tela; ele ve o risco mas manda mesmo assim.
        perfil = ritmo.Perfil(teto_diario=500, intervalo_min_seg=20, intervalo_max_seg=25)
        assert perfil.avisos()
        assert ritmo.avaliar(perfil, situacao(), TERCA_10H).liberado


class TestRestantes:
    def test_conta_quanto_ainda_pode_sair_hoje(self):
        perfil = ritmo.Perfil(teto_diario=40)
        assert ritmo.restantes_hoje(perfil, situacao(enviadas_hoje=15)) == 25

    def test_nunca_devolve_negativo(self):
        perfil = ritmo.Perfil(teto_diario=40)
        assert ritmo.restantes_hoje(perfil, situacao(enviadas_hoje=99)) == 0

    def test_respeita_o_aquecimento(self):
        perfil = ritmo.Perfil(teto_diario=200)
        s = situacao(idade_conexao_dias=1, enviadas_hoje=10)
        assert ritmo.restantes_hoje(perfil, s) == 40


class TestCaminhoFeliz:
    def test_conta_madura_em_horario_comercial_libera(self):
        d = ritmo.avaliar(ritmo.Perfil(), situacao(), TERCA_10H)
        assert d.liberado
        assert d.motivo == ""

    @pytest.mark.parametrize("hora", [9, 12, 17])
    def test_ao_longo_do_expediente(self, hora):
        agora = datetime(2026, 7, 28, hora, 30)
        assert ritmo.avaliar(ritmo.Perfil(), situacao(), agora).liberado
