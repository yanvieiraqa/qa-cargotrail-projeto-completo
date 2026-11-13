"""API do CargoTrail (Flask). Documentação automática em /docs (Swagger) e /openapi.json."""
import secrets
import time
from datetime import date, datetime

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash

from common.kit import OpenApi

from . import core, seed, servicos
from .db import Config, agora_iso, conectar, config_viagem, gravar_config_viagem, passou_s
from .servicos import Erro, chamar, evento

ADM, OPE, FIN = "Administrador", "Operador", "Financeiro"
LEITURA_OPERACAO = (OPE, ADM)   # Administrador só consulta pedidos, viagens e documentos
CAD_CONFIG = {
    "embarcadores": ["razao_social", "cnpj", "email", "telefone", "cep"],
    "transportadoras": ["razao_social", "cnpj", "rntrc", "rntrc_validade", "email", "telefone", "cep"],
    "motoristas": ["nome", "cpf", "cnh_categoria", "cnh_validade", "transportadora_id"],
    "veiculos": ["placa", "tipo", "capacidade_kg", "transportadora_id"],
}
CAD_CORPO = {
    "embarcadores": {"razao_social": "string*", "cnpj": "string*", "email": "string*", "telefone": "string*", "cep": "string*"},
    "transportadoras": {"razao_social": "string*", "cnpj": "string*", "rntrc": "string*", "rntrc_validade": "string*",
                        "email": "string*", "telefone": "string*", "cep": "string*"},
    "motoristas": {"nome": "string*", "cpf": "string*", "cnh_categoria": "string*", "cnh_validade": "string*", "transportadora_id": "integer*"},
    "veiculos": {"placa": "string*", "tipo": "string*", "capacidade_kg": "number*", "transportadora_id": "integer*"},
}
ROTULO = {"embarcadores": "Embarcador", "transportadoras": "Transportadora", "motoristas": "Motorista", "veiculos": "Veículo"}


def create_app(config=None, agendador=True):
    cfg = config or Config()
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    kit = OpenApi("CargoTrail — API", "1.0.0",
                  "Sistema fictício de gestão de transportes (TMS) para portfólio de QA. Todos os dados são fictícios. "
                  "Faça login em /api/auth/login e use o token como Bearer.")

    # inicialização do banco
    conn0 = conectar(cfg.db_path)
    if not conn0.execute("SELECT 1 FROM sqlite_master WHERE name='usuarios'").fetchone():
        seed.carga_padrao(conn0)
    conn0.close()

    # ------------------------------------------------------------------ infraestrutura
    def db():
        if "db" not in g:
            g.db = conectar(cfg.db_path)
        return g.db

    @app.teardown_appcontext
    def fechar(_exc):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    @app.errorhandler(Erro)
    def tratar_erro(e):
        corpo = {"erro": e.mensagem}
        if e.campos:
            corpo["campos"] = e.campos
        return jsonify(corpo), e.status

    @app.errorhandler(404)
    def nao_achado(_e):
        return jsonify({"erro": "Não encontrado"}), 404

    @app.errorhandler(405)
    def metodo_nao_permitido(_e):
        return jsonify({"erro": "Método não permitido"}), 405

    def autenticar(perfis):
        cab = request.headers.get("Authorization", "")
        if not cab.startswith("Bearer "):
            raise Erro(401, "Não autenticado")
        con = db()
        s = con.execute("SELECT * FROM sessoes WHERE token=?", (cab[7:],)).fetchone()
        if not s:
            raise Erro(401, "Não autenticado")
        if time.time() - s["ultima_atividade"] > cfg.sessao_timeout_s:
            con.execute("DELETE FROM sessoes WHERE token=?", (s["token"],))
            con.commit()
            raise Erro(401, "Sessão expirada")
        u = con.execute("SELECT * FROM usuarios WHERE id=?", (s["usuario_id"],)).fetchone()
        if not u or u["status"] != "Ativo":
            raise Erro(401, "Não autenticado")
        con.execute("UPDATE sessoes SET ultima_atividade=? WHERE token=?", (time.time(), s["token"]))
        con.commit()
        if perfis != "*" and u["perfil"] not in perfis:
            raise Erro(403, "Acesso negado")
        g.usuario = u

    def rota(regra, metodos, resumo, tag, perfis=None, corpo=None, respostas=None, nome=None):
        def deco(fn):
            kit.doc(regra, metodos, resumo, tag, corpo=corpo, autenticada=perfis is not None, respostas=respostas)

            def visao(*a, **k):
                if perfis is not None:
                    autenticar(perfis)
                return fn(*a, **k)

            endpoint = nome or fn.__name__
            visao.__name__ = endpoint
            app.add_url_rule(regra, endpoint=endpoint, view_func=visao, methods=metodos)
            return fn
        return deco

    def corpo():
        d = request.get_json(silent=True)
        return d if isinstance(d, dict) else {}

    def vazio(v):
        return v is None or str(v).strip() == ""

    def exigir(d, campos):
        erros = {c: "Campo obrigatório" for c in campos if vazio(d.get(c))}
        if erros:
            raise Erro(422, "Dados inválidos", erros)

    def money(c):
        return None if c is None else c / 100

    def iso_epoch(t):
        return None if t is None else datetime.fromtimestamp(t).isoformat(timespec="seconds")

    def quem():
        return g.usuario["nome"]

    # ------------------------------------------------------------------ público
    @rota("/api/config", ["GET"], "Configuração pública (simulador ativo?)", "Sistema")
    def config_publica():
        return jsonify({"simulador_ativo": cfg.simulador_ativo, "sessao_timeout_s": cfg.sessao_timeout_s, "versao": "1.0.0"})

    # ------------------------------------------------------------------ autenticação (RN-ACE)
    @rota("/api/auth/login", ["POST"], "Login", "Acesso", corpo={"email": "string*", "senha": "string*"},
          respostas={"200": "Token de acesso", "401": "Credenciais inválidas", "423": "Conta bloqueada", "422": "Dados incompletos"})
    def login():
        d, con = corpo(), db()
        if vazio(d.get("email")) or vazio(d.get("senha")):
            raise Erro(422, "E-mail e senha são obrigatórios")
        u = con.execute("SELECT * FROM usuarios WHERE lower(email)=lower(?)", (str(d["email"]).strip(),)).fetchone()
        if u and u["status"] == "Bloqueado":
            raise Erro(423, "Conta bloqueada. Procure o Administrador.")
        if not u or not check_password_hash(u["senha_hash"], str(d["senha"])):
            if u:
                tent = u["tentativas"] + 1
                if tent >= cfg.max_tentativas_login:
                    con.execute("UPDATE usuarios SET tentativas=?, status='Bloqueado' WHERE id=?", (tent, u["id"]))
                    con.commit()
                    raise Erro(423, f"Conta bloqueada após {cfg.max_tentativas_login} tentativas com senha errada.")
                con.execute("UPDATE usuarios SET tentativas=? WHERE id=?", (tent, u["id"]))
                con.commit()
            raise Erro(401, "E-mail ou senha inválidos")
        con.execute("UPDATE usuarios SET tentativas=0 WHERE id=?", (u["id"],))
        token = secrets.token_hex(24)
        con.execute("INSERT INTO sessoes(token,usuario_id,ultima_atividade) VALUES(?,?,?)", (token, u["id"], time.time()))
        con.commit()
        return jsonify({"token": token, "usuario": {"id": u["id"], "nome": u["nome"], "email": u["email"], "perfil": u["perfil"]}})

    @rota("/api/auth/me", ["GET"], "Usuário logado", "Acesso", perfis="*")
    def me():
        u = g.usuario
        return jsonify({"id": u["id"], "nome": u["nome"], "email": u["email"], "perfil": u["perfil"]})

    @rota("/api/auth/logout", ["POST"], "Encerra a sessão", "Acesso", perfis="*")
    def logout():
        db().execute("DELETE FROM sessoes WHERE token=?", (request.headers["Authorization"][7:],))
        db().commit()
        return jsonify({"ok": True})

    @rota("/api/usuarios", ["GET"], "Lista usuários", "Acesso", perfis=(ADM,))
    def usuarios():
        return jsonify([{"id": u["id"], "nome": u["nome"], "email": u["email"], "perfil": u["perfil"], "status": u["status"]}
                        for u in db().execute("SELECT * FROM usuarios ORDER BY id")])

    @rota("/api/usuarios/<int:uid>/desbloquear", ["POST"], "Desbloqueia um usuário", "Acesso", perfis=(ADM,))
    def desbloquear(uid):
        con = db()
        if not con.execute("SELECT 1 FROM usuarios WHERE id=?", (uid,)).fetchone():
            raise Erro(404, "Usuário não encontrado")
        con.execute("UPDATE usuarios SET status='Ativo', tentativas=0 WHERE id=?", (uid,))
        con.commit()
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ cadastros (RN-CAD)
    def cnpj_em_uso(con, cnpj, ignorar):
        for t in ("embarcadores", "transportadoras"):
            for r in con.execute(f"SELECT id FROM {t} WHERE cnpj=?", (cnpj,)):
                if (t, r["id"]) != ignorar:
                    return True
        return False

    def validar_cadastro(ent, d, atual=None):
        con = db()
        erros, out = {}, {}
        obrig = CAD_CONFIG[ent]
        for c in obrig:
            if vazio(d.get(c)):
                erros[c] = "Campo obrigatório"
            else:
                out[c] = d[c]
        ident = (ent, atual["id"]) if atual else (ent, None)
        if "cnpj" in out:
            if not core.cnpj_valido(out["cnpj"]):
                erros["cnpj"] = "CNPJ inválido"
            else:
                out["cnpj"] = core.so_digitos(out["cnpj"])
                if cnpj_em_uso(con, out["cnpj"], ident):
                    erros["cnpj"] = "CNPJ já cadastrado"
        if "cpf" in out:
            if not core.cpf_valido(out["cpf"]):
                erros["cpf"] = "CPF inválido"
            else:
                out["cpf"] = core.so_digitos(out["cpf"])
                dup = con.execute("SELECT id FROM motoristas WHERE cpf=?", (out["cpf"],)).fetchone()
                if dup and (not atual or dup["id"] != atual["id"]):
                    erros["cpf"] = "CPF já cadastrado"
        if "email" in out and not core.email_valido(out["email"]):
            erros["email"] = "E-mail inválido"
        if "telefone" in out:
            if core.telefone_valido(out["telefone"]):
                out["telefone"] = core.so_digitos(out["telefone"])
            else:
                erros["telefone"] = "Telefone inválido"
        if "cep" in out:
            if core.cep_valido(out["cep"]):
                out["cep"] = core.so_digitos(out["cep"])
            else:
                erros["cep"] = "CEP inválido"
        for campo in ("rntrc_validade", "cnh_validade"):
            if campo in out:
                try:
                    dt = date.fromisoformat(str(out[campo])[:10])
                    out[campo] = dt.isoformat()
                    if campo == "cnh_validade" and dt <= date.today():
                        erros[campo] = "CNH vencida"
                except ValueError:
                    erros[campo] = "Data inválida (use AAAA-MM-DD)"
        if "cnh_categoria" in out and out["cnh_categoria"] not in core.CATEGORIAS_CNH:
            erros["cnh_categoria"] = "Categoria inválida (A, B, C, D ou E)"
        if "placa" in out:
            if core.placa_valida(out["placa"]):
                out["placa"] = core.normalizar_placa(out["placa"])
                dup = con.execute("SELECT id FROM veiculos WHERE placa=?", (out["placa"],)).fetchone()
                if dup and (not atual or dup["id"] != atual["id"]):
                    erros["placa"] = "Placa já cadastrada"
            else:
                erros["placa"] = "Placa inválida"
        if "tipo" in out and out["tipo"] not in core.TIPOS_VEICULO:
            erros["tipo"] = "Tipo inválido (" + ", ".join(core.TIPOS_VEICULO) + ")"
        if "capacidade_kg" in out:
            try:
                cap = float(out["capacidade_kg"])
                if cap <= 0:
                    erros["capacidade_kg"] = "Capacidade deve ser maior que zero"
                out["capacidade_kg"] = cap
            except (TypeError, ValueError):
                erros["capacidade_kg"] = "Capacidade inválida"
        if "transportadora_id" in out:
            try:
                tid = int(out["transportadora_id"])
                if not con.execute("SELECT 1 FROM transportadoras WHERE id=?", (tid,)).fetchone():
                    erros["transportadora_id"] = "Transportadora não encontrada"
                out["transportadora_id"] = tid
            except (TypeError, ValueError):
                erros["transportadora_id"] = "Transportadora inválida"
        if erros:
            raise Erro(422, "Dados inválidos", erros)
        return out

    def cad_json(ent, r):
        d = dict(r)
        d["ativo"] = bool(d["ativo"])
        d["status"] = "Ativo" if d["ativo"] else "Inativo"
        if ent == "transportadoras":
            vencido = bool(d["rntrc_validade"]) and d["rntrc_validade"] < date.today().isoformat()
            d["rntrc_vencido"] = vencido
            d["bloqueada_contratacao"] = vencido or not d["ativo"]
        if ent == "veiculos":
            d["capacidade_kg"] = d["capacidade_kg"]
        return d

    def em_viagem_ativa(con, ent, rid):
        col = {"motoristas": "motorista_id", "veiculos": "veiculo_id", "transportadoras": "transportadora_id"}.get(ent)
        return bool(col) and con.execute(f"SELECT 1 FROM viagens WHERE ativa=1 AND {col}=?", (rid,)).fetchone() is not None

    def registrar_cadastro(ent):
        def listar():
            con = db()
            sql = f"SELECT * FROM {ent}"
            if request.args.get("ativo") in ("true", "false"):
                sql += f" WHERE ativo={1 if request.args['ativo'] == 'true' else 0}"
            return jsonify([cad_json(ent, r) for r in con.execute(sql + " ORDER BY id")])

        def obter(rid):
            r = db().execute(f"SELECT * FROM {ent} WHERE id=?", (rid,)).fetchone()
            if not r:
                raise Erro(404, f"{ROTULO[ent]} não encontrado")
            return jsonify(cad_json(ent, r))

        def criar():
            con, dados = db(), validar_cadastro(ent, corpo())
            cols = ",".join(dados)
            cur = con.execute(f"INSERT INTO {ent}({cols}) VALUES({','.join('?' * len(dados))})", tuple(dados.values()))
            con.commit()
            return jsonify(cad_json(ent, con.execute(f"SELECT * FROM {ent} WHERE id=?", (cur.lastrowid,)).fetchone())), 201

        def alterar(rid):
            con = db()
            atual = con.execute(f"SELECT * FROM {ent} WHERE id=?", (rid,)).fetchone()
            if not atual:
                raise Erro(404, f"{ROTULO[ent]} não encontrado")
            novo = {**{c: atual[c] for c in CAD_CONFIG[ent]}, **corpo()}
            dados = validar_cadastro(ent, novo, atual)
            con.execute(f"UPDATE {ent} SET {','.join(c + '=?' for c in dados)} WHERE id=?", (*dados.values(), rid))
            con.commit()
            return jsonify(cad_json(ent, con.execute(f"SELECT * FROM {ent} WHERE id=?", (rid,)).fetchone()))

        def inativar(rid):
            con = db()
            if not con.execute(f"SELECT 1 FROM {ent} WHERE id=?", (rid,)).fetchone():
                raise Erro(404, f"{ROTULO[ent]} não encontrado")
            if em_viagem_ativa(con, ent, rid):
                raise Erro(409, "Cadastro em viagem ativa")
            con.execute(f"UPDATE {ent} SET ativo=0 WHERE id=?", (rid,))
            con.commit()
            return jsonify({"ok": True})

        def ativar(rid):
            con = db()
            if not con.execute(f"SELECT 1 FROM {ent} WHERE id=?", (rid,)).fetchone():
                raise Erro(404, f"{ROTULO[ent]} não encontrado")
            con.execute(f"UPDATE {ent} SET ativo=1 WHERE id=?", (rid,))
            con.commit()
            return jsonify({"ok": True})

        base, tag = f"/api/{ent}", "Cadastros"
        rota(base, ["GET"], f"Lista {ent}", tag, perfis=(ADM, OPE), nome=f"{ent}_listar")(listar)
        rota(base + "/<int:rid>", ["GET"], f"Consulta {ent}", tag, perfis=(ADM, OPE), nome=f"{ent}_obter")(obter)
        rota(base, ["POST"], f"Cadastra {ent}", tag, perfis=(ADM,), corpo=CAD_CORPO[ent], nome=f"{ent}_criar",
             respostas={"201": "Criado", "422": "Dados inválidos"})(criar)
        rota(base + "/<int:rid>", ["PUT"], f"Altera {ent}", tag, perfis=(ADM,), corpo=CAD_CORPO[ent], nome=f"{ent}_alterar")(alterar)
        rota(base + "/<int:rid>/inativar", ["POST"], f"Inativa {ent} (nunca exclui)", tag, perfis=(ADM,), nome=f"{ent}_inativar",
             respostas={"200": "Inativado", "409": "Em viagem ativa"})(inativar)
        rota(base + "/<int:rid>/ativar", ["POST"], f"Reativa {ent}", tag, perfis=(ADM,), nome=f"{ent}_ativar")(ativar)

    for _ent in CAD_CONFIG:
        registrar_cadastro(_ent)

    # ------------------------------------------------------------------ consultas auxiliares de pedido
    def pedido_ou_404(pid):
        p = db().execute("SELECT * FROM pedidos WHERE id=?", (pid,)).fetchone()
        if not p:
            raise Erro(404, "Pedido não encontrado")
        return p

    def viagem_do_pedido(pid):
        return db().execute("SELECT * FROM viagens WHERE pedido_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()

    def doc_ativo(pid, tipo):
        return db().execute("SELECT * FROM documentos WHERE pedido_id=? AND tipo=? AND status IN ('Pendente de envio','Autorizado') "
                            "ORDER BY id DESC LIMIT 1", (pid, tipo)).fetchone()

    def doc_autorizado(pid, tipo):
        return db().execute("SELECT * FROM documentos WHERE pedido_id=? AND tipo=? AND status='Autorizado' ORDER BY id DESC LIMIT 1",
                            (pid, tipo)).fetchone()

    def ciot_da_viagem(vid):
        return db().execute("SELECT * FROM ciots WHERE viagem_id=?", (vid,)).fetchone()

    def prazo_cancel(d):
        return cfg.prazo_cancelar_cte_s if d["tipo"] == "CTE" else cfg.prazo_cancelar_mdfe_s

    def doc_json(d, v=None):
        restante = None
        if d["status"] == "Autorizado" and d["autorizado_em"]:
            restante = prazo_cancel(d) - passou_s(d["autorizado_em"])
        acoes = {
            "reenviar": d["status"] in ("Pendente de envio", "Rejeitado"),
            "cancelar": d["status"] == "Autorizado" and restante is not None and restante > 0
                        and (d["tipo"] == "CTE" or (v is not None and v["estado"] == "Aguardando saída")),
            "encerrar": d["tipo"] == "MDFE" and d["status"] == "Autorizado" and v is not None and v["estado"] == "Entregue",
        }
        return {"id": d["id"], "tipo": "CT-e" if d["tipo"] == "CTE" else "MDF-e", "tipo_codigo": d["tipo"], "pedido_id": d["pedido_id"],
                "status": d["status"], "chave": d["chave"], "protocolo": d["protocolo"], "rejeicao_codigo": d["rejeicao_codigo"],
                "rejeicao_motivo": d["rejeicao_motivo"], "icms_aliquota": d["icms_aliquota"], "icms_valor": money(d["icms_centavos"]),
                "autorizado_em": d["autorizado_em"], "criado_em": d["criado_em"], "correcao": d["correcao"],
                "prazo_cancelamento_restante_s": None if restante is None else round(restante),
                "acoes": acoes}

    def viagem_json(v, completo=True):
        con = db()
        p = con.execute("SELECT * FROM pedidos WHERE id=?", (v["pedido_id"],)).fetchone()
        t = con.execute("SELECT id,razao_social FROM transportadoras WHERE id=?", (v["transportadora_id"],)).fetchone()
        m = con.execute("SELECT id,nome FROM motoristas WHERE id=?", (v["motorista_id"],)).fetchone()
        ve = con.execute("SELECT id,placa,tipo FROM veiculos WHERE id=?", (v["veiculo_id"],)).fetchone()
        ciot = ciot_da_viagem(v["id"])
        atraso = v["estado"] == "Em trânsito" and v["previsao_chegada_em"] is not None and time.time() > v["previsao_chegada_em"]
        mdfe = doc_autorizado(v["pedido_id"], "MDFE")
        comp = con.execute("SELECT * FROM comprovantes WHERE viagem_id=?", (v["id"],)).fetchone()
        out = {"id": v["id"], "numero": f"VIA-{v['id']:04d}", "pedido_id": v["pedido_id"], "estado": v["estado"], "ativa": bool(v["ativa"]),
               "frete": money(v["frete_centavos"]), "transportadora": dict(t), "motorista": dict(m), "veiculo": dict(ve),
               "ciot": ciot["numero"] if ciot else None, "iniciada_em": iso_epoch(v["iniciada_em"]),
               "previsao_chegada_em": iso_epoch(v["previsao_chegada_em"]), "chegada_em": iso_epoch(v["chegada_em"]),
               "atraso": bool(atraso), "pedido_status": p["status"],
               "acoes": {"iniciar": v["estado"] == "Aguardando saída" and mdfe is not None and v["ativa"] == 1,
                         "confirmar_entrega": v["estado"] == "No ponto de entrega",
                         "registrar_ocorrencia": v["estado"] != "Entregue" and p["status"] != "Cancelado"}}
        if completo:
            out["ocorrencias"] = [dict(o) for o in con.execute("SELECT tipo,descricao,criado_em,usuario FROM ocorrencias WHERE viagem_id=? ORDER BY id", (v["id"],))]
            out["eventos"] = [dict(e) for e in con.execute("SELECT texto,origem,criado_em FROM eventos WHERE pedido_id=? ORDER BY id", (v["pedido_id"],))]
            out["comprovante"] = None if not comp else {
                "recebedor_nome": comp["recebedor_nome"], "recebedor_cpf": comp["recebedor_cpf"], "divergencia": comp["divergencia"],
                "descricao": comp["descricao"], "desconto": money(comp["desconto_centavos"]), "criado_em": comp["criado_em"]}
        return out

    def pedido_json(p, completo=True):
        con = db()
        emb = con.execute("SELECT id,razao_social FROM embarcadores WHERE id=?", (p["embarcador_id"],)).fetchone()
        v = viagem_do_pedido(p["id"])
        cte_ativo = doc_ativo(p["id"], "CTE")
        cte_ok = doc_autorizado(p["id"], "CTE")
        mdfe_ativo = doc_ativo(p["id"], "MDFE")
        ciot = ciot_da_viagem(v["id"]) if v else None
        out = {"id": p["id"], "embarcador": dict(emb), "origem": p["origem"], "destino": p["destino"], "distancia_km": p["distancia_km"],
               "produto": p["produto"], "peso_kg": p["peso_kg"], "valor_carga": money(p["valor_carga_centavos"]), "nfe_chave": p["nfe_chave"],
               "status": p["status"], "criado_em": p["criado_em"], "motivo_cancelamento": p["motivo_cancelamento"],
               "viagem": viagem_json(v, completo=False) if v else None,
               "acoes": {"editar": p["status"] == "Rascunho",
                         "vincular_nfe": p["status"] in ("Rascunho", "Aguardando contratação"),
                         "enviar_contratacao": p["status"] == "Rascunho" and bool(p["nfe_chave"]),
                         "contratar": p["status"] == "Aguardando contratação",
                         "alterar_frete": p["status"] == "Contratado" and v is not None and cte_ativo is None,
                         "emitir_cte": p["status"] == "Contratado" and cte_ativo is None and bool(p["nfe_chave"]),
                         "gerar_ciot": cte_ok is not None and v is not None and ciot is None,
                         "emitir_mdfe": cte_ok is not None and ciot is not None and mdfe_ativo is None and p["status"] == "Contratado",
                         "cancelar": p["status"] in ("Rascunho", "Aguardando contratação", "Contratado") and cte_ok is None}}
        if completo:
            out["documentos"] = [doc_json(d, v) for d in con.execute("SELECT * FROM documentos WHERE pedido_id=? ORDER BY id", (p["id"],))]
            out["eventos"] = [dict(e) for e in con.execute("SELECT texto,origem,criado_em FROM eventos WHERE pedido_id=? ORDER BY id", (p["id"],))]
            out["viagem"] = viagem_json(v) if v else None
        return out

    def validar_nfe(chave):
        c = core.so_digitos(chave) if str(chave).strip().isdigit() else str(chave).strip()
        if len(c) != 44 or not c.isdigit():
            raise Erro(422, "Dados inválidos", {"nfe_chave": "Chave deve ter 44 dígitos numéricos"})
        if not core.chave_valida(c):
            raise Erro(422, "Dados inválidos", {"nfe_chave": "Dígito verificador inválido"})
        return c

    def nfe_livre(chave, pid):
        outro = db().execute("SELECT id FROM pedidos WHERE nfe_chave=? AND id<>? AND status<>'Cancelado'", (chave, pid)).fetchone()
        if outro:
            raise Erro(409, f"NF-e já vinculada ao pedido {outro['id']}", {"nfe_chave": f"NF-e já vinculada ao pedido {outro['id']}"})

    # ------------------------------------------------------------------ pedidos (RN-PED)
    @rota("/api/pedidos", ["GET"], "Lista pedidos (filtro opcional ?status=)", "Pedidos", perfis=LEITURA_OPERACAO)
    def pedidos_listar():
        sql, args = "SELECT * FROM pedidos", []
        if request.args.get("status"):
            sql += " WHERE status=?"
            args.append(request.args["status"])
        return jsonify([pedido_json(p, completo=False) for p in db().execute(sql + " ORDER BY id DESC", args)])

    @rota("/api/pedidos/<int:pid>", ["GET"], "Consulta um pedido com documentos, viagem e linha do tempo", "Pedidos", perfis=LEITURA_OPERACAO)
    def pedido_obter(pid):
        return jsonify(pedido_json(pedido_ou_404(pid)))

    CORPO_PEDIDO = {"embarcador_id": "integer*", "origem": "string*", "destino": "string*", "distancia_km": "number*",
                    "produto": "string*", "peso_kg": "number*", "valor_carga": "number*", "nfe_chave": "string"}

    def validar_pedido(d):
        con, erros = db(), {}
        for c in ("embarcador_id", "origem", "destino", "distancia_km", "produto", "peso_kg", "valor_carga"):
            if vazio(d.get(c)):
                erros[c] = "Campo obrigatório"
        out = {}
        if "embarcador_id" not in erros:
            e = con.execute("SELECT * FROM embarcadores WHERE id=?", (d["embarcador_id"],)).fetchone() if str(d["embarcador_id"]).isdigit() else None
            if not e or not e["ativo"]:
                erros["embarcador_id"] = "Embarcador inválido ou inativo"
            out["embarcador_id"] = int(d["embarcador_id"]) if e else None
        for c in ("origem", "destino"):
            if c not in erros:
                if not core.parse_local(d[c]):
                    erros[c] = "Use o formato Cidade/UF (ex.: São Paulo/SP)"
        if not erros.get("origem") and not erros.get("destino") and "origem" in d and "destino" in d:
            if d["origem"].strip().lower() == d["destino"].strip().lower():
                erros["destino"] = "Origem e destino devem ser diferentes"
        for campo, rotulo in (("distancia_km", "Distância"), ("peso_kg", "Peso")):
            if campo not in erros:
                try:
                    v = float(d[campo])
                    if v <= 0:
                        erros[campo] = f"{rotulo} deve ser maior que zero"
                    out[campo] = v
                except (TypeError, ValueError):
                    erros[campo] = f"{rotulo} inválido"
        if "valor_carga" not in erros:
            try:
                c = core.centavos(d["valor_carga"])
                if c <= 0:
                    erros["valor_carga"] = "Valor da carga deve ser maior que zero"
                out["valor_carga_centavos"] = c
            except ValueError:
                erros["valor_carga"] = "Valor da carga inválido"
        if erros:
            raise Erro(422, "Dados inválidos", erros)
        out.update({"origem": d["origem"].strip(), "destino": d["destino"].strip(), "produto": str(d["produto"]).strip()})
        return out

    @rota("/api/pedidos", ["POST"], "Cria um pedido (Rascunho)", "Pedidos", perfis=(OPE,), corpo=CORPO_PEDIDO,
          respostas={"201": "Pedido criado", "422": "Dados inválidos"}, nome="pedidos_criar")
    def pedido_criar():
        con, d = db(), corpo()
        dados = validar_pedido(d)
        chave = None
        if not vazio(d.get("nfe_chave")):
            chave = validar_nfe(d["nfe_chave"])
            nfe_livre(chave, 0)
        cur = con.execute(
            "INSERT INTO pedidos(embarcador_id,origem,destino,distancia_km,produto,peso_kg,valor_carga_centavos,nfe_chave,criado_em) VALUES(?,?,?,?,?,?,?,?,?)",
            (dados["embarcador_id"], dados["origem"], dados["destino"], dados["distancia_km"], dados["produto"], dados["peso_kg"],
             dados["valor_carga_centavos"], chave, agora_iso()))
        evento(con, cur.lastrowid, None, "Pedido criado (Rascunho)", quem())
        con.commit()
        return jsonify(pedido_json(pedido_ou_404(cur.lastrowid))), 201

    @rota("/api/pedidos/<int:pid>", ["PUT"], "Altera um pedido em Rascunho", "Pedidos", perfis=(OPE,), corpo=CORPO_PEDIDO)
    def pedido_alterar(pid):
        con, p = db(), pedido_ou_404(pid)
        if p["status"] != "Rascunho":
            raise Erro(409, "Só é possível alterar pedidos em Rascunho")
        base = {"embarcador_id": p["embarcador_id"], "origem": p["origem"], "destino": p["destino"], "distancia_km": p["distancia_km"],
                "produto": p["produto"], "peso_kg": p["peso_kg"], "valor_carga": p["valor_carga_centavos"] / 100}
        dados = validar_pedido({**base, **corpo()})
        con.execute("UPDATE pedidos SET embarcador_id=?,origem=?,destino=?,distancia_km=?,produto=?,peso_kg=?,valor_carga_centavos=? WHERE id=?",
                    (dados["embarcador_id"], dados["origem"], dados["destino"], dados["distancia_km"], dados["produto"], dados["peso_kg"],
                     dados["valor_carga_centavos"], pid))
        con.commit()
        return jsonify(pedido_json(pedido_ou_404(pid)))

    @rota("/api/pedidos/<int:pid>/nfe", ["POST"], "Vincula a NF-e (chave de 44 dígitos) ao pedido", "Pedidos", perfis=(OPE,),
          corpo={"nfe_chave": "string*"}, respostas={"200": "Vinculada", "409": "NF-e em outro pedido", "422": "Chave inválida"})
    def pedido_nfe(pid):
        con, p, d = db(), pedido_ou_404(pid), corpo()
        if p["status"] not in ("Rascunho", "Aguardando contratação"):
            raise Erro(409, "A NF-e só pode ser vinculada antes da contratação")
        exigir(d, ["nfe_chave"])
        chave = validar_nfe(d["nfe_chave"])
        nfe_livre(chave, pid)
        con.execute("UPDATE pedidos SET nfe_chave=? WHERE id=?", (chave, pid))
        evento(con, pid, None, "NF-e vinculada", quem())
        con.commit()
        return jsonify(pedido_json(pedido_ou_404(pid)))

    def enviar_contratacao(pid, usuario):
        con, p = db(), pedido_ou_404(pid)
        if p["status"] != "Rascunho":
            raise Erro(409, "Transição de estado inválida")
        if not p["nfe_chave"]:
            raise Erro(409, "Vincule uma NF-e antes de enviar")
        con.execute("UPDATE pedidos SET status='Aguardando contratação' WHERE id=?", (pid,))
        evento(con, pid, None, "Pedido enviado para contratação", usuario)
        con.commit()

    @rota("/api/pedidos/<int:pid>/enviar", ["POST"], "Envia o pedido para contratação", "Pedidos", perfis=(OPE,))
    def pedido_enviar(pid):
        enviar_contratacao(pid, quem())
        return jsonify(pedido_json(pedido_ou_404(pid)))

    def cancelar_pedido(pid, motivo, usuario):
        con, p = db(), pedido_ou_404(pid)
        if p["status"] not in ("Rascunho", "Aguardando contratação", "Contratado"):
            raise Erro(409, "Transição de estado inválida")
        if doc_autorizado(pid, "CTE"):
            raise Erro(409, "Cancele o CT-e antes de cancelar o pedido")
        con.execute("UPDATE pedidos SET status='Cancelado', motivo_cancelamento=? WHERE id=?", (motivo, pid))
        con.execute("UPDATE documentos SET status='Cancelado' WHERE pedido_id=? AND status IN ('Pendente de envio','Rejeitado')", (pid,))
        con.execute("UPDATE viagens SET ativa=0 WHERE pedido_id=?", (pid,))
        evento(con, pid, None, f"Pedido cancelado. Motivo: {motivo}", usuario)
        con.commit()

    @rota("/api/pedidos/<int:pid>/cancelar", ["POST"], "Cancela o pedido (antes do CT-e autorizado)", "Pedidos", perfis=(OPE,),
          corpo={"motivo": "string*"}, respostas={"200": "Cancelado", "409": "Não pode cancelar"})
    def pedido_cancelar(pid):
        d = corpo()
        exigir(d, ["motivo"])
        cancelar_pedido(pid, str(d["motivo"]).strip(), quem())
        return jsonify(pedido_json(pedido_ou_404(pid)))

    @rota("/api/pedidos/<int:pid>/status", ["PATCH"], "Muda o status do pedido (só transições válidas)", "Pedidos", perfis=(OPE,),
          corpo={"status": "string*", "motivo": "string"}, respostas={"200": "Alterado", "409": "Transição inválida"})
    def pedido_status(pid):
        d = corpo()
        exigir(d, ["status"])
        pedido_ou_404(pid)
        if d["status"] == "Aguardando contratação":
            enviar_contratacao(pid, quem())
        elif d["status"] == "Cancelado":
            cancelar_pedido(pid, d.get("motivo") or "Cancelado via API", quem())
        else:
            raise Erro(409, "Transição de estado inválida")
        return jsonify(pedido_json(pedido_ou_404(pid)))

    # ------------------------------------------------------------------ contratação (RN-CON)
    def viagem_ativa_de(coluna, rid):
        return db().execute(f"SELECT 1 FROM viagens WHERE ativa=1 AND {coluna}=?", (rid,)).fetchone() is not None

    def validar_frete(p, tipo_veiculo, frete):
        try:
            frete_c = core.centavos(frete)
        except ValueError:
            raise Erro(422, "Dados inválidos", {"frete": "Frete inválido"})
        piso = core.piso_centavos(p["distancia_km"], tipo_veiculo)
        if frete_c < piso:
            raise Erro(409, f"Frete abaixo do piso mínimo. Valor mínimo: {core.brl(piso)}", {"frete": f"Mínimo {core.brl(piso)}"})
        return frete_c

    @rota("/api/pedidos/<int:pid>/contratar", ["POST"], "Contrata transportadora, motorista e veículo", "Contratação", perfis=(OPE,),
          corpo={"transportadora_id": "integer*", "motorista_id": "integer*", "veiculo_id": "integer*", "frete": "number*"},
          respostas={"200": "Contratado", "409": "Regra de contratação violada", "422": "Dados inválidos"})
    def contratar(pid):
        con, p, d = db(), pedido_ou_404(pid), corpo()
        if p["status"] != "Aguardando contratação":
            raise Erro(409, "O pedido não está aguardando contratação")
        exigir(d, ["transportadora_id", "motorista_id", "veiculo_id", "frete"])
        t = con.execute("SELECT * FROM transportadoras WHERE id=?", (d["transportadora_id"],)).fetchone()
        m = con.execute("SELECT * FROM motoristas WHERE id=?", (d["motorista_id"],)).fetchone()
        v = con.execute("SELECT * FROM veiculos WHERE id=?", (d["veiculo_id"],)).fetchone()
        if not t or not m or not v:
            raise Erro(422, "Transportadora, motorista ou veículo não encontrado")
        if not t["ativo"]:
            raise Erro(409, "Transportadora inativa")
        if not t["rntrc_validade"] or t["rntrc_validade"] < date.today().isoformat():
            raise Erro(409, "RNTRC vencido")
        if m["transportadora_id"] != t["id"]:
            raise Erro(409, "Motorista não pertence à transportadora")
        if v["transportadora_id"] != t["id"]:
            raise Erro(409, "Veículo não pertence à transportadora")
        if not m["ativo"]:
            raise Erro(409, "Motorista inativo")
        if not v["ativo"]:
            raise Erro(409, "Veículo inativo")
        if m["cnh_validade"] and m["cnh_validade"] <= date.today().isoformat():
            raise Erro(409, "CNH vencida")
        if m["cnh_categoria"] not in core.CNH_POR_VEICULO[v["tipo"]]:
            raise Erro(409, "Categoria da CNH incompatível com o veículo")
        if p["peso_kg"] > v["capacidade_kg"]:
            raise Erro(409, "Peso acima da capacidade do veículo")
        frete_c = validar_frete(p, v["tipo"], d["frete"])
        if viagem_ativa_de("motorista_id", m["id"]):
            raise Erro(409, "Motorista em viagem ativa")
        if viagem_ativa_de("veiculo_id", v["id"]):
            raise Erro(409, "Veículo em viagem ativa")
        cur = con.execute("INSERT INTO viagens(pedido_id,transportadora_id,motorista_id,veiculo_id,frete_centavos) VALUES(?,?,?,?,?)",
                          (pid, t["id"], m["id"], v["id"], frete_c))
        con.execute("UPDATE pedidos SET status='Contratado' WHERE id=?", (pid,))
        evento(con, pid, cur.lastrowid, f"Contratação confirmada (VIA-{cur.lastrowid:04d}, frete {core.brl(frete_c)})", quem())
        con.commit()
        return jsonify(pedido_json(pedido_ou_404(pid)))

    @rota("/api/pedidos/<int:pid>/frete", ["POST"], "Altera o frete (antes do CT-e)", "Contratação", perfis=(OPE,), corpo={"frete": "number*"},
          respostas={"200": "Alterado", "409": "Frete bloqueado"})
    def alterar_frete(pid):
        con, p, d = db(), pedido_ou_404(pid), corpo()
        v = viagem_do_pedido(pid)
        if p["status"] != "Contratado" or not v:
            raise Erro(409, "O frete só pode ser alterado em pedidos contratados")
        if doc_ativo(pid, "CTE"):
            raise Erro(409, "Frete não pode ser alterado após o CT-e")
        exigir(d, ["frete"])
        tipo = con.execute("SELECT tipo FROM veiculos WHERE id=?", (v["veiculo_id"],)).fetchone()["tipo"]
        novo = validar_frete(p, tipo, d["frete"])
        con.execute("UPDATE viagens SET frete_centavos=? WHERE id=?", (novo, v["id"]))
        evento(con, pid, v["id"], f"Frete alterado de {core.brl(v['frete_centavos'])} para {core.brl(novo)}", quem())
        con.commit()
        return jsonify(pedido_json(pedido_ou_404(pid)))

    # ------------------------------------------------------------------ documentos (RN-EXE)
    def respostar(pid, msg, status=200):
        corpo_ = pedido_json(pedido_ou_404(pid))
        corpo_["mensagem"] = msg
        return jsonify(corpo_), status

    @rota("/api/pedidos/<int:pid>/cte", ["POST"], "Emite o CT-e e envia à SEFAZ simulada", "Documentos", perfis=(OPE,),
          respostas={"201": "CT-e criado (veja o status)", "409": "Regra violada"})
    def emitir_cte(pid):
        con, p = db(), pedido_ou_404(pid)
        v = viagem_do_pedido(pid)
        if doc_ativo(pid, "CTE"):
            raise Erro(409, "Já existe CT-e ativo")
        if p["status"] != "Contratado" or not v:
            raise Erro(409, "Pedido não contratado")
        if not p["nfe_chave"]:
            raise Erro(409, "NF-e não vinculada")
        doc_id = servicos.novo_documento(con, p, v, "CTE")
        con.commit()
        msg = servicos.enviar_documento(cfg, con, doc_id, quem())
        return respostar(pid, msg, 201)

    @rota("/api/pedidos/<int:pid>/ciot", ["POST"], "Gera o CIOT da viagem", "Documentos", perfis=(OPE,),
          respostas={"201": "CIOT gerado", "409": "Regra violada", "422": "Recusado pelo serviço"})
    def gerar_ciot(pid):
        con, p = db(), pedido_ou_404(pid)
        v = viagem_do_pedido(pid)
        if not v or not doc_autorizado(pid, "CTE"):
            raise Erro(409, "CT-e autorizado é obrigatório")
        if ciot_da_viagem(v["id"]):
            raise Erro(409, "CIOT já gerado")
        m = con.execute("SELECT ativo FROM motoristas WHERE id=?", (v["motorista_id"],)).fetchone()
        ve = con.execute("SELECT ativo FROM veiculos WHERE id=?", (v["veiculo_id"],)).fetchone()
        if not m["ativo"] or not ve["ativo"]:
            raise Erro(409, "Motorista e veículo precisam estar ativos")
        resp = chamar(cfg, "POST", "/ciot/gerar", {"viagem": f"VIA-{v['id']:04d}"})
        if resp.get("indisponivel"):
            raise Erro(503, "Serviço de CIOT indisponível")
        if resp.get("status") != "gerado":
            raise Erro(422, f"CIOT recusado: {resp.get('motivo')}")
        con.execute("INSERT INTO ciots(viagem_id,numero,criado_em) VALUES(?,?,?)", (v["id"], resp["ciot"], agora_iso()))
        evento(con, pid, v["id"], f"CIOT gerado ({resp['ciot']})", quem())
        con.commit()
        return respostar(pid, f"CIOT {resp['ciot']} gerado.", 201)

    @rota("/api/pedidos/<int:pid>/mdfe", ["POST"], "Emite o MDF-e e envia à SEFAZ simulada", "Documentos", perfis=(OPE,),
          respostas={"201": "MDF-e criado (veja o status)", "409": "Regra violada"})
    def emitir_mdfe(pid):
        con, p = db(), pedido_ou_404(pid)
        v = viagem_do_pedido(pid)
        if not v or not doc_autorizado(pid, "CTE"):
            raise Erro(409, "CT-e autorizado é obrigatório")
        if not ciot_da_viagem(v["id"]):
            raise Erro(409, "CIOT é obrigatório")
        if doc_ativo(pid, "MDFE"):
            raise Erro(409, "Já existe MDF-e ativo")
        doc_id = servicos.novo_documento(con, p, v, "MDFE")
        con.commit()
        msg = servicos.enviar_documento(cfg, con, doc_id, quem())
        return respostar(pid, msg, 201)

    def documento_ou_404(did):
        d = db().execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
        if not d:
            raise Erro(404, "Documento não encontrado")
        return d

    @rota("/api/documentos/<int:did>", ["GET"], "Consulta um documento", "Documentos", perfis=LEITURA_OPERACAO)
    def documento_obter(did):
        d = documento_ou_404(did)
        return jsonify(doc_json(d, viagem_do_pedido(d["pedido_id"])))

    @rota("/api/documentos/<int:did>/xml", ["GET"], "XML do documento", "Documentos", perfis=LEITURA_OPERACAO)
    def documento_xml(did):
        d = documento_ou_404(did)
        return app.response_class(d["xml"], mimetype="application/xml")

    @rota("/api/documentos/<int:did>/reenviar", ["POST"], "Reenvia documento pendente ou rejeitado (sem duplicar)", "Documentos", perfis=(OPE,),
          corpo={"correcao": "string"}, respostas={"200": "Reenviado", "409": "Documento não pode ser reenviado"})
    def documento_reenviar(did):
        con, d, dados = db(), documento_ou_404(did), corpo()
        if d["status"] not in ("Pendente de envio", "Rejeitado"):
            raise Erro(409, "Só documentos pendentes de envio ou rejeitados podem ser reenviados")
        if d["status"] == "Rejeitado":
            if vazio(dados.get("correcao")):
                raise Erro(422, "Dados inválidos", {"correcao": "Informe a correção realizada"})
            con.execute("UPDATE documentos SET correcao=? WHERE id=?", (str(dados["correcao"]).strip(), did))
            con.commit()
        if d["tipo"] == "CTE" and doc_ativo(d["pedido_id"], "CTE") and d["status"] == "Rejeitado":
            raise Erro(409, "Já existe CT-e ativo")
        msg = servicos.enviar_documento(cfg, con, did, quem())
        return respostar(d["pedido_id"], msg)

    @rota("/api/documentos/<int:did>/cancelar", ["POST"], "Cancela CT-e (7 dias) ou MDF-e (24 horas)", "Documentos", perfis=(OPE,),
          corpo={"motivo": "string*"}, respostas={"200": "Cancelado", "409": "Fora do prazo ou bloqueado", "503": "SEFAZ indisponível"})
    def documento_cancelar(did):
        con, d, dados = db(), documento_ou_404(did), corpo()
        v = viagem_do_pedido(d["pedido_id"])
        rotulo = "CT-e" if d["tipo"] == "CTE" else "MDF-e"
        if d["status"] == "Encerrado":
            raise Erro(409, "MDF-e já encerrado")
        if d["status"] != "Autorizado":
            raise Erro(409, f"Só é possível cancelar {rotulo} autorizado")
        if passou_s(d["autorizado_em"]) > prazo_cancel(d):
            raise Erro(409, "Prazo de cancelamento expirado")
        if d["tipo"] == "CTE" and doc_ativo(d["pedido_id"], "MDFE"):
            raise Erro(409, "Cancele o MDF-e antes")
        if d["tipo"] == "MDFE" and v and v["estado"] != "Aguardando saída":
            raise Erro(409, "Viagem já iniciada: o MDF-e não pode ser cancelado")
        exigir(dados, ["motivo"])
        res, resp = servicos.enviar_evento(cfg, "/sefaz/cte/cancelamento" if d["tipo"] == "CTE" else "/sefaz/mdfe/cancelamento",
                                           d["chave"], dados["motivo"])
        if res == "indisponivel":
            raise Erro(503, "SEFAZ indisponível. Tente novamente mais tarde.")
        if res == "rejeitado":
            raise Erro(422, f"Cancelamento rejeitado: {resp.get('motivo')}")
        con.execute("UPDATE documentos SET status='Cancelado', motivo=? WHERE id=?", (str(dados["motivo"]).strip(), did))
        con.execute("UPDATE pedidos SET status='Contratado' WHERE id=? AND status='Em execução'", (d["pedido_id"],))
        evento(con, d["pedido_id"], v["id"] if v else None, f"{rotulo} cancelado. Motivo: {dados['motivo']}", quem())
        con.commit()
        return respostar(d["pedido_id"], f"{rotulo} cancelado.")

    @rota("/api/documentos/<int:did>/encerrar", ["POST"], "Encerra o MDF-e (após a entrega comprovada)", "Documentos", perfis=(OPE,),
          respostas={"200": "Encerrado", "409": "Entrega não comprovada"})
    def documento_encerrar(did):
        con, d = db(), documento_ou_404(did)
        v = viagem_do_pedido(d["pedido_id"])
        if d["tipo"] != "MDFE" or d["status"] != "Autorizado":
            raise Erro(409, "Só MDF-e autorizado pode ser encerrado")
        if not v or v["estado"] != "Entregue":
            raise Erro(409, "Entrega não comprovada")
        res, resp = servicos.enviar_evento(cfg, "/sefaz/mdfe/encerramento", d["chave"])
        if res == "indisponivel":
            raise Erro(503, "SEFAZ indisponível. Tente novamente mais tarde.")
        if res == "rejeitado":
            raise Erro(422, f"Encerramento rejeitado: {resp.get('motivo')}")
        con.execute("UPDATE documentos SET status='Encerrado' WHERE id=?", (did,))
        evento(con, d["pedido_id"], v["id"], "MDF-e encerrado", quem())
        con.commit()
        return respostar(d["pedido_id"], "MDF-e encerrado.")

    # ------------------------------------------------------------------ viagem (RN-MON) e comprovação (RN-COM)
    def viagem_ou_404(vid):
        v = db().execute("SELECT * FROM viagens WHERE id=?", (vid,)).fetchone()
        if not v:
            raise Erro(404, "Viagem não encontrada")
        return v

    @rota("/api/viagens", ["GET"], "Lista viagens", "Viagem", perfis=LEITURA_OPERACAO)
    def viagens_listar():
        return jsonify([viagem_json(v, completo=False) for v in db().execute("SELECT * FROM viagens ORDER BY id DESC")])

    @rota("/api/viagens/<int:vid>", ["GET"], "Consulta a viagem, ocorrências e linha do tempo", "Viagem", perfis=LEITURA_OPERACAO)
    def viagem_obter(vid):
        return jsonify(viagem_json(viagem_ou_404(vid)))

    @rota("/api/viagens/<int:vid>/iniciar", ["POST"], "Inicia a viagem (exige MDF-e autorizado)", "Viagem", perfis=(OPE,),
          respostas={"200": "Em trânsito", "409": "Sem MDF-e autorizado"})
    def viagem_iniciar(vid):
        servicos.iniciar_viagem(db(), viagem_ou_404(vid), quem())
        return jsonify(viagem_json(viagem_ou_404(vid)))

    @rota("/api/viagens/<int:vid>/estado", ["PATCH"], "Muda o estado da viagem (só transições válidas)", "Viagem", perfis=(OPE,),
          corpo={"estado": "string*"}, respostas={"200": "Alterado", "409": "Transição inválida"})
    def viagem_estado(vid):
        d = corpo()
        exigir(d, ["estado"])
        v = viagem_ou_404(vid)
        if d["estado"] != "Em trânsito":
            raise Erro(409, "Transição de estado inválida")
        servicos.iniciar_viagem(db(), v, quem())
        return jsonify(viagem_json(viagem_ou_404(vid)))

    @rota("/api/viagens/<int:vid>/ocorrencias", ["POST"], "Registra uma ocorrência (atraso, avaria ou parada)", "Viagem", perfis=(OPE,),
          corpo={"tipo": "string*", "descricao": "string*"}, respostas={"201": "Registrada", "422": "Dados inválidos"})
    def ocorrencia(vid):
        con, v, d = db(), viagem_ou_404(vid), corpo()
        exigir(d, ["tipo", "descricao"])
        if d["tipo"] not in ("Atraso", "Avaria", "Parada"):
            raise Erro(422, "Dados inválidos", {"tipo": "Tipo inválido (Atraso, Avaria ou Parada)"})
        if v["estado"] == "Entregue":
            raise Erro(409, "A viagem já foi entregue")
        con.execute("INSERT INTO ocorrencias(viagem_id,tipo,descricao,criado_em,usuario) VALUES(?,?,?,?,?)",
                    (vid, d["tipo"], str(d["descricao"]).strip(), agora_iso(), quem()))
        evento(con, v["pedido_id"], vid, f"Ocorrência registrada: {d['tipo']} — {str(d['descricao']).strip()}", quem())
        con.commit()
        return jsonify(viagem_json(viagem_ou_404(vid))), 201

    @rota("/api/viagens/<int:vid>/comprovante", ["POST"], "Confirma a entrega (comprovante digital)", "Comprovação", perfis=(OPE,),
          corpo={"recebedor_nome": "string*", "recebedor_cpf": "string*", "divergencia": "string", "descricao": "string", "desconto": "number"},
          respostas={"201": "Entrega confirmada", "409": "Viagem fora do ponto de entrega", "422": "Dados inválidos"})
    def comprovante_criar(vid):
        con, v, d = db(), viagem_ou_404(vid), corpo()
        if v["estado"] != "No ponto de entrega":
            raise Erro(409, "Viagem fora do ponto de entrega")
        erros = {}
        if vazio(d.get("recebedor_nome")):
            erros["recebedor_nome"] = "Nome é obrigatório"
        if vazio(d.get("recebedor_cpf")):
            erros["recebedor_cpf"] = "CPF é obrigatório"
        elif not core.cpf_valido(d["recebedor_cpf"]):
            erros["recebedor_cpf"] = "CPF inválido"
        diverg = d.get("divergencia") or "Nenhuma"
        if diverg not in ("Nenhuma", "Avaria", "Falta"):
            erros["divergencia"] = "Divergência inválida (Nenhuma, Avaria ou Falta)"
        desconto_c = 0
        if diverg != "Nenhuma" and vazio(d.get("descricao")) and "divergencia" not in erros:
            erros["descricao"] = "Descreva a divergência"
        if diverg == "Avaria" and not vazio(d.get("desconto")):
            try:
                desconto_c = core.centavos(d["desconto"])
                if desconto_c < 0:
                    erros["desconto"] = "Desconto não pode ser negativo"
                elif desconto_c > v["frete_centavos"]:
                    erros["desconto"] = "Desconto não pode ser maior que o frete"
            except ValueError:
                erros["desconto"] = "Desconto inválido"
        if erros:
            raise Erro(422, next(iter(erros.values())), erros)
        con.execute("INSERT INTO comprovantes(viagem_id,recebedor_nome,recebedor_cpf,divergencia,descricao,desconto_centavos,criado_em) VALUES(?,?,?,?,?,?,?)",
                    (vid, str(d["recebedor_nome"]).strip(), core.so_digitos(d["recebedor_cpf"]), diverg,
                     None if diverg == "Nenhuma" else str(d["descricao"]).strip(), desconto_c, agora_iso()))
        con.execute("UPDATE viagens SET estado='Entregue', ativa=0 WHERE id=?", (vid,))
        evento(con, v["pedido_id"], vid, f"Entrega comprovada (recebedor: {str(d['recebedor_nome']).strip()})", quem())
        con.commit()
        return jsonify(viagem_json(viagem_ou_404(vid))), 201

    @rota("/api/viagens/<int:vid>/comprovante", ["GET"], "Consulta o comprovante de entrega", "Comprovação", perfis=LEITURA_OPERACAO)
    def comprovante_obter(vid):
        c = viagem_json(viagem_ou_404(vid))["comprovante"]
        if not c:
            raise Erro(404, "Comprovante não encontrado")
        return jsonify(c)

    # ------------------------------------------------------------------ pagamento (RN-PAG)
    def valor_a_pagar(v):
        comp = db().execute("SELECT desconto_centavos FROM comprovantes WHERE viagem_id=?", (v["id"],)).fetchone()
        return v["frete_centavos"] - (comp["desconto_centavos"] if comp else 0)

    def pagamento_situacao(v):
        pg = db().execute("SELECT * FROM pagamentos WHERE viagem_id=?", (v["id"],)).fetchone()
        if pg and pg["status"] == "Pago":
            return "Pago", None
        if v["estado"] != "Entregue":
            return "Aguardando entrega", "Entrega não comprovada"
        mdfe = db().execute("SELECT status FROM documentos WHERE pedido_id=? AND tipo='MDFE' ORDER BY id DESC LIMIT 1", (v["pedido_id"],)).fetchone()
        if not mdfe or mdfe["status"] != "Encerrado":
            return "Aguardando encerramento do MDF-e", "MDF-e não encerrado"
        return ("Pendente" if pg else "Pronto para pagar"), None

    def pagamento_json(v):
        sit, bloqueio = pagamento_situacao(v)
        pg = db().execute("SELECT * FROM pagamentos WHERE viagem_id=?", (v["id"],)).fetchone()
        tent = [] if not pg else [{"valor": money(t["valor_centavos"]), "status": t["status"], "motivo": t["motivo"], "criado_em": t["criado_em"]}
                                   for t in db().execute("SELECT * FROM pagamento_tentativas WHERE pagamento_id=? ORDER BY id", (pg["id"],))]
        comp = db().execute("SELECT desconto_centavos FROM comprovantes WHERE viagem_id=?", (v["id"],)).fetchone()
        return {"viagem_id": v["id"], "numero": f"VIA-{v['id']:04d}", "pedido_id": v["pedido_id"], "frete": money(v["frete_centavos"]),
                "desconto": money(comp["desconto_centavos"]) if comp else 0, "valor_a_pagar": money(valor_a_pagar(v)),
                "situacao": sit, "pode_pagar": sit in ("Pronto para pagar", "Pendente"), "motivo_bloqueio": bloqueio, "tentativas": tent}

    @rota("/api/pagamentos", ["GET"], "Lista as viagens e a situação do pagamento", "Pagamento", perfis=(FIN,))
    def pagamentos_listar():
        return jsonify([pagamento_json(v) for v in db().execute(
            "SELECT v.* FROM viagens v JOIN pedidos p ON p.id=v.pedido_id WHERE p.status<>'Cancelado' ORDER BY v.id DESC")])

    @rota("/api/viagens/<int:vid>/pagamento", ["GET"], "Situação do pagamento da viagem", "Pagamento", perfis=(FIN,))
    def pagamento_obter(vid):
        return jsonify(pagamento_json(viagem_ou_404(vid)))

    @rota("/api/viagens/<int:vid>/pagamento", ["POST"], "Libera o pagamento do frete (somente Financeiro)", "Pagamento", perfis=(FIN,),
          respostas={"200": "Resultado do pagamento (Pago ou Pendente)", "409": "Pagamento bloqueado"})
    def pagamento_liberar(vid):
        con, v = db(), viagem_ou_404(vid)
        sit, bloqueio = pagamento_situacao(v)
        if sit == "Pago":
            raise Erro(409, "Viagem já paga")
        if bloqueio:
            raise Erro(409, bloqueio)
        valor_c = valor_a_pagar(v)
        pg = con.execute("SELECT * FROM pagamentos WHERE viagem_id=?", (vid,)).fetchone()
        pg_id = pg["id"] if pg else con.execute("INSERT INTO pagamentos(viagem_id,valor_centavos) VALUES(?,?)", (vid, valor_c)).lastrowid
        con.commit()
        resp = chamar(cfg, "POST", "/pagamento/liberar", {"referencia": f"VIA-{vid:04d}", "valor": valor_c / 100})
        if resp.get("indisponivel"):
            status, motivo, msg = "Falha de comunicação", "O serviço de pagamento não respondeu", "Falha de comunicação com o serviço de pagamento. O pagamento continua pendente."
        elif resp.get("status") == "aprovado":
            status, motivo, msg = "Pago", None, "Pagamento aprovado."
        else:
            status, motivo, msg = "Recusado", resp.get("motivo"), f"Pagamento recusado: {resp.get('motivo')}. O pagamento continua pendente."
        con.execute("INSERT INTO pagamento_tentativas(pagamento_id,valor_centavos,status,motivo,criado_em) VALUES(?,?,?,?,?)",
                    (pg_id, valor_c, status, motivo, agora_iso()))
        if status == "Pago":
            con.execute("UPDATE pagamentos SET status='Pago', valor_centavos=? WHERE id=?", (valor_c, pg_id))
            con.execute("UPDATE pedidos SET status='Concluído' WHERE id=?", (v["pedido_id"],))
            evento(con, v["pedido_id"], vid, f"Pagamento aprovado ({core.brl(valor_c)})", quem())
        else:
            evento(con, v["pedido_id"], vid, f"Pagamento não concluído: {status}", quem())
        con.commit()
        corpo_ = pagamento_json(viagem_ou_404(vid))
        corpo_["mensagem"] = msg
        return jsonify(corpo_)

    # ------------------------------------------------------------------ painel do simulador (RN-SIM)
    def exigir_simulador():
        if not cfg.simulador_ativo:
            raise Erro(404, "Não encontrado")

    def sim(metodo, caminho, corpo_=None):
        r = chamar(cfg, metodo, caminho, corpo_)
        if isinstance(r, dict) and r.get("indisponivel"):
            raise Erro(502, "Simulador indisponível")
        return r

    def sim_rota(regra, metodos, resumo, corpo_=None, nome=None):
        def deco(fn):
            def com_guarda(*a, **k):
                exigir_simulador()
                return fn(*a, **k)
            return rota(regra, metodos, resumo, "Simulador", perfis=(ADM,), corpo=corpo_, nome=nome or fn.__name__)(com_guarda)
        return deco

    @rota("/api/simulador/estado", ["GET"], "Cenários, configuração da viagem e estados de teste", "Simulador", perfis=(ADM,))
    def sim_estado():
        exigir_simulador()
        c = sim("GET", "/control/cenarios")
        return jsonify({"cenarios": c["cenarios"], "proxima": c["proxima"], "rejeicoes": c["rejeicoes"],
                        "viagem": config_viagem(db()), "estados_teste": seed.ESTADOS_TESTE})

    @rota("/api/simulador/cenarios/<servico>", ["PUT"], "Programa o cenário de SEFAZ, CIOT ou pagamento", "Simulador", perfis=(ADM,),
          corpo={"modo": "string*", "codigo": "string", "motivo": "string", "atraso_s": "number", "escopo": "string"})
    def sim_cenario(servico):
        exigir_simulador()
        r = chamar(cfg, "PUT", f"/control/cenarios/{servico}", corpo())
        if isinstance(r, dict) and r.get("indisponivel"):
            raise Erro(502, "Simulador indisponível")
        if "erro" in r:
            raise Erro(422, r["erro"])
        return jsonify(r)

    @rota("/api/simulador/historico", ["GET"], "Histórico das requisições recebidas pelo simulador", "Simulador", perfis=(ADM,))
    def sim_historico():
        exigir_simulador()
        return jsonify(sim("GET", "/control/historico"))

    @rota("/api/simulador/historico", ["DELETE"], "Limpa o histórico do simulador", "Simulador", perfis=(ADM,), nome="sim_historico_limpar")
    def sim_historico_limpar():
        exigir_simulador()
        return jsonify(sim("DELETE", "/control/historico"))

    @rota("/api/simulador/viagem", ["PUT"], "Configura a simulação da viagem (tempos, previsão, aceleração, modo manual)", "Simulador",
          perfis=(ADM,), corpo={"inicio_automatico": "boolean", "espera_inicio_s": "number", "tempo_transito_s": "number",
                                "previsao_chegada_s": "number", "fator_aceleracao": "number", "modo_manual": "boolean"})
    def sim_viagem_config():
        exigir_simulador()
        d, novo, erros = corpo(), {}, {}
        for c in ("espera_inicio_s", "tempo_transito_s", "previsao_chegada_s", "fator_aceleracao"):
            if c in d:
                try:
                    n = float(d[c])
                    if n <= 0:
                        raise ValueError
                    novo[c] = n
                except (TypeError, ValueError):
                    erros[c] = "Deve ser um número maior que zero"
        for c in ("inicio_automatico", "modo_manual"):
            if c in d:
                novo[c] = bool(d[c])
        if erros:
            raise Erro(422, "Dados inválidos", erros)
        return jsonify(gravar_config_viagem(db(), novo))

    @rota("/api/simulador/restaurar", ["POST"], "Restaura o padrão: cenários, histórico e carga de dados", "Simulador", perfis=(ADM,))
    def sim_restaurar():
        exigir_simulador()
        sim("POST", "/control/restaurar")
        con = db()
        email, token = g.usuario["email"], request.headers["Authorization"][7:]
        seed.carga_padrao(con)
        u = con.execute("SELECT id FROM usuarios WHERE email=?", (email,)).fetchone()
        con.execute("INSERT OR REPLACE INTO sessoes(token,usuario_id,ultima_atividade) VALUES(?,?,?)", (token, u["id"], time.time()))
        con.commit()
        return jsonify({"ok": True})

    @rota("/api/simulador/pedidos-teste", ["POST"], "Cria um pedido de teste já em um estado escolhido", "Simulador", perfis=(ADM,),
          corpo={"estado": "string*", "idade_cte_dias": "number", "idade_mdfe_horas": "number", "frete": "number", "peso_kg": "number",
                 "distancia_km": "number", "origem": "string", "destino": "string", "tipo_veiculo": "string", "sem_nfe": "boolean",
                 "com_nfe": "boolean", "divergencia": "string", "desconto": "number", "sem_avanco_automatico": "boolean"},
          respostas={"201": "Pedido de teste criado", "422": "Dados inválidos"})
    def sim_pedido_teste():
        exigir_simulador()
        d = corpo()
        exigir(d, ["estado"])
        opcoes = {k: v for k, v in d.items() if k != "estado"}
        try:
            r = seed.criar_pedido_teste(db(), d["estado"], **opcoes)
        except (ValueError, KeyError) as e:
            db().rollback()
            raise Erro(422, str(e))
        return jsonify(r), 201

    @rota("/api/simulador/viagens/<int:vid>/avancar", ["POST"], "Modo manual: avança a etapa da viagem", "Simulador", perfis=(ADM,),
          respostas={"200": "Etapa avançada", "409": "Modo manual desligado ou sem próxima etapa"})
    def sim_avancar(vid):
        exigir_simulador()
        con, v = db(), viagem_ou_404(vid)
        if not config_viagem(con)["modo_manual"]:
            raise Erro(409, "Modo manual desligado")
        if v["estado"] == "Aguardando saída":
            servicos.iniciar_viagem(con, v, quem())
        elif v["estado"] == "Em trânsito":
            servicos.registrar_chegada(con, v, quem())
        else:
            raise Erro(409, "Não há próxima etapa manual (a entrega exige comprovante)")
        return jsonify(viagem_json(viagem_ou_404(vid)))

    # ------------------------------------------------------------------ interface
    @app.get("/")
    def raiz():
        return send_from_directory(app.static_folder, "index.html")

    kit.registrar_paginas(app)
    if agendador:
        servicos.iniciar_agendador(cfg)
    app.cfg = cfg
    return app
