"""Simulador dos serviços externos do CargoTrail: SEFAZ, CIOT e pagamento.

Tudo aqui é FICTÍCIO. Os cenários (autorizar, rejeitar, demorar, fora do ar) são
programados pela API de controle (/control/...), usada pelo painel do simulador.
"""
import random
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request

from common.kit import OpenApi

SERVICOS = ("sefaz", "ciot", "pagamento")
MODOS = ("autorizar", "rejeitar", "demorar", "fora_do_ar")
REJEICOES = {
    "204": "Duplicidade de documento",
    "225": "Falha no esquema XML",
    "539": "Divergência de dados do documento",
}

app = Flask(__name__)
LOCK = threading.RLock()
ESTADO = {}
api = OpenApi("CargoTrail — Simulador", "1.0.0",
              "Simula SEFAZ, CIOT e pagamento. Serviços e dados fictícios, só para estudo.")


def _padrao():
    return {"modo": "autorizar", "codigo": None, "motivo": None, "atraso_s": 0}


def reiniciar():
    with LOCK:
        ESTADO["cenarios"] = {s: _padrao() for s in SERVICOS}
        ESTADO["proxima"] = {s: None for s in SERVICOS}
        ESTADO["historico"] = []
        ESTADO["respostas"] = {}   # idempotência: mesma chave/referência devolve a mesma resposta
        ESTADO["seq"] = 0
        ESTADO["geracao"] = ESTADO.get("geracao", 0) + 1   # respostas atrasadas de antes do reset são descartadas


reiniciar()


def _agora():
    return datetime.now().isoformat(timespec="seconds")


def _resolver(servico):
    """Devolve o cenário a aplicar agora. O cenário 'só a próxima' é consumido."""
    with LOCK:
        if ESTADO["proxima"][servico]:
            sc = ESTADO["proxima"][servico]
            ESTADO["proxima"][servico] = None
            return sc
        return dict(ESTADO["cenarios"][servico])


def _espiar(servico):
    with LOCK:
        return ESTADO["proxima"][servico] or ESTADO["cenarios"][servico]


def _registrar(servico, tipo, ref, resultado, codigo=None, repetida=False):
    with LOCK:
        ESTADO["seq"] += 1
        ESTADO["historico"].append({"id": ESTADO["seq"], "hora": _agora(), "servico": servico,
                                    "tipo": tipo, "referencia": ref, "resultado": resultado, "codigo": codigo,
                                    "repetida": repetida})


def _processar(servico, tipo, ref, gerar_resposta):
    """Aplica o cenário programado e devolve a resposta HTTP."""
    sc = _resolver(servico)
    geracao = ESTADO["geracao"]
    modo = sc["modo"]
    if modo == "fora_do_ar":
        _registrar(servico, tipo, ref, "fora_do_ar")
        return jsonify({"erro": "Serviço indisponível"}), 503
    if modo == "demorar":
        time.sleep(float(sc.get("atraso_s") or 0))
    if modo == "rejeitar":
        padrao = "225" if servico == "sefaz" else "900"
        codigo = str(sc.get("codigo") or padrao)
        motivo = sc.get("motivo") or REJEICOES.get(codigo) or (
            "Rejeição simulada" if servico == "sefaz" else "Recusado pelo serviço simulado")
        _registrar(servico, tipo, ref, "rejeitado", codigo)
        status = "recusado" if servico == "pagamento" else "rejeitado"
        return jsonify({"status": status, "codigo": codigo, "motivo": motivo}), 200
    chave = (servico, tipo, ref)
    with LOCK:
        if ESTADO["geracao"] != geracao:   # o simulador foi restaurado enquanto esta chamada esperava
            return jsonify(gerar_resposta()), 200
        resposta = ESTADO["respostas"].get(chave)
        repetida = resposta is not None   # mesma chave/referência já processada: devolve a mesma resposta
        if resposta is None:
            resposta = gerar_resposta()
            ESTADO["respostas"][chave] = resposta
    _registrar(servico, tipo, ref, resposta["status"], repetida=repetida)
    return jsonify(resposta), 200


def _corpo():
    return request.get_json(silent=True) or {}


def rota(regra, metodos, resumo, tag, corpo=None, respostas=None):
    def deco(fn):
        api.doc(regra, metodos, resumo, tag, corpo=corpo, respostas=respostas)
        app.add_url_rule(regra, endpoint=fn.__name__, view_func=fn, methods=metodos)
        return fn
    return deco


# ------------------------------------------------------------------ SEFAZ
def _autorizar():
    return {"status": "autorizado", "protocolo": str(random.randint(10 ** 14, 10 ** 15 - 1)),
            "data_autorizacao": _agora()}


@rota("/sefaz/status", ["GET"], "Consulta de disponibilidade da SEFAZ simulada", "SEFAZ",
      respostas={"200": "Disponível", "503": "Fora do ar"})
def sefaz_status():
    if _espiar("sefaz")["modo"] == "fora_do_ar":
        return jsonify({"disponivel": False}), 503
    return jsonify({"disponivel": True})


@rota("/sefaz/cte", ["POST"], "Envia um CT-e para autorização", "SEFAZ", corpo={"chave": "string*", "xml": "string"})
def sefaz_cte():
    c = _corpo()
    return _processar("sefaz", "CT-e", c.get("chave"), _autorizar)


@rota("/sefaz/mdfe", ["POST"], "Envia um MDF-e para autorização", "SEFAZ", corpo={"chave": "string*", "xml": "string"})
def sefaz_mdfe():
    c = _corpo()
    return _processar("sefaz", "MDF-e", c.get("chave"), _autorizar)


@rota("/sefaz/cte/cancelamento", ["POST"], "Cancela um CT-e", "SEFAZ", corpo={"chave": "string*", "motivo": "string"})
def sefaz_cte_cancelamento():
    return _processar("sefaz", "Cancelamento CT-e", _corpo().get("chave"), _autorizar)


@rota("/sefaz/mdfe/cancelamento", ["POST"], "Cancela um MDF-e", "SEFAZ", corpo={"chave": "string*", "motivo": "string"})
def sefaz_mdfe_cancelamento():
    return _processar("sefaz", "Cancelamento MDF-e", _corpo().get("chave"), _autorizar)


@rota("/sefaz/mdfe/encerramento", ["POST"], "Encerra um MDF-e", "SEFAZ", corpo={"chave": "string*"})
def sefaz_mdfe_encerramento():
    return _processar("sefaz", "Encerramento MDF-e", _corpo().get("chave"), _autorizar)


# ------------------------------------------------------------------ CIOT e pagamento
@rota("/ciot/gerar", ["POST"], "Gera o CIOT de uma viagem", "CIOT", corpo={"viagem": "string*"})
def ciot_gerar():
    c = _corpo()
    return _processar("ciot", "CIOT", c.get("viagem"),
                      lambda: {"status": "gerado", "ciot": str(random.randint(10 ** 11, 10 ** 12 - 1))})


@rota("/pagamento/liberar", ["POST"], "Libera o pagamento do frete", "Pagamento",
      corpo={"referencia": "string*", "valor": "number*"})
def pagamento_liberar():
    c = _corpo()
    return _processar("pagamento", "Pagamento", c.get("referencia"),
                      lambda: {"status": "aprovado", "comprovante": "PG" + str(random.randint(10 ** 7, 10 ** 8 - 1)),
                               "valor": c.get("valor")})


# ------------------------------------------------------------------ Controle (painel)
@rota("/control/cenarios", ["GET"], "Cenários programados de cada serviço", "Controle")
def control_cenarios():
    with LOCK:
        return jsonify({"cenarios": ESTADO["cenarios"], "proxima": ESTADO["proxima"],
                        "rejeicoes": REJEICOES})


@rota("/control/cenarios/<servico>", ["PUT"], "Programa o cenário de um serviço", "Controle",
      corpo={"modo": "string*", "codigo": "string", "motivo": "string", "atraso_s": "number", "escopo": "string"},
      respostas={"200": "Cenário gravado", "422": "Dados inválidos"})
def control_programar(servico):
    c = _corpo()
    if servico not in SERVICOS:
        return jsonify({"erro": "Serviço desconhecido"}), 404
    modo = c.get("modo")
    escopo = c.get("escopo", "todas")
    if modo not in MODOS:
        return jsonify({"erro": "Modo inválido", "modos": MODOS}), 422
    if escopo not in ("todas", "proxima"):
        return jsonify({"erro": "Escopo inválido"}), 422
    try:
        atraso = float(c.get("atraso_s") or 0)
    except (TypeError, ValueError):
        return jsonify({"erro": "atraso_s deve ser um número"}), 422
    cenario = {"modo": modo, "codigo": c.get("codigo"), "motivo": c.get("motivo"), "atraso_s": atraso}
    with LOCK:
        if escopo == "proxima":
            ESTADO["proxima"][servico] = cenario
        else:
            ESTADO["cenarios"][servico] = cenario
            ESTADO["proxima"][servico] = None
    return jsonify({"servico": servico, "escopo": escopo, **cenario})


@rota("/control/historico", ["GET", "DELETE"], "Histórico das requisições recebidas (GET) ou limpeza (DELETE)", "Controle")
def control_historico():
    with LOCK:
        if request.method == "DELETE":
            ESTADO["historico"] = []
            return jsonify({"ok": True})
        return jsonify(ESTADO["historico"])


@rota("/control/restaurar", ["POST"], "Restaura o padrão do simulador (cenários, histórico e respostas)", "Controle")
def control_restaurar():
    reiniciar()
    return jsonify({"ok": True})


@rota("/health", ["GET"], "Verificação de saúde do serviço", "Controle")
def health():
    return jsonify({"status": "ok"})


api.registrar_paginas(app)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8001")), threaded=True)
