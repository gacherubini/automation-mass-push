from app import telefone as t


class TestCelular:
    def test_formato_do_google_maps(self):
        r = t.normalizar("(51) 99898-4086")
        assert r.numero == "5551998984086"
        assert r.tipo == t.CELULAR
        assert r.provavel_whatsapp

    def test_aceita_qualquer_pontuacao(self):
        esperado = "5551998984086"
        for bruto in [
            "(51) 99898-4086",
            "51998984086",
            "51 99898 4086",
            "+55 51 99898-4086",
            "5551998984086",
            "051 99898-4086",
            "  (51)99898.4086  ",
        ]:
            assert t.normalizar(bruto).numero == esperado, bruto

    def test_celular_antigo_de_oito_digitos(self):
        r = t.normalizar("(51) 9898-4086")
        assert r.tipo == t.CELULAR
        assert r.numero == "555198984086"


class TestFixo:
    def test_fixo_e_reconhecido_e_marcado_como_improvavel(self):
        # Telefone real de clinica veterinaria capturado no Maps.
        r = t.normalizar("(51) 3466-0454")
        assert r.tipo == t.FIXO
        assert r.numero == "555134660454"
        assert r.valido
        assert not r.provavel_whatsapp, "fixo nao deve virar disparo"

    def test_fixos_comecando_de_2_a_5(self):
        for prefixo in "2345":
            r = t.normalizar(f"(51) {prefixo}466-0454")
            assert r.tipo == t.FIXO, prefixo


class TestInvalido:
    def test_digitos_de_menos_ou_de_mais(self):
        for bruto in ["", "123", "51 9999", "5551998984086999", "abc"]:
            assert not t.normalizar(bruto).valido, bruto

    def test_ddd_inexistente(self):
        # 20, 23 e 63... 63 existe (TO). 20 e 23 nao existem.
        for ddd in ["20", "23", "26", "30", "90"]:
            r = t.normalizar(f"({ddd}) 99898-4086")
            assert not r.valido, ddd

    def test_nove_digitos_sem_comecar_com_nove(self):
        # 9 digitos so e valido para celular, que sempre comeca com 9.
        assert not t.normalizar("(51) 39898-4086").valido

    def test_invalido_preserva_o_original_para_o_usuario_ver(self):
        r = t.normalizar("liga no 0800")
        assert not r.valido
        assert r.original == "liga no 0800"


class TestVariantesDoNonoDigito:
    def test_celular_novo_oferece_versao_sem_o_nove(self):
        r = t.normalizar("(51) 99898-4086")
        assert t.variantes(r) == ["5551998984086", "555198984086"]

    def test_celular_antigo_oferece_versao_com_o_nove(self):
        r = t.normalizar("(51) 9898-4086")
        assert t.variantes(r) == ["555198984086", "5551998984086"]

    def test_a_mais_provavel_vem_primeiro(self):
        r = t.normalizar("(51) 99898-4086")
        assert t.variantes(r)[0] == r.numero

    def test_fixo_nao_tem_variante(self):
        r = t.normalizar("(51) 3466-0454")
        assert t.variantes(r) == ["555134660454"]

    def test_invalido_nao_gera_candidato(self):
        assert t.variantes(t.normalizar("xxx")) == []


class TestFormatacao:
    def test_volta_para_o_formato_legivel(self):
        assert t.formatar_br(t.normalizar("(51) 99898-4086")) == "(51) 99898-4086"
        assert t.formatar_br(t.normalizar("(51) 3466-0454")) == "(51) 3466-0454"

    def test_invalido_mostra_o_que_o_usuario_digitou(self):
        assert t.formatar_br(t.normalizar("sei la")) == "sei la"


class TestDeduplicacao:
    def test_mesmo_numero_escrito_diferente_normaliza_igual(self):
        # A lista de ja-contatados depende disso para nunca repetir alguem.
        escritas = ["(51) 99898-4086", "+5551998984086", "051998984086"]
        normalizados = {t.normalizar(x).numero for x in escritas}
        assert len(normalizados) == 1
