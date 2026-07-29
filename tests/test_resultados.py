"""Metricas do dashboard e comparacao das variacoes de abertura."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    Campanha,
    Conversa,
    EtapaConversa,
    Lead,
    Mensagem,
    PapelContato,
    StatusConversa,
    StatusEntrega,
    StatusLead,
    Usuario,
)
from app.resultados import resumo_usuario, variantes_da_campanha


def test_compara_variantes_e_monta_funil_do_usuario():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with fabrica() as sessao:
            usuario = Usuario(email="metricas@exemplo.com", senha_hash="x", nome="M")
            sessao.add(usuario)
            sessao.flush()
            campanha = Campanha(
                usuario_id=usuario.id,
                nome="Teste A/B",
                modelos=["Oi {nome}", "Ola {nome}"],
            )
            sessao.add(campanha)
            sessao.flush()

            lead_decisor = Lead(
                campanha_id=campanha.id,
                nome="Decisor",
                telefone="5551999999901",
                status=StatusLead.RESPONDEU,
            )
            lead_optout = Lead(
                campanha_id=campanha.id,
                nome="Optout",
                telefone="5551999999902",
                status=StatusLead.OPTOUT,
            )
            lead_falha = Lead(
                campanha_id=campanha.id,
                nome="Falha",
                telefone="5551999999903",
                status=StatusLead.FALHOU,
            )
            lead_b = Lead(
                campanha_id=campanha.id,
                nome="Variacao B",
                telefone="5551999999904",
                status=StatusLead.ENVIADO,
            )
            sessao.add_all([lead_decisor, lead_optout, lead_falha, lead_b])
            sessao.flush()
            sessao.add(
                Conversa(
                    lead_id=lead_decisor.id,
                    status=StatusConversa.AGUARDANDO_HUMANO,
                    papel_contato=PapelContato.DECISOR,
                    etapa=EtapaConversa.TRANSFERINDO,
                )
            )
            sessao.add_all(
                [
                    Mensagem(
                        lead_id=lead_decisor.id,
                        campanha_id=campanha.id,
                        texto="Oi Decisor",
                        variante_indice=1,
                        variante_texto="Oi {nome}",
                        status_entrega=StatusEntrega.LIDA,
                    ),
                    Mensagem(
                        lead_id=lead_optout.id,
                        campanha_id=campanha.id,
                        texto="Oi Optout",
                        variante_indice=1,
                        variante_texto="Oi {nome}",
                        status_entrega=StatusEntrega.ENTREGUE,
                    ),
                    Mensagem(
                        lead_id=lead_falha.id,
                        campanha_id=campanha.id,
                        texto="Oi Falha",
                        variante_indice=1,
                        variante_texto="Oi {nome}",
                        status_entrega=StatusEntrega.FALHOU,
                    ),
                    Mensagem(
                        lead_id=lead_b.id,
                        campanha_id=campanha.id,
                        texto="Ola Variacao B",
                        variante_indice=2,
                        variante_texto="Ola {nome}",
                        status_entrega=StatusEntrega.ENVIADA,
                    ),
                ]
            )
            sessao.commit()

            variantes = variantes_da_campanha(sessao, campanha.id)
            resumo = resumo_usuario(sessao, usuario.id)

        assert len(variantes) == 2
        primeira = variantes[0]
        assert primeira.indice == 1
        assert primeira.tentativas == 3
        assert primeira.enviadas == 2
        assert primeira.respostas == 2
        assert primeira.taxa_resposta == 100.0
        assert primeira.decisores == 1
        assert primeira.transferencias == 1
        assert primeira.optouts == 1
        assert primeira.falhas == 1

        assert resumo.campanhas == 1
        assert resumo.leads == 4
        assert resumo.enviadas == 3
        assert resumo.respostas == 2
        assert resumo.taxa_resposta == 66.7
        assert resumo.decisores == 1
        assert resumo.optouts == 1
        assert resumo.aguardando_humano == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
