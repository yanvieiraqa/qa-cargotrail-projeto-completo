"""Teste de fumaça da interface: sobe os dois serviços e percorre o fluxo completo no navegador.

Rodar:  python dev_tests/ui_smoke.py      (precisa de: pip install playwright && playwright install chromium)
Os prints ficam em dev_tests/shots/.
"""
import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app.api import create_app
from app.db import Config
from simulator.app import app as sim_app

SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)


def porta():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def servir(app, p):
    srv = make_server("127.0.0.1", p, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ps, pa = porta(), porta()
    servir(sim_app, ps)
    cfg = Config(db_path=tempfile.mktemp(suffix=".db"), simulador_url=f"http://127.0.0.1:{ps}", servico_timeout_s=3.0,
                 simulador_ativo=True, agendador_intervalo_s=0.4)
    servir(create_app(cfg, agendador=True), pa)
    url = f"http://127.0.0.1:{pa}"

    with sync_playwright() as p:
        nav = p.chromium.launch()
        ctx = nav.new_context(viewport={"width": 1366, "height": 850})
        pg = ctx.new_page()
        erros_js = []
        pg.on("pageerror", lambda e: erros_js.append(str(e)))
        pg.on("console", lambda m: erros_js.append(m.text) if m.type == "error" and "fonts.googleapis" not in m.text and "Failed to load resource" not in m.text else None)

        def tid(x):
            return pg.get_by_test_id(x)

        def entrar(email):
            pg.goto(url)
            tid("campo-email").fill(email)
            tid("campo-senha").fill("Teste@123")
            tid("login-submit").click()
            expect(tid("usuario-nome")).to_be_visible()

        def sair():
            tid("btn-sair").click()
            expect(tid("login-submit")).to_be_visible()

        # ---- login e proteção
        pg.goto(url)
        pg.screenshot(path=f"{SHOTS}/01_login.png")
        tid("campo-email").fill("operador@cargotrail.test")
        tid("campo-senha").fill("errada")
        tid("login-submit").click()
        expect(tid("erro-login")).to_contain_text("E-mail ou senha inválidos")
        expect(tid("faixa-simulacao")).to_contain_text("Ambiente de simulação")

        # ---- administrador: simulador e cadastros
        entrar("admin@cargotrail.test")
        expect(tid("usuario-perfil")).to_have_text("Administrador")
        pg.screenshot(path=f"{SHOTS}/02_painel.png")
        pg.goto(url + "/#/simulador")
        tid("campo-tempo_transito_s").fill("2")
        tid("campo-previsao_chegada_s").fill("30")
        tid("btn-salvar-viagem").click()
        expect(tid("toast").last).to_contain_text("salva")
        pg.screenshot(path=f"{SHOTS}/03_simulador.png", full_page=True)
        pg.goto(url + "/#/cadastros/transportadoras")
        expect(pg.get_by_text("Rota Norte Transportes Ltda")).to_be_visible()
        expect(pg.get_by_text("Bloqueada p/ contratação")).to_be_visible()
        pg.screenshot(path=f"{SHOTS}/04_cadastros.png")
        tid("btn-novo").click()
        tid("campo-razao_social").fill("Delta Cargas Ltda")
        tid("campo-cnpj").fill("11.222.333/0001-82")
        tid("janela-ok").click()
        expect(tid("erro-cnpj")).to_have_text("CNPJ inválido")
        tid("janela-cancelar").click()
        sair()

        # ---- operador: fluxo completo
        entrar("operador@cargotrail.test")
        expect(pg.get_by_text("Cadastros")).to_have_count(0)
        pg.goto(url + "/#/pedidos")
        tid("btn-novo-pedido").click()
        tid("campo-embarcador_id").select_option(label="Alfa Alimentos Ltda")
        tid("campo-origem").fill("São Paulo/SP")
        tid("campo-destino").fill("Belo Horizonte/MG")
        tid("campo-distancia_km").fill("600")
        tid("campo-produto").fill("Bebidas")
        tid("campo-peso_kg").fill("20000")
        tid("campo-valor_carga").fill("150000")
        tid("janela-ok").click()
        expect(tid("status-pedido")).to_have_text("Rascunho")
        tid("input-nfe").fill("35261011222333000181550010000001001200000005")
        tid("btn-vincular-nfe").click()
        expect(tid("erro-nfe")).to_contain_text("verificador")
        tid("input-nfe").fill("35261011222333000181550010000001001200000004")
        tid("btn-vincular-nfe").click()
        expect(tid("nfe-chave")).to_contain_text("35261011222333000181550010000001001200000004")
        tid("btn-enviar-contratacao").click()
        expect(tid("status-pedido")).to_have_text("Aguardando contratação")
        pg.screenshot(path=f"{SHOTS}/05_pedido.png", full_page=True)

        tid("aba-contratacao").click()
        tid("campo-transportadora_id").select_option(label="TransMinas Cargas Ltda")
        tid("campo-motorista_id").select_option(index=1)
        tid("campo-veiculo_id").select_option(index=1)
        expect(tid("piso-minimo")).to_contain_text("3.120,00")
        tid("campo-frete").fill("3000")
        tid("btn-contratar").click()
        expect(tid("erro-geral").first).to_contain_text("Frete abaixo do piso mínimo")
        tid("campo-frete").fill("3500")
        tid("btn-contratar").click()
        expect(tid("status-pedido")).to_have_text("Contratado")
        expect(tid("frete-valor")).to_contain_text("3.500,00")

        tid("aba-documentos").click()
        expect(tid("btn-gerar-ciot")).to_be_disabled()
        tid("btn-emitir-cte").click()
        expect(tid("btn-emitir-cte")).to_be_disabled()
        expect(pg.get_by_text("Autorizado").first).to_be_visible()
        tid("btn-gerar-ciot").click()
        expect(tid("ciot-numero")).to_be_visible()
        tid("btn-emitir-mdfe").click()
        expect(tid("status-pedido")).to_have_text("Em execução")
        pg.screenshot(path=f"{SHOTS}/06_documentos.png", full_page=True)

        tid("aba-viagem").click()
        expect(tid("estado-viagem")).to_have_text("Aguardando saída")
        tid("btn-iniciar-viagem").click()
        expect(tid("estado-viagem")).to_have_text("Em trânsito")
        tid("btn-registrar-ocorrencia").click()
        tid("campo-tipo").select_option("Parada")
        tid("campo-descricao").fill("Parada para descanso do motorista")
        tid("janela-ok").click()
        expect(tid("tabela-ocorrencias")).to_contain_text("descanso")
        expect(tid("estado-viagem")).to_have_text("No ponto de entrega", timeout=15000)   # avanço automático
        tid("btn-confirmar-entrega").click()
        tid("campo-recebedor_nome").fill("Marcos Almeida")
        tid("campo-recebedor_cpf").fill("111.111.111-11")
        tid("janela-ok").click()
        expect(tid("erro-recebedor_cpf")).to_have_text("CPF inválido")
        tid("campo-recebedor_cpf").fill("529.982.247-25")
        tid("janela-ok").click()
        expect(tid("estado-viagem")).to_have_text("Entregue")
        pg.screenshot(path=f"{SHOTS}/07_viagem.png", full_page=True)
        tid("aba-documentos").click()
        pg.locator("[data-testid^='btn-encerrar-']").click()
        expect(pg.locator("[data-testid^='doc-status-']").last).to_have_text("Encerrado")
        tid("aba-historico").click()
        expect(tid("linha-do-tempo-eventos")).to_contain_text("Chegada ao ponto de entrega")
        pg.screenshot(path=f"{SHOTS}/08_historico.png", full_page=True)
        sair()

        # ---- financeiro: pagamento
        entrar("financeiro@cargotrail.test")
        pg.goto(url + "/#/pagamentos")
        pg.locator("[data-testid^='btn-pagar-']").first.click()
        expect(pg.locator("[data-testid^='pag-situacao-']").first).to_have_text("Pago")
        pg.screenshot(path=f"{SHOTS}/09_pagamentos.png")
        pg.goto(url + "/#/pedidos")
        expect(tid("acesso-negado")).to_be_visible()
        sair()

        nav.close()
        if erros_js:
            print("ERROS DE JAVASCRIPT:", erros_js)
            sys.exit(1)
    print("UI OK — fluxo completo percorrido no navegador. Prints em", SHOTS)


if __name__ == "__main__":
    main()
