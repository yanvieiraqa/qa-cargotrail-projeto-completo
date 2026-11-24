"""Operações de negócio que conversam com o simulador e mexem em vários objetos."""
import threading
import time
from datetime import datetime
from xml.sax.saxutils import escape

import requests

from . import core
from .db import agora_iso, conectar, config_viagem, passou_s


class Erro(Exception):
    def __init__(self, status, mensagem, campos=None):
        super().__init__(mensagem)
        self.status, self.mensagem, self.campos = status, mensagem, campos


# ------------------------------------------------------------------ simulador
def chamar(cfg, metodo, caminho, corpo=None):
    """Chama o simulador. Devolve {'indisponivel': True} se ele não responder (fora do ar ou lento)."""
    try:
        r = requests.request(metodo, cfg.simulador_url + caminho, json=corpo, timeout=cfg.servico_timeout_s)
        if r.status_code >= 500:
            return {"indisponivel": True}
        dados = r.json()
        return dados if isinstance(dados, (dict, list)) else {"indisponivel": True}
    except (requests.RequestException, ValueError):
        return {"indisponivel": True}


def evento(conn, pedido_id, viagem_id, texto, origem="Sistema"):
    conn.execute("INSERT INTO eventos(viagem_id,pedido_id,texto,origem,criado_em) VALUES(?,?,?,?,?)",
                 (viagem_id, pedido_id, texto, origem, agora_iso()))


def fmt_duracao(seg):
    seg = int(round(seg))
    m, s = divmod(seg, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min" + (f" {s} s" if s else "")
    return f"{s} s"


# ------------------------------------------------------------------ documentos fiscais
def _xml_cte(pedido, transp, frete_c, aliq, icms_c, chave):
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            f"<CTe><chave>{chave}</chave><emitente><cnpj>{transp['cnpj']}</cnpj><nome>{escape(transp['razao_social'])}</nome></emitente>"
            f"<origem>{escape(pedido['origem'])}</origem><destino>{escape(pedido['destino'])}</destino>"
            f"<nfe>{pedido['nfe_chave']}</nfe><valorFrete>{frete_c / 100:.2f}</valorFrete>"
            f"<icms><aliquota>{aliq}</aliquota><valor>{icms_c / 100:.2f}</valor></icms></CTe>")


def _xml_mdfe(pedido, transp, motorista, veiculo, cte, chave):
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            f"<MDFe><chave>{chave}</chave><emitente><cnpj>{transp['cnpj']}</cnpj></emitente>"
            f"<cte>{cte['chave']}</cte><motorista><cpf>{motorista['cpf']}</cpf><nome>{escape(motorista['nome'])}</nome></motorista>"
            f"<veiculo><placa>{veiculo['placa']}</placa></veiculo>"
            f"<origem>{escape(pedido['origem'])}</origem><destino>{escape(pedido['destino'])}</destino></MDFe>")


def contexto_pedido(conn, pedido_id):
    pedido = conn.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    viagem = conn.execute("SELECT * FROM viagens WHERE pedido_id=? ORDER BY id DESC LIMIT 1", (pedido_id,)).fetchone()
    return pedido, viagem


def novo_documento(conn, pedido, viagem, tipo):
    """Cria o documento (CT-e ou MDF-e) com chave e XML, ainda sem enviar."""
    transp = conn.execute("SELECT * FROM transportadoras WHERE id=?", (viagem["transportadora_id"],)).fetchone()
    uf_o, uf_d = core.parse_local(pedido["origem"])[1], core.parse_local(pedido["destino"])[1]
    seq = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM documentos").fetchone()[0]
    chave = core.gerar_chave(uf_o, transp["cnpj"], "57" if tipo == "CTE" else "58", seq)
    while conn.execute("SELECT 1 FROM documentos WHERE chave=?", (chave,)).fetchone():
        seq += 1000
        chave = core.gerar_chave(uf_o, transp["cnpj"], "57" if tipo == "CTE" else "58", seq)
    aliq = icms = None
    if tipo == "CTE":
        aliq = core.aliquota_icms(uf_o, uf_d)
        icms = core.icms_centavos(viagem["frete_centavos"], aliq)
        xml = _xml_cte(pedido, transp, viagem["frete_centavos"], aliq, icms, chave)
    else:
        cte = conn.execute("SELECT * FROM documentos WHERE pedido_id=? AND tipo='CTE' AND status='Autorizado' ORDER BY id DESC LIMIT 1",
                           (pedido["id"],)).fetchone()
        mot = conn.execute("SELECT * FROM motoristas WHERE id=?", (viagem["motorista_id"],)).fetchone()
        vei = conn.execute("SELECT * FROM veiculos WHERE id=?", (viagem["veiculo_id"],)).fetchone()
        xml = _xml_mdfe(pedido, transp, mot, vei, cte, chave)
    cur = conn.execute("INSERT INTO documentos(pedido_id,tipo,status,chave,xml,icms_aliquota,icms_centavos,criado_em) "
                       "VALUES(?,?,?,?,?,?,?,?)", (pedido["id"], tipo, "Pendente de envio", chave, xml, aliq, icms, agora_iso()))
    return cur.lastrowid


def enviar_documento(cfg, conn, doc_id, origem):
    """Envia o documento à SEFAZ simulada e grava o resultado. Devolve a mensagem para o usuário."""
    doc = conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()
    rotulo = "CT-e" if doc["tipo"] == "CTE" else "MDF-e"
    resp = chamar(cfg, "POST", "/sefaz/cte" if doc["tipo"] == "CTE" else "/sefaz/mdfe", {"chave": doc["chave"], "xml": doc["xml"]})
    pedido, viagem = contexto_pedido(conn, doc["pedido_id"])
    vid = viagem["id"] if viagem else None
    if resp.get("indisponivel"):
        conn.execute("UPDATE documentos SET status='Pendente de envio' WHERE id=?", (doc_id,))
        msg = f"SEFAZ indisponível. O {rotulo} ficou pendente de envio e pode ser reenviado."
        evento(conn, doc["pedido_id"], vid, f"{rotulo} pendente de envio (SEFAZ indisponível)", origem)
    elif resp.get("status") == "autorizado":
        conn.execute("UPDATE documentos SET status='Autorizado', protocolo=?, autorizado_em=?, rejeicao_codigo=NULL, rejeicao_motivo=NULL WHERE id=?",
                     (resp.get("protocolo"), agora_iso(), doc_id))
        if doc["tipo"] == "MDFE":
            conn.execute("UPDATE pedidos SET status='Em execução' WHERE id=?", (doc["pedido_id"],))
        msg = f"{rotulo} autorizado. Protocolo {resp.get('protocolo')}."
        evento(conn, doc["pedido_id"], vid, f"{rotulo} autorizado (protocolo {resp.get('protocolo')})", origem)
    else:
        conn.execute("UPDATE documentos SET status='Rejeitado', rejeicao_codigo=?, rejeicao_motivo=? WHERE id=?",
                     (resp.get("codigo"), resp.get("motivo"), doc_id))
        msg = f"{rotulo} rejeitado. Código {resp.get('codigo')}: {resp.get('motivo')}."
        evento(conn, doc["pedido_id"], vid, f"{rotulo} rejeitado (código {resp.get('codigo')})", origem)
    conn.commit()
    return msg


def enviar_evento(cfg, caminho, chave, motivo=None):
    """Cancelamento ou encerramento. Devolve ('ok'|'rejeitado'|'indisponivel', resposta)."""
    resp = chamar(cfg, "POST", caminho, {"chave": chave, "motivo": motivo})
    if resp.get("indisponivel"):
        return "indisponivel", resp
    return ("ok" if resp.get("status") == "autorizado" else "rejeitado"), resp


# ------------------------------------------------------------------ viagem
def tem_mdfe_autorizado(conn, pedido_id):
    return conn.execute("SELECT 1 FROM documentos WHERE pedido_id=? AND tipo='MDFE' AND status='Autorizado'", (pedido_id,)).fetchone() is not None


def iniciar_viagem(conn, viagem, origem):
    if viagem["estado"] != "Aguardando saída":
        raise Erro(409, "Transição de estado inválida")
    if not tem_mdfe_autorizado(conn, viagem["pedido_id"]):
        raise Erro(409, "MDF-e autorizado é obrigatório")
    cfg_v = config_viagem(conn)
    agora = time.time()
    chegada = agora + float(cfg_v["tempo_transito_s"])
    conn.execute("UPDATE viagens SET estado='Em trânsito', iniciada_em=?, chegada_automatica_em=?, previsao_chegada_em=? WHERE id=?",
                 (agora, chegada, agora + float(cfg_v["previsao_chegada_s"]), viagem["id"]))
    evento(conn, viagem["pedido_id"], viagem["id"], "Viagem iniciada (em trânsito)", origem)
    conn.commit()


def registrar_chegada(conn, viagem, origem):
    agora = time.time()
    fator = float(config_viagem(conn)["fator_aceleracao"] or 1)
    simulado = (agora - (viagem["iniciada_em"] or agora)) * fator
    conn.execute("UPDATE viagens SET estado='No ponto de entrega', chegada_em=?, chegada_automatica_em=NULL WHERE id=?", (agora, viagem["id"]))
    evento(conn, viagem["pedido_id"], viagem["id"],
           f"Chegada ao ponto de entrega (duração simulada: {fmt_duracao(simulado)})", origem)
    conn.commit()


def tick(conn):
    """Um passo do agendador: avança viagens pelos tempos configurados (RN-MON-02 e RN-MON-03)."""
    cfg_v = config_viagem(conn)
    if cfg_v["modo_manual"]:
        return
    agora = time.time()
    for v in conn.execute("SELECT * FROM viagens WHERE estado='Em trânsito' AND chegada_automatica_em IS NOT NULL AND chegada_automatica_em<=?", (agora,)).fetchall():
        registrar_chegada(conn, v, "Sistema")
    if cfg_v["inicio_automatico"]:
        for v in conn.execute("SELECT * FROM viagens WHERE estado='Aguardando saída' AND ativa=1").fetchall():
            m = conn.execute("SELECT autorizado_em FROM documentos WHERE pedido_id=? AND tipo='MDFE' AND status='Autorizado'", (v["pedido_id"],)).fetchone()
            if m and m["autorizado_em"] and passou_s(m["autorizado_em"]) >= float(cfg_v["espera_inicio_s"]):
                iniciar_viagem(conn, v, "Sistema")


def iniciar_agendador(cfg):
    def laco():
        while True:
            try:
                conn = conectar(cfg.db_path)
                try:
                    tick(conn)
                finally:
                    conn.close()
            except Exception:  # o agendador nunca deve parar
                pass
            time.sleep(cfg.agendador_intervalo_s)

    t = threading.Thread(target=laco, name="agendador-viagem", daemon=True)
    t.start()
    return t
