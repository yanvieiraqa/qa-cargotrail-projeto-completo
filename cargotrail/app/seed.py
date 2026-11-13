"""Carga padrão de dados (massa de teste) e criação de pedidos de teste em um estado escolhido (RN-SIM-06)."""
import random
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from . import core
from .db import agora_iso, config_viagem, criar_schema, iso_de

SENHA = "Teste@123"
_HASH = None

ESTADOS_TESTE = [
    "Rascunho", "Aguardando contratação", "Contratado", "CT-e pendente de envio", "CT-e rejeitado",
    "CT-e autorizado", "CIOT gerado", "MDF-e autorizado", "Em trânsito", "No ponto de entrega",
    "Entregue", "MDF-e encerrado", "Concluído",
]
CAPACIDADE_PADRAO = {"Van": 1500, "Caminhão 3/4": 5000, "Truck": 14000, "Carreta": 30000}


def _hash():
    global _HASH
    if _HASH is None:
        _HASH = generate_password_hash(SENHA, method="pbkdf2:sha256:50000")
    return _HASH


def _dias(n):
    return iso_de(datetime.now() + timedelta(days=n))[:10]


def carga_padrao(conn):
    """Recria o banco com usuários e cadastros da massa de teste (ADM-01, EMB-01, TRA-01 etc.)."""
    criar_schema(conn)
    h = _hash()
    for nome, email, perfil in [("Administrador", "admin@cargotrail.test", "Administrador"),
                                ("Operador", "operador@cargotrail.test", "Operador"),
                                ("Financeiro", "financeiro@cargotrail.test", "Financeiro")]:
        conn.execute("INSERT INTO usuarios(nome,email,senha_hash,perfil) VALUES(?,?,?,?)", (nome, email, h, perfil))
    conn.execute("INSERT INTO embarcadores(razao_social,cnpj,email,telefone,cep) VALUES(?,?,?,?,?)",
                 ("Alfa Alimentos Ltda", "11222333000181", "contato@alfa.test", "1140001000", "01310100"))
    transp = [
        ("TransMinas Cargas Ltda", "11444777000161", "12345678", _dias(365), 1),
        ("Rota Norte Transportes Ltda", "12345678000195", "87654321", _dias(-30), 1),
        ("Serra Fretes Ltda", "23456789000195", "11223344", _dias(365), 1),
        ("Planalto Logística Ltda", "34567890000130", "55667788", _dias(365), 0),
    ]
    for nome, cnpj, rntrc, val, ativo in transp:
        conn.execute("INSERT INTO transportadoras(razao_social,cnpj,rntrc,rntrc_validade,email,telefone,cep,ativo) "
                     "VALUES(?,?,?,?,?,?,?,?)", (nome, cnpj, rntrc, val, "contato@transp.test", "1130002000", "30130010", ativo))
    mot = [
        ("João da Silva Santos", "52998224725", "E", 1, 1), ("Pedro Henrique Costa", "11144477735", "B", 1, 1),
        ("Rui Barbosa Neto", "12345678909", "E", 1, 0), ("Marcos Vieira Dias", "98765432100", "E", 1, 1),
        ("Lucas Ramos Pinto", "24681357928", "E", 3, 1),
    ]
    for nome, cpf, cat, t, ativo in mot:
        conn.execute("INSERT INTO motoristas(nome,cpf,cnh_categoria,cnh_validade,transportadora_id,ativo) VALUES(?,?,?,?,?,?)",
                     (nome, cpf, cat, _dias(730), t, ativo))
    for placa, tipo, cap, t in [("ABC1D23", "Carreta", 30000, 1), ("BCD2E34", "Van", 1500, 1), ("CDE3456", "Truck", 14000, 1),
                                ("DEF4G56", "Carreta", 30000, 1), ("EFG5H67", "Carreta", 30000, 3)]:
        conn.execute("INSERT INTO veiculos(placa,tipo,capacidade_kg,transportadora_id) VALUES(?,?,?,?)", (placa, tipo, cap, t))
    conn.commit()


def _evento(conn, pedido_id, viagem_id, texto, origem="Sistema"):
    conn.execute("INSERT INTO eventos(viagem_id,pedido_id,texto,origem,criado_em) VALUES(?,?,?,?,?)",
                 (viagem_id, pedido_id, texto, origem, agora_iso()))


def _documento(conn, pedido, tipo, status, idade_s, transp_cnpj, extra=None):
    modelo = "57" if tipo == "CTE" else "58"
    seq = (conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] or 0) + 1
    uf = core.parse_local(pedido["origem"])[1]
    chave = core.gerar_chave(uf, transp_cnpj, modelo, seq)
    autorizado = status in ("Autorizado", "Encerrado")
    protocolo = str(random.randint(10 ** 14, 10 ** 15 - 1)) if autorizado else None
    aut_em = iso_de(datetime.now() - timedelta(seconds=idade_s)) if autorizado else None
    campos = {"icms_aliquota": None, "icms_centavos": None}
    if tipo == "CTE":
        uf_d = core.parse_local(pedido["destino"])[1]
        frete = (extra or {}).get("frete_centavos", 0)
        aliq = core.aliquota_icms(uf, uf_d)
        campos = {"icms_aliquota": aliq, "icms_centavos": core.icms_centavos(frete, aliq)}
    rej = ("225", "Falha no esquema XML") if status == "Rejeitado" else (None, None)
    conn.execute(
        "INSERT INTO documentos(pedido_id,tipo,status,chave,protocolo,rejeicao_codigo,rejeicao_motivo,xml,icms_aliquota,"
        "icms_centavos,autorizado_em,criado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (pedido["id"], tipo, status, chave, protocolo, rej[0], rej[1], f"<{tipo.lower()} chave='{chave}'/>",
         campos["icms_aliquota"], campos["icms_centavos"], aut_em, agora_iso()))


def criar_pedido_teste(conn, estado, **o):
    """Cria um pedido já no estado pedido, com motorista e veículo livres (quando o estado exige)."""
    if estado not in ESTADOS_TESTE:
        raise ValueError(f"Estado inválido. Use um destes: {', '.join(ESTADOS_TESTE)}")
    idx = ESTADOS_TESTE.index(estado)
    tipo_v = o.get("tipo_veiculo", "Carreta")
    if tipo_v not in core.TIPOS_VEICULO:
        raise ValueError("Tipo de veículo inválido")
    origem, destino = o.get("origem", "São Paulo/SP"), o.get("destino", "Belo Horizonte/MG")
    if not core.parse_local(origem) or not core.parse_local(destino):
        raise ValueError("Origem e destino devem ter o formato Cidade/UF")
    distancia = float(o.get("distancia_km", 600))
    peso = float(o.get("peso_kg", 20000))
    frete_c = core.centavos(o.get("frete", 3500))
    valor_c = core.centavos(o.get("valor_carga", 150000))
    emb = conn.execute("SELECT id FROM embarcadores WHERE ativo=1 ORDER BY id LIMIT 1").fetchone()["id"]

    chave = None if o.get("sem_nfe") else _nfe_livre(conn)
    status_pedido = {0: "Rascunho", 1: "Aguardando contratação"}.get(idx)
    if idx >= 2:
        status_pedido = "Contratado"
    if idx >= 7:
        status_pedido = "Em execução"
    if estado == "Concluído":
        status_pedido = "Concluído"
    cur = conn.execute(
        "INSERT INTO pedidos(embarcador_id,origem,destino,distancia_km,produto,peso_kg,valor_carga_centavos,nfe_chave,status,criado_em) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)", (emb, origem, destino, distancia, o.get("produto", "Bebidas"), peso, valor_c,
                                        chave if idx >= 1 or chave else None, status_pedido, agora_iso()))
    pid = cur.lastrowid
    if idx == 0 and not o.get("com_nfe"):
        conn.execute("UPDATE pedidos SET nfe_chave=NULL WHERE id=?", (pid,))
    _evento(conn, pid, None, f"Pedido de teste criado pelo simulador no estado '{estado}'")
    resultado = {"pedido_id": pid, "estado": estado, "status_pedido": status_pedido}
    if idx < 2:
        conn.commit()
        return resultado

    # transportadora, motorista e veículo livres
    transp = conn.execute("SELECT * FROM transportadoras WHERE cnpj='11444777000161'").fetchone()
    cpf = core.gerar_cpf()
    while conn.execute("SELECT 1 FROM motoristas WHERE cpf=?", (cpf,)).fetchone():
        cpf = core.gerar_cpf()
    mid = conn.execute("INSERT INTO motoristas(nome,cpf,cnh_categoria,cnh_validade,transportadora_id) VALUES(?,?,?,?,?)",
                       (f"Motorista Teste {cpf[-4:]}", cpf, "E", _dias(730), transp["id"])).lastrowid
    placa = core.gerar_placa_mercosul()
    while conn.execute("SELECT 1 FROM veiculos WHERE placa=?", (placa,)).fetchone():
        placa = core.gerar_placa_mercosul()
    vid = conn.execute("INSERT INTO veiculos(placa,tipo,capacidade_kg,transportadora_id) VALUES(?,?,?,?)",
                       (placa, tipo_v, max(CAPACIDADE_PADRAO[tipo_v], peso), transp["id"])).lastrowid
    cfg = config_viagem(conn)
    viagem_cur = conn.execute("INSERT INTO viagens(pedido_id,transportadora_id,motorista_id,veiculo_id,frete_centavos) VALUES(?,?,?,?,?)",
                              (pid, transp["id"], mid, vid, frete_c))
    viagem_id = viagem_cur.lastrowid
    resultado.update({"viagem_id": viagem_id, "numero_viagem": f"VIA-{viagem_id:04d}", "motorista_id": mid,
                      "veiculo_id": vid, "transportadora_id": transp["id"]})
    pedido = conn.execute("SELECT * FROM pedidos WHERE id=?", (pid,)).fetchone()
    _evento(conn, pid, viagem_id, "Contratação confirmada", "Sistema")

    idade_cte = float(o.get("idade_cte_dias", 0.001)) * 86400
    idade_mdfe = float(o.get("idade_mdfe_horas", 0.001)) * 3600
    cnpj = transp["cnpj"]
    if estado == "CT-e pendente de envio":
        _documento(conn, pedido, "CTE", "Pendente de envio", 0, cnpj, {"frete_centavos": frete_c})
    elif estado == "CT-e rejeitado":
        _documento(conn, pedido, "CTE", "Rejeitado", 0, cnpj, {"frete_centavos": frete_c})
    elif idx >= 5:
        _documento(conn, pedido, "CTE", "Autorizado", idade_cte, cnpj, {"frete_centavos": frete_c})
    if idx >= 6:
        conn.execute("INSERT INTO ciots(viagem_id,numero,criado_em) VALUES(?,?,?)",
                     (viagem_id, str(random.randint(10 ** 11, 10 ** 12 - 1)), agora_iso()))
    if idx >= 7:
        _documento(conn, pedido, "MDFE", "Encerrado" if idx >= 11 else "Autorizado", idade_mdfe, cnpj)
    agora = datetime.now().timestamp()
    if idx == 8:
        auto = None if o.get("sem_avanco_automatico") else agora + cfg["tempo_transito_s"]
        conn.execute("UPDATE viagens SET estado='Em trânsito', iniciada_em=?, chegada_automatica_em=?, previsao_chegada_em=? WHERE id=?",
                     (agora, auto, agora + cfg["previsao_chegada_s"], viagem_id))
        _evento(conn, pid, viagem_id, "Viagem iniciada")
    elif idx == 9:
        conn.execute("UPDATE viagens SET estado='No ponto de entrega', iniciada_em=?, chegada_em=? WHERE id=?",
                     (agora - 60, agora, viagem_id))
        _evento(conn, pid, viagem_id, "Chegada ao ponto de entrega")
    elif idx >= 10:
        diverg = o.get("divergencia", "Nenhuma")
        desc_c = core.centavos(o.get("desconto", 0)) if diverg == "Avaria" else 0
        conn.execute("UPDATE viagens SET estado='Entregue', ativa=0, iniciada_em=?, chegada_em=? WHERE id=?", (agora - 120, agora - 60, viagem_id))
        conn.execute("INSERT INTO comprovantes(viagem_id,recebedor_nome,recebedor_cpf,divergencia,descricao,desconto_centavos,criado_em) "
                     "VALUES(?,?,?,?,?,?,?)", (viagem_id, "Marcos Almeida", "52998224725", diverg,
                                              None if diverg == "Nenhuma" else "Divergência registrada no teste", desc_c, agora_iso()))
        _evento(conn, pid, viagem_id, "Entrega comprovada")
    if estado == "Concluído":
        valor = frete_c - (desc_c if idx >= 10 else 0)
        pg = conn.execute("INSERT INTO pagamentos(viagem_id,valor_centavos,status) VALUES(?,?,'Pago')", (viagem_id, valor)).lastrowid
        conn.execute("INSERT INTO pagamento_tentativas(pagamento_id,valor_centavos,status,criado_em) VALUES(?,?,?,?)",
                     (pg, valor, "Pago", agora_iso()))
    conn.commit()
    return resultado


def _nfe_livre(conn):
    while True:
        chave = core.novo_chave_nfe()
        if not conn.execute("SELECT 1 FROM pedidos WHERE nfe_chave=?", (chave,)).fetchone():
            return chave
