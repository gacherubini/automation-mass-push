import random

import pytest

from app import mensagem
from app.templates_presets import MODELOS_IA_PEQUENOS_NEGOCIOS
from app.planilha import Lead

BICHO_MANIA = Lead(
    nome="Bicho Mania",
    telefone="5551998984086",
    telefone_exibicao="(51) 99898-4086",
    endereco="R. Joaquim Caetano, 211",
    categoria="Banho e tosa",
    busca="pet shop canoas",
    link_maps="https://maps.google.com/?cid=1",
)
AGROPET = Lead(
    nome="Agropet Tipo Bicho",
    telefone="5551998581025",
    telefone_exibicao="(51) 99858-1025",
    endereco="R. Joaquim Nabuco, 171",
    categoria="Pet Shop",
    busca="pet shop canoas",
)
CAOTELLI = Lead(
    nome="CaoTelli",
    telefone="5551997655755",
    telefone_exibicao="(51) 99765-5755",
    endereco="Av. Santos Ferreira, 997",
    categoria="Pet Shop",
    busca="pet shop canoas",
)

MODELOS = [
    "Oi! Vi a {nome} aqui no Google Maps e achei o trabalho de voces bacana.",
    "Ola! Encontrei a {nome} pesquisando {busca} e queria trocar uma ideia.",
    "Bom dia! Passei pela {endereco} e conheci a {nome}. Posso te mostrar uma coisa?",
    "Oi! Trabalho com {categoria} tambem e a {nome} me chamou atencao no Maps.",
]


class TestValidacaoDoModelo:
    def test_modelo_do_scraper_e_valido(self):
        assert mensagem.lacunas_usadas(MODELOS[0]) == {"nome"}

    def test_reconhece_todas_as_lacunas(self):
        modelo = "{nome} {categoria} {endereco} {busca}"
        assert mensagem.lacunas_usadas(modelo) == set(mensagem.LACUNAS_VALIDAS)

    def test_texto_sem_lacuna_e_valido(self):
        assert mensagem.lacunas_usadas("Oi, tudo bem?") == frozenset()

    def test_lacuna_desconhecida_diz_qual_e_e_quais_valem(self):
        with pytest.raises(mensagem.ModeloInvalido) as erro:
            mensagem.lacunas_usadas("Vou mandar no {telefone} mesmo")

        texto = str(erro.value)
        assert "{telefone}" in texto
        assert "{nome}" in texto and "{categoria}" in texto

    def test_chave_aberta_e_nao_fechada(self):
        with pytest.raises(mensagem.ModeloInvalido, match="(?i)desbalanceada"):
            mensagem.lacunas_usadas("Oi, {nome, tudo bem?")

    def test_chave_fechada_solta(self):
        with pytest.raises(mensagem.ModeloInvalido, match="(?i)desbalanceada"):
            mensagem.lacunas_usadas("Oi nome}, tudo bem?")

    def test_lacuna_sem_nome(self):
        with pytest.raises(mensagem.ModeloInvalido, match="sem nome"):
            mensagem.lacunas_usadas("Oi {}, tudo bem?")

    def test_formatacao_extra_e_recusada_antes_do_disparo(self):
        # "{nome:d}" so quebraria com a fila andando.
        with pytest.raises(mensagem.ModeloInvalido, match="(?i)formatacao"):
            mensagem.lacunas_usadas("Oi {nome:d}")

    def test_chave_escapada_e_texto_normal(self):
        modelo = "Promo {{50% off}} para a {nome}"
        assert mensagem.lacunas_usadas(modelo) == {"nome"}
        assert mensagem.montar(modelo, BICHO_MANIA) == (
            "Promo {50% off} para a Bicho Mania"
        )


class TestValidacaoDaLista:
    def test_lista_boa_passa(self):
        assert mensagem.validar(MODELOS) is None

    def test_lista_vazia_e_recusada(self):
        with pytest.raises(mensagem.ModeloInvalido, match="(?i)pelo menos um"):
            mensagem.validar([])

    def test_modelo_em_branco_e_recusado(self):
        with pytest.raises(mensagem.ModeloInvalido, match="(?i)vazio"):
            mensagem.validar([MODELOS[0], "   "])

    def test_erro_aponta_a_posicao_do_modelo(self):
        with pytest.raises(mensagem.ModeloInvalido, match="Modelo 2"):
            mensagem.validar([MODELOS[0], "Manda no {telefone}"])


class TestMontagem:
    def test_troca_a_lacuna_pelo_dado_do_lead(self):
        texto = mensagem.montar("Oi! Vi a {nome} no Maps.", BICHO_MANIA)
        assert texto == "Oi! Vi a Bicho Mania no Maps."

    def test_usa_varias_lacunas_de_uma_vez(self):
        modelo = "{nome}, de {categoria}, na {endereco} (busca: {busca})"
        assert mensagem.montar(modelo, BICHO_MANIA) == (
            "Bicho Mania, de Banho e tosa, na R. Joaquim Caetano, 211 "
            "(busca: pet shop canoas)"
        )

    def test_aceita_lead_em_dicionario(self):
        # O lead pode vir do banco, nao so da planilha.
        texto = mensagem.montar("Oi, {nome}!", {"nome": "CaoTelli"})
        assert texto == "Oi, CaoTelli!"

    def test_lacuna_invalida_nao_chega_a_montar(self):
        with pytest.raises(mensagem.ModeloInvalido):
            mensagem.montar("Manda no {telefone}", BICHO_MANIA)


class TestLacunaSemValor:
    """A planilha do Maps vem furada: categoria e endereco faltam bastante."""

    SEM_CATEGORIA = Lead(
        nome="Pet do Bairro",
        telefone="5551997655755",
        telefone_exibicao="(51) 99765-5755",
        categoria="",
    )

    def test_nao_sai_none_nem_chave_crua(self):
        texto = mensagem.montar("Trabalho com {categoria} tambem.", self.SEM_CATEGORIA)
        assert "None" not in texto
        assert "{categoria}" not in texto

    def test_usa_o_substituto_neutro(self):
        texto = mensagem.montar("Trabalho com {categoria} tambem.", self.SEM_CATEGORIA)
        assert texto == "Trabalho com o seu segmento tambem."

    def test_nao_deixa_buraco_na_frase(self):
        # Substituir por vazio deixaria "Vi a  no Maps" - espaco duplo e artigo
        # solto denunciam o molde na hora.
        texto = mensagem.montar("Vi a {nome} no Maps.", Lead("", "5551", "(51)"))
        assert "  " not in texto
        assert texto == "Vi a sua loja no Maps."

    @pytest.mark.parametrize("lacuna", mensagem.LACUNAS_VALIDAS)
    def test_toda_lacuna_tem_substituto(self, lacuna):
        vazio = {campo: "" for campo in mensagem.LACUNAS_VALIDAS}
        texto = mensagem.montar("[{" + lacuna + "}]", vazio)
        assert texto == f"[{mensagem.SUBSTITUTOS[lacuna]}]"

    def test_campo_ausente_no_dicionario_tambem_usa_substituto(self):
        assert mensagem.montar("Oi {nome}!", {}) == "Oi sua loja!"

    def test_espaco_em_branco_conta_como_vazio(self):
        assert mensagem.montar("Oi {nome}!", {"nome": "   "}) == "Oi sua loja!"


class TestSorteioDeVariacao:
    def test_sempre_devolve_um_dos_modelos(self):
        rng = random.Random(1)
        for _ in range(100):
            assert mensagem.sortear(MODELOS, rng) in MODELOS

    def test_e_deterministico_com_a_mesma_semente(self):
        um = [mensagem.sortear(MODELOS, random.Random(42)) for _ in range(5)]
        outro = [mensagem.sortear(MODELOS, random.Random(42)) for _ in range(5)]
        assert um == outro

    def test_realmente_espalha_entre_as_variacoes(self):
        # Texto identico repetido e o padrao mais facil de detectar: o sorteio
        # existe para nao concentrar tudo numa variacao.
        rng = random.Random(7)
        usados = {mensagem.sortear(MODELOS, rng) for _ in range(60)}
        assert len(usados) == len(MODELOS)

    def test_lista_vazia_nao_sorteia(self):
        with pytest.raises(mensagem.ModeloInvalido):
            mensagem.sortear([], random.Random(1))

    def test_montar_para_junta_sorteio_e_preenchimento(self):
        texto = mensagem.montar_para(MODELOS, BICHO_MANIA, random.Random(3))
        assert "Bicho Mania" in texto
        assert "{" not in texto

    def test_escolha_carrega_indice_modelo_e_texto(self):
        escolha = mensagem.escolher_para(
            MODELOS, BICHO_MANIA, random.Random(3)
        )
        assert escolha.indice >= 1
        assert escolha.modelo == MODELOS[escolha.indice - 1]
        assert "Bicho Mania" in escolha.texto


class TestModeloPadraoIA:
    def test_variacoes_sao_validas_curtas_e_com_uma_pergunta(self):
        mensagem.validar(MODELOS_IA_PEQUENOS_NEGOCIOS)
        assert len(MODELOS_IA_PEQUENOS_NEGOCIOS) == 4
        for modelo in MODELOS_IA_PEQUENOS_NEGOCIOS:
            assert "automações de IA" in modelo
            assert len(modelo) <= 450
            assert modelo.count("?") == 1


class TestPrevia:
    def test_devolve_uma_mensagem_por_lead(self):
        pronto = mensagem.previa(MODELOS, [BICHO_MANIA, AGROPET, CAOTELLI],
                                 random.Random(5))
        assert len(pronto) == 3
        assert [p.nome for p in pronto] == [
            "Bicho Mania", "Agropet Tipo Bicho", "CaoTelli"
        ]

    def test_leva_o_telefone_para_a_tela_conferir(self):
        pronto = mensagem.previa(MODELOS, [BICHO_MANIA], random.Random(5))
        assert pronto[0].telefone == "5551998984086"

    def test_texto_ja_vem_montado(self):
        pronto = mensagem.previa(MODELOS, [BICHO_MANIA], random.Random(5))
        assert "Bicho Mania" in pronto[0].texto
        assert "{" not in pronto[0].texto

    def test_e_deterministica_para_a_tela_nao_dancar(self):
        leads = [BICHO_MANIA, AGROPET, CAOTELLI]
        um = mensagem.previa(MODELOS, leads, random.Random(9))
        outro = mensagem.previa(MODELOS, leads, random.Random(9))
        assert um == outro

    def test_lista_de_leads_vazia(self):
        assert mensagem.previa(MODELOS, [], random.Random(1)) == []

    def test_modelo_invalido_falha_antes_de_montar_qualquer_coisa(self):
        with pytest.raises(mensagem.ModeloInvalido):
            mensagem.previa(["Manda no {telefone}"], [BICHO_MANIA])


class TestDiversidade:
    def test_conta_quantos_recebem_o_mesmo_texto(self):
        d = mensagem.diversidade(MODELOS, destinatarios=40)
        assert d.variacoes == 4
        assert d.por_variacao == 10

    def test_poucas_variacoes_para_lista_grande_avisa(self):
        d = mensagem.diversidade(MODELOS, destinatarios=200)
        assert not d.suficiente
        assert str(d.por_variacao) in d.aviso()
        assert "spam" in d.aviso()

    def test_dentro_do_limite_nao_avisa(self):
        d = mensagem.diversidade(MODELOS, destinatarios=60)
        assert d.suficiente
        assert d.aviso() == ""

    def test_limite_e_o_da_pesquisa(self):
        no_limite = mensagem.diversidade([MODELOS[0]], destinatarios=15)
        passou = mensagem.diversidade([MODELOS[0]], destinatarios=16)
        assert no_limite.suficiente
        assert not passou.suficiente

    def test_modelo_repetido_nao_conta_como_variacao(self):
        # Colar o mesmo paragrafo duas vezes nao engana o outro lado.
        d = mensagem.diversidade([MODELOS[0], MODELOS[0], MODELOS[1]], 30)
        assert d.variacoes == 2

    def test_so_a_pontuacao_do_espaco_nao_faz_variacao(self):
        d = mensagem.diversidade([MODELOS[0], MODELOS[0].replace(" ", "  ")], 10)
        assert d.variacoes == 1

    def test_sugere_quantas_variacoes_escrever(self):
        d = mensagem.diversidade([MODELOS[0]], destinatarios=100)
        assert d.minimo_recomendado == 7
        assert "7" in d.aviso()

    def test_lista_pequena_nao_exige_nada(self):
        d = mensagem.diversidade([MODELOS[0]], destinatarios=0)
        assert d.suficiente
        assert d.minimo_recomendado == 1

    def test_modelos_invalidos_nao_passam_pela_estimativa(self):
        with pytest.raises(mensagem.ModeloInvalido):
            mensagem.diversidade(["Manda no {telefone}"], 10)


class TestFluxoCompleto:
    def test_da_planilha_ate_a_previa(self):
        leads = [BICHO_MANIA, AGROPET, CAOTELLI]
        mensagem.validar(MODELOS)
        assert mensagem.diversidade(MODELOS, len(leads)).suficiente

        pronto = mensagem.previa(MODELOS, leads, random.Random(11))

        assert len(pronto) == 3
        for item in pronto:
            assert item.texto
            assert "{" not in item.texto and "}" not in item.texto
            assert "None" not in item.texto
