"""Testes de desenvolvimento: verificam se a construção cumpre as regras de negócio (RN-xxx).

NÃO são a suíte de QA do portfólio (essa fica em tests/). Servem para garantir que o sistema nasce correto.
Rodar:  python -m unittest discover -s dev_tests -v      (ou: pytest dev_tests)
"""
import os
import socket
import tempfile
import threading
import time
import unittest

import requests
from werkzeug.serving import make_server

from app import seed, servicos
from app.api import create_app
from app.core import chave_valida
from app.db import Config, conectar, gravar_config_viagem
from simulator.app import app as sim_app

SENHAS = {"adm": "admin@cargotrail.test", "ope": "operador@cargotrail.test", "fin": "financeiro@cargotrail.test"}
NFE = ["35261011222333000181550010000001001200000004", "35261011222333000181550010000001011200001113"]
ID = {"TRA01": 1, "TRA02": 2, "TRA03": 3, "TRA04": 4, "MOT01": 1, "MOT02": 2, "MOT03": 3, "MOT04": 4, "MOT05": 5,
      "VEI01": 1, "VEI02": 2, "VEI03": 3, "VEI04": 4, "VEI05": 5}


def porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.porta = porta_livre()
        cls.servidor = make_server("127.0.0.1", cls.porta, sim_app, threaded=True)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.sim_url = f"http://127.0.0.1:{cls.porta}"
        cls.dbfile = tempfile.mktemp(suffix=".db")
        cls.cfg = Config(db_path=cls.dbfile, simulador_url=cls.sim_url, servico_timeout_s=2.0, sessao_timeout_s=900,
                         simulador_ativo=True)
        cls.app = create_app(cls.cfg, agendador=False)

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(cls.dbfile + suf):
                os.remove(cls.dbfile + suf)

    def setUp(self):
        self.cfg.sessao_timeout_s = 900
        self.cfg.simulador_ativo = True
        requests.post(self.sim_url + "/control/restaurar")
        c = conectar(self.dbfile)
        seed.carga_padrao(c)
        c.close()
        self.cli = self.app.test_client()
        self.tok = {}
        for k, email in SENHAS.items():
            r = self.cli.post("/api/auth/login", json={"email": email, "senha": "Teste@123"})
            self.tok[k] = r.json["token"]

    # ---- ajudas
    def api(self, papel, metodo, caminho, corpo=None):
        h = {"Authorization": "Bearer " + self.tok[papel]}
        r = getattr(self.cli, metodo)(caminho, json=corpo, headers=h)
        return r.status_code, (r.json if r.is_json else r.get_data(as_text=True))

    def ok(self, papel, metodo, caminho, corpo=None, esperado=(200, 201)):
        s, j = self.api(papel, metodo, caminho, corpo)
        self.assertIn(s, esperado, f"{metodo.upper()} {caminho} -> {s}: {j}")
        return j

    def erro(self, papel, metodo, caminho, corpo=None, status=409, trecho=None):
        s, j = self.api(papel, metodo, caminho, corpo)
        self.assertEqual(s, status, f"{metodo.upper()} {caminho} -> {s}: {j}")
        if trecho:
            self.assertIn(trecho, str(j))
        return j

    def pt(self, estado, **o):
        return self.ok("adm", "post", "/api/simulador/pedidos-teste", {"estado": estado, **o})

    def cenario(self, servico, modo, **o):
        self.ok("adm", "put", f"/api/simulador/cenarios/{servico}", {"modo": modo, **o})

    def pedido(self, pid):
        return self.ok("ope", "get", f"/api/pedidos/{pid}")

    def doc(self, pid, tipo):
        return [d for d in self.pedido(pid)["documentos"] if d["tipo_codigo"] == tipo][-1]

    def conn(self):
        return conectar(self.dbfile)

    def contratar(self, pid, mot=1, vei=1, frete=3500, tra=1):
        return self.api("ope", "post", f"/api/pedidos/{pid}/contratar",
                        {"transportadora_id": tra, "motorista_id": mot, "veiculo_id": vei, "frete": frete})


# ======================================================================= ACESSO
class TestAcesso(Base):
    def test_login_valido_e_perfis(self):
        j = self.ok("adm", "get", "/api/auth/me")
        self.assertEqual(j["perfil"], "Administrador")

    def test_credenciais_invalidas_mensagem_generica(self):
        a = self.cli.post("/api/auth/login", json={"email": "operador@cargotrail.test", "senha": "x"})
        b = self.cli.post("/api/auth/login", json={"email": "ninguem@cargotrail.test", "senha": "x"})
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.json["erro"], b.json["erro"])

    def test_campos_vazios(self):
        self.assertEqual(self.cli.post("/api/auth/login", json={}).status_code, 422)

    def test_bloqueio_na_quinta_tentativa_e_desbloqueio(self):
        for i in range(4):
            r = self.cli.post("/api/auth/login", json={"email": SENHAS["ope"], "senha": "errada"})
            self.assertEqual(r.status_code, 401)
        r = self.cli.post("/api/auth/login", json={"email": SENHAS["ope"], "senha": "errada"})
        self.assertEqual(r.status_code, 423)
        r = self.cli.post("/api/auth/login", json={"email": SENHAS["ope"], "senha": "Teste@123"})
        self.assertEqual(r.status_code, 423)
        uid = [u for u in self.ok("adm", "get", "/api/usuarios") if u["perfil"] == "Operador"][0]["id"]
        self.ok("adm", "post", f"/api/usuarios/{uid}/desbloquear")
        self.assertEqual(self.cli.post("/api/auth/login", json={"email": SENHAS["ope"], "senha": "Teste@123"}).status_code, 200)

    def test_permissoes_por_perfil(self):
        self.erro("ope", "post", "/api/transportadoras", {}, 403)
        self.erro("ope", "get", "/api/simulador/estado", None, 403)
        self.erro("fin", "get", "/api/pedidos", None, 403)
        self.erro("fin", "post", "/api/pedidos", {}, 403)
        self.assertEqual(self.cli.get("/api/pedidos").status_code, 401)

    def test_sessao_expira(self):
        self.cfg.sessao_timeout_s = 0.5
        time.sleep(0.7)
        j = self.erro("ope", "get", "/api/pedidos", None, 401)
        self.assertIn("expirada", j["erro"])


# ======================================================================= CADASTROS
class TestCadastros(Base):
    def test_embarcador_validacoes(self):
        base = {"razao_social": "Beta Bebidas Ltda", "cnpj": "45.678.901/0001-75", "email": "contato@beta.test", "telefone": "(11) 4000-1234", "cep": "01310-100"}
        j = self.ok("adm", "post", "/api/embarcadores", base)
        self.assertEqual(j["status"], "Ativo")
        j = self.erro("adm", "post", "/api/embarcadores", {**base, "cnpj": "11.222.333/0001-82"}, 422)
        self.assertEqual(j["campos"]["cnpj"], "CNPJ inválido")
        j = self.erro("adm", "post", "/api/embarcadores", {**base, "cnpj": "11.222.333/0001-81"}, 422)
        self.assertEqual(j["campos"]["cnpj"], "CNPJ já cadastrado")
        j = self.erro("adm", "post", "/api/embarcadores", {}, 422)
        self.assertEqual(len(j["campos"]), 5)
        j = self.erro("adm", "post", "/api/embarcadores", {**base, "cnpj": "56.789.012/0001-00", "email": "contato@beta", "telefone": "12345", "cep": "0131"}, 422)
        self.assertEqual(set(j["campos"]), {"email", "telefone", "cep"})

    def test_transportadora_rntrc(self):
        base = {"razao_social": "Delta Cargas Ltda", "cnpj": "56.789.012/0001-00", "rntrc": "12345678", "rntrc_validade": "2030-01-01",
                "email": "a@b.co", "telefone": "1130002000", "cep": "30130010"}
        self.ok("adm", "post", "/api/transportadoras", base)
        j = self.erro("adm", "post", "/api/transportadoras", {**base, "cnpj": "45.678.901/0001-75", "rntrc": "", "rntrc_validade": ""}, 422)
        self.assertEqual(set(j["campos"]), {"rntrc", "rntrc_validade"})

    def test_motorista(self):
        base = {"nome": "Carlos Lima", "cpf": "135.792.468-28", "cnh_categoria": "E", "cnh_validade": "2030-01-01", "transportadora_id": 1}
        self.ok("adm", "post", "/api/motoristas", base)
        self.assertEqual(self.erro("adm", "post", "/api/motoristas", {**base, "cpf": "111.111.111-11"}, 422)["campos"]["cpf"], "CPF inválido")
        self.assertEqual(self.erro("adm", "post", "/api/motoristas", {**base, "cpf": "529.982.247-25"}, 422)["campos"]["cpf"], "CPF já cadastrado")
        from datetime import date, timedelta
        for validade in ((date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()):
            j = self.erro("adm", "post", "/api/motoristas", {**base, "cpf": "987.654.321-00", "cnh_validade": validade}, 422)
            self.assertEqual(j["campos"]["cnh_validade"], "CNH vencida")
        self.erro("adm", "post", "/api/motoristas", {**base, "cpf": "987.654.321-00", "cnh_categoria": ""}, 422)

    def test_veiculo(self):
        base = {"placa": "QWE1R23", "tipo": "Carreta", "capacidade_kg": 30000, "transportadora_id": 1}
        self.ok("adm", "post", "/api/veiculos", base)
        self.ok("adm", "post", "/api/veiculos", {**base, "placa": "ABC-1234", "tipo": "Truck", "capacidade_kg": 14000})
        for placa in ("AB-12345", "1234ABC", "ABCD123"):
            self.assertEqual(self.erro("adm", "post", "/api/veiculos", {**base, "placa": placa}, 422)["campos"]["placa"], "Placa inválida")
        self.assertEqual(self.erro("adm", "post", "/api/veiculos", {**base, "placa": "ABC1D23"}, 422)["campos"]["placa"], "Placa já cadastrada")
        for cap in (0, -100):
            self.erro("adm", "post", "/api/veiculos", {**base, "placa": "ZZZ9Z99", "capacidade_kg": cap}, 422)
        self.ok("adm", "post", "/api/veiculos", {**base, "placa": "ZZZ9Z99", "capacidade_kg": 1})
        self.erro("adm", "post", "/api/veiculos", {**base, "placa": "YYY8Y88", "transportadora_id": ""}, 422)

    def test_inativar_e_nunca_excluir(self):
        self.ok("adm", "post", f"/api/motoristas/{ID['MOT04']}/inativar")
        r = self.pt("Em trânsito")
        self.erro("adm", "post", f"/api/motoristas/{r['motorista_id']}/inativar", None, 409, "viagem ativa")
        self.erro("adm", "post", f"/api/veiculos/{r['veiculo_id']}/inativar", None, 409, "viagem ativa")
        self.erro("adm", "post", f"/api/transportadoras/{r['transportadora_id']}/inativar", None, 409, "viagem ativa")
        self.erro("adm", "delete", "/api/embarcadores/1", None, 405)
        self.assertEqual(len(self.ok("adm", "get", "/api/embarcadores")), 1)


# ======================================================================= PEDIDO
class TestPedido(Base):
    BASE = {"embarcador_id": 1, "origem": "São Paulo/SP", "destino": "Belo Horizonte/MG", "distancia_km": 600,
            "produto": "Bebidas", "peso_kg": 20000, "valor_carga": 150000}

    def test_criar_e_validar(self):
        j = self.ok("ope", "post", "/api/pedidos", self.BASE)
        self.assertEqual(j["status"], "Rascunho")
        self.assertEqual(self.erro("ope", "post", "/api/pedidos", {}, 422)["campos"].keys() >= {"origem", "peso_kg"}, True)
        for campo, valor in (("peso_kg", 0), ("peso_kg", -10), ("valor_carga", 0), ("valor_carga", -1), ("distancia_km", 0)):
            self.erro("ope", "post", "/api/pedidos", {**self.BASE, campo: valor}, 422)
        self.ok("ope", "post", "/api/pedidos", {**self.BASE, "peso_kg": 1, "valor_carga": 0.01, "distancia_km": 1})
        j = self.erro("ope", "post", "/api/pedidos", {**self.BASE, "destino": "São Paulo/SP"}, 422)
        self.assertIn("diferentes", j["campos"]["destino"])

    def test_nfe(self):
        pid = self.ok("ope", "post", "/api/pedidos", self.BASE)["id"]
        self.ok("ope", "post", f"/api/pedidos/{pid}/nfe", {"nfe_chave": NFE[0]})
        outro = self.ok("ope", "post", "/api/pedidos", self.BASE)["id"]
        self.erro("ope", "post", f"/api/pedidos/{outro}/nfe", {"nfe_chave": NFE[0][:43]}, 422, "44 dígitos")
        self.erro("ope", "post", f"/api/pedidos/{outro}/nfe", {"nfe_chave": NFE[0][:43] + "5"}, 422, "verificador")
        self.erro("ope", "post", f"/api/pedidos/{outro}/nfe", {"nfe_chave": NFE[0][:40] + "AB4"}, 422)
        self.erro("ope", "post", f"/api/pedidos/{outro}/nfe", {"nfe_chave": NFE[0]}, 409, f"pedido {pid}")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cancelar", {"motivo": "teste"})
        self.ok("ope", "post", f"/api/pedidos/{outro}/nfe", {"nfe_chave": NFE[0]})   # NF-e liberada pelo cancelamento

    def test_enviar_e_estados(self):
        pid = self.pt("Rascunho")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{pid}/enviar", None, 409, "Vincule uma NF-e")
        self.erro("ope", "patch", f"/api/pedidos/{pid}/status", {"status": "Em execução"}, 409, "Transição")
        self.erro("ope", "patch", f"/api/pedidos/{pid}/status", {"status": "Concluído"}, 409)
        pid2 = self.pt("Rascunho", com_nfe=True)["pedido_id"]
        j = self.ok("ope", "post", f"/api/pedidos/{pid2}/enviar")
        self.assertEqual(j["status"], "Aguardando contratação")

    def test_cancelar(self):
        pid = self.pt("Contratado")["pedido_id"]
        j = self.ok("ope", "post", f"/api/pedidos/{pid}/cancelar", {"motivo": "cliente desistiu"})
        self.assertEqual(j["status"], "Cancelado")
        com_cte = self.pt("CT-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{com_cte}/cancelar", {"motivo": "x"}, 409, "Cancele o CT-e")


# ======================================================================= CONTRATAÇÃO
class TestContratacao(Base):
    def novo(self, **o):
        return self.pt("Aguardando contratação", **o)["pedido_id"]

    def test_contratacao_valida_e_numero_sequencial(self):
        p1, p2 = self.novo(), self.novo()
        s, j = self.contratar(p1)
        self.assertEqual(s, 200, j)
        self.assertEqual(j["status"], "Contratado")
        self.assertEqual(j["viagem"]["numero"], "VIA-0001")
        s, j = self.contratar(p2, mot=ID["MOT04"], vei=ID["VEI04"])
        self.assertEqual(j["viagem"]["numero"], "VIA-0002")

    def test_bloqueios_cadastrais(self):
        pid = self.novo()
        casos = [(dict(tra=ID["TRA04"], mot=1, vei=1), "inativa"), (dict(tra=ID["TRA02"], mot=1, vei=1), "RNTRC vencido"),
                 (dict(mot=ID["MOT05"]), "Motorista não pertence"), (dict(vei=ID["VEI05"]), "Veículo não pertence"),
                 (dict(mot=ID["MOT03"]), "Motorista inativo"), (dict(mot=ID["MOT02"]), "Categoria da CNH incompatível")]
        for kw, trecho in casos:
            s, j = self.contratar(pid, **kw)
            self.assertEqual(s, 409, j)
            self.assertIn(trecho, j["erro"])
        self.assertEqual(self.pedido(pid)["status"], "Aguardando contratação")

    def test_capacidade(self):
        pid = self.novo()
        s, j = self.contratar(pid, vei=ID["VEI03"], frete=5000)
        self.assertEqual(s, 409)
        self.assertIn("Peso acima da capacidade", j["erro"])
        igual = self.novo(peso_kg=14000)
        s, j = self.contratar(igual, vei=ID["VEI03"], frete=2500)
        self.assertEqual(s, 200, j)

    def test_piso_minimo(self):
        casos = [(3000, False), (3119.99, False), (3120, True)]
        for frete, aceito in casos:
            self.setUp()
            pid = self.novo()
            s, j = self.contratar(pid, frete=frete)
            self.assertEqual(s, 200 if aceito else 409, (frete, j))
            if not aceito:
                self.assertIn("R$ 3.120,00", j["erro"])

    def test_piso_por_tipo_de_veiculo(self):
        pid = self.novo(peso_kg=1000)
        s, j = self.contratar(pid, vei=ID["VEI02"], frete=1320)   # Van: 600 x 2,20
        self.assertEqual(s, 200, j)
        self.setUp()
        pid = self.novo(peso_kg=1000)
        s, j = self.contratar(pid, vei=ID["VEI03"], frete=1320)   # Truck: piso 2.400
        self.assertEqual(s, 409)
        self.assertIn("R$ 2.400,00", j["erro"])

    def test_motorista_e_veiculo_em_outra_viagem(self):
        p1, p2, p3 = self.novo(), self.novo(), self.novo()
        self.assertEqual(self.contratar(p1)[0], 200)
        s, j = self.contratar(p2, mot=1, vei=ID["VEI04"])
        self.assertEqual((s, j["erro"]), (409, "Motorista em viagem ativa"))
        s, j = self.contratar(p3, mot=ID["MOT04"], vei=1)
        self.assertEqual((s, j["erro"]), (409, "Veículo em viagem ativa"))

    def test_alterar_frete(self):
        pid = self.pt("Contratado")["pedido_id"]
        j = self.ok("ope", "post", f"/api/pedidos/{pid}/frete", {"frete": 3600})
        self.assertEqual(j["viagem"]["frete"], 3600)
        self.erro("ope", "post", f"/api/pedidos/{pid}/frete", {"frete": 100}, 409, "piso mínimo")
        com_cte = self.pt("CT-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{com_cte}/frete", {"frete": 3600}, 409, "após o CT-e")


# ======================================================================= EXECUÇÃO
class TestExecucao(Base):
    def test_cte_autorizado_chave_e_icms_interestadual(self):
        pid = self.pt("Contratado")["pedido_id"]
        j = self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual(d["status"], "Autorizado")
        self.assertTrue(d["protocolo"])
        self.assertTrue(chave_valida(d["chave"]))
        self.assertEqual((d["icms_aliquota"], d["icms_valor"]), (12, 420.0))
        self.assertEqual(len(self.ok("adm", "get", "/api/simulador/historico")), 1)

    def test_icms_mesma_uf_e_arredondamento(self):
        pid = self.pt("Contratado", origem="Campinas/SP", destino="Santos/SP", distancia_km=100, frete=1000)["pedido_id"]
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual((d["icms_aliquota"], d["icms_valor"]), (18, 180.0))
        pid = self.pt("Contratado", frete=3333.33)["pedido_id"]
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        self.assertEqual(self.doc(pid, "CTE")["icms_valor"], 400.0)

    def test_pre_condicoes_e_segunda_emissao(self):
        ag = self.pt("Aguardando contratação")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{ag}/cte", None, 409, "Pedido não contratado")
        sem_nfe = self.pt("Contratado", sem_nfe=True)["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{sem_nfe}/cte", None, 409, "NF-e não vinculada")
        ok = self.pt("CT-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{ok}/cte", None, 409, "Já existe CT-e ativo")

    def test_rejeicao_correcao_e_reenvio(self):
        pid = self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "rejeitar", codigo="225", escopo="proxima")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual((d["status"], d["rejeicao_codigo"]), ("Rejeitado", "225"))
        self.assertIn("esquema", d["rejeicao_motivo"])
        self.erro("ope", "post", f"/api/documentos/{d['id']}/reenviar", {}, 422, "correção")
        self.ok("ope", "post", f"/api/documentos/{d['id']}/reenviar", {"correcao": "Dado ajustado"})
        self.assertEqual(self.doc(pid, "CTE")["status"], "Autorizado")

    def test_rejeicao_204_duplicidade(self):
        pid = self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "rejeitar", codigo="204")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual((d["status"], d["rejeicao_codigo"]), ("Rejeitado", "204"))
        self.assertIn("Duplicidade", d["rejeicao_motivo"])

    def test_fora_do_ar_pendente_e_reenvio_sem_duplicar(self):
        pid = self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "fora_do_ar")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual(d["status"], "Pendente de envio")
        self.erro("ope", "post", f"/api/pedidos/{pid}/mdfe", None, 409)
        self.cenario("sefaz", "autorizar")
        self.ok("ope", "post", f"/api/documentos/{d['id']}/reenviar", {})
        self.assertEqual(len([x for x in self.pedido(pid)["documentos"] if x["tipo_codigo"] == "CTE"]), 1)
        self.assertEqual(self.doc(pid, "CTE")["status"], "Autorizado")

    def test_demora_alem_do_tempo_limite_fica_pendente_e_reenvio_nao_duplica(self):
        pid = self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "demorar", atraso_s=3, escopo="proxima")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        d = self.doc(pid, "CTE")
        self.assertEqual(d["status"], "Pendente de envio")
        time.sleep(1.5)   # a SEFAZ simulada termina de processar depois que o sistema desistiu
        self.ok("ope", "post", f"/api/documentos/{d['id']}/reenviar", {})
        self.assertEqual(self.doc(pid, "CTE")["status"], "Autorizado")
        autorizadas = [h for h in self.ok("adm", "get", "/api/simulador/historico") if h["resultado"] == "autorizado" and not h["repetida"]]
        self.assertEqual(len(autorizadas), 1)

    def test_cancelar_cte_prazos(self):
        dentro = self.pt("CT-e autorizado", idade_cte_dias=1)["pedido_id"]
        j = self.ok("ope", "post", f"/api/documentos/{self.doc(dentro, 'CTE')['id']}/cancelar", {"motivo": "erro"})
        self.assertEqual(j["status"], "Contratado")
        self.assertEqual(self.doc(dentro, "CTE")["status"], "Cancelado")
        self.ok("ope", "post", f"/api/pedidos/{dentro}/cte")            # novo CT-e depois do cancelamento
        ultimo = self.pt("CT-e autorizado", idade_cte_dias=6.95)["pedido_id"]
        self.ok("ope", "post", f"/api/documentos/{self.doc(ultimo, 'CTE')['id']}/cancelar", {"motivo": "limite"})
        fora = self.pt("CT-e autorizado", idade_cte_dias=8)["pedido_id"]
        self.erro("ope", "post", f"/api/documentos/{self.doc(fora, 'CTE')['id']}/cancelar", {"motivo": "x"}, 409, "Prazo")
        com_mdfe = self.pt("MDF-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/documentos/{self.doc(com_mdfe, 'CTE')['id']}/cancelar", {"motivo": "x"}, 409, "Cancele o MDF-e")

    def test_ciot(self):
        pid = self.pt("CT-e autorizado")["pedido_id"]
        j = self.ok("ope", "post", f"/api/pedidos/{pid}/ciot")
        self.assertTrue(j["viagem"]["ciot"])
        self.erro("ope", "post", f"/api/pedidos/{pid}/ciot", None, 409, "CIOT já gerado")
        sem = self.pt("Contratado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{sem}/ciot", None, 409, "CT-e autorizado é obrigatório")
        self.cenario("ciot", "rejeitar")
        outro = self.pt("CT-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{outro}/ciot", None, 422, "recusado")
        self.assertIsNone(self.pedido(outro)["viagem"]["ciot"])

    def test_mdfe(self):
        pid = self.pt("CIOT gerado")["pedido_id"]
        self.ok("ope", "post", f"/api/pedidos/{pid}/mdfe")
        m = self.doc(pid, "MDFE")
        self.assertEqual(m["status"], "Autorizado")
        self.assertTrue(chave_valida(m["chave"]))
        self.assertEqual(self.pedido(pid)["status"], "Em execução")
        xml = self.ok("ope", "get", f"/api/documentos/{m['id']}/xml")
        self.assertIn(self.doc(pid, "CTE")["chave"], xml)
        self.assertIn("<placa>", xml)
        sem_ciot = self.pt("CT-e autorizado")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{sem_ciot}/mdfe", None, 409, "CIOT é obrigatório")
        pend = self.pt("CT-e pendente de envio")["pedido_id"]
        self.erro("ope", "post", f"/api/pedidos/{pend}/mdfe", None, 409, "CT-e autorizado é obrigatório")
        self.cenario("sefaz", "rejeitar", codigo="539", escopo="proxima")
        rej = self.pt("CIOT gerado")["pedido_id"]
        self.ok("ope", "post", f"/api/pedidos/{rej}/mdfe")
        self.assertEqual(self.doc(rej, "MDFE")["rejeicao_codigo"], "539")
        self.assertEqual(self.pedido(rej)["status"], "Contratado")

    def test_cancelar_e_encerrar_mdfe(self):
        dentro = self.pt("MDF-e autorizado", idade_mdfe_horas=2)["pedido_id"]
        j = self.ok("ope", "post", f"/api/documentos/{self.doc(dentro, 'MDFE')['id']}/cancelar", {"motivo": "erro"})
        self.assertEqual(j["status"], "Contratado")
        fora = self.pt("MDF-e autorizado", idade_mdfe_horas=25)["pedido_id"]
        self.erro("ope", "post", f"/api/documentos/{self.doc(fora, 'MDFE')['id']}/cancelar", {"motivo": "x"}, 409, "Prazo")
        enc = self.pt("MDF-e encerrado")["pedido_id"]
        self.erro("ope", "post", f"/api/documentos/{self.doc(enc, 'MDFE')['id']}/cancelar", {"motivo": "x"}, 409, "já encerrado")
        ent = self.pt("Entregue")["pedido_id"]
        j = self.ok("ope", "post", f"/api/documentos/{self.doc(ent, 'MDFE')['id']}/encerrar")
        self.assertEqual(self.doc(ent, "MDFE")["status"], "Encerrado")
        pto = self.pt("No ponto de entrega")["pedido_id"]
        self.erro("ope", "post", f"/api/documentos/{self.doc(pto, 'MDFE')['id']}/encerrar", None, 409, "Entrega não comprovada")


# ======================================================================= MONITORAMENTO e COMPROVAÇÃO
class TestViagem(Base):
    def vid(self, r):
        return r["viagem_id"]

    def test_iniciar_exige_mdfe(self):
        sem = self.pt("CIOT gerado")
        self.erro("ope", "post", f"/api/viagens/{self.vid(sem)}/iniciar", None, 409, "MDF-e autorizado é obrigatório")
        ok = self.pt("MDF-e autorizado")
        j = self.ok("ope", "post", f"/api/viagens/{self.vid(ok)}/iniciar")
        self.assertEqual(j["estado"], "Em trânsito")
        self.assertEqual(j["eventos"][-1]["origem"], "Operador")

    def test_avanco_automatico_por_tempo(self):
        gravar_config_viagem(self.conn(), {"tempo_transito_s": 1, "previsao_chegada_s": 30})
        r = self.pt("MDF-e autorizado")
        v = self.vid(r)
        self.ok("ope", "post", f"/api/viagens/{v}/iniciar")
        c = self.conn()
        servicos.tick(c)
        self.assertEqual(self.ok("ope", "get", f"/api/viagens/{v}")["estado"], "Em trânsito")
        time.sleep(1.2)
        servicos.tick(c)
        j = self.ok("ope", "get", f"/api/viagens/{v}")
        self.assertEqual(j["estado"], "No ponto de entrega")
        self.assertEqual(j["eventos"][-1]["origem"], "Sistema")
        self.assertIn("duração simulada", j["eventos"][-1]["texto"])

    def test_nao_avanca_se_nao_iniciada(self):
        gravar_config_viagem(self.conn(), {"tempo_transito_s": 1})
        r = self.pt("MDF-e autorizado")
        time.sleep(1.2)
        servicos.tick(self.conn())
        self.assertEqual(self.ok("ope", "get", f"/api/viagens/{self.vid(r)}")["estado"], "Aguardando saída")

    def test_estados_nao_pulam(self):
        v = self.vid(self.pt("MDF-e autorizado"))
        self.erro("ope", "patch", f"/api/viagens/{v}/estado", {"estado": "Entregue"}, 409, "Transição")
        self.erro("ope", "patch", f"/api/viagens/{v}/estado", {"estado": "No ponto de entrega"}, 409)
        self.assertEqual(self.ok("ope", "get", f"/api/viagens/{v}")["estado"], "Aguardando saída")

    def test_ocorrencias(self):
        v = self.vid(self.pt("Em trânsito"))
        j = self.ok("ope", "post", f"/api/viagens/{v}/ocorrencias", {"tipo": "Atraso", "descricao": "Trânsito intenso"})
        self.assertEqual(j["ocorrencias"][0]["tipo"], "Atraso")
        self.erro("ope", "post", f"/api/viagens/{v}/ocorrencias", {"tipo": "", "descricao": "x"}, 422)
        self.erro("ope", "post", f"/api/viagens/{v}/ocorrencias", {"tipo": "Parada", "descricao": ""}, 422)

    def test_alerta_de_atraso(self):
        gravar_config_viagem(self.conn(), {"previsao_chegada_s": 1, "tempo_transito_s": 30})
        v = self.vid(self.pt("MDF-e autorizado"))
        self.ok("ope", "post", f"/api/viagens/{v}/iniciar")
        self.assertFalse(self.ok("ope", "get", f"/api/viagens/{v}")["atraso"])
        time.sleep(1.2)
        self.assertTrue(self.ok("ope", "get", f"/api/viagens/{v}")["atraso"])

    def test_modo_manual_e_permissao(self):
        v = self.vid(self.pt("Em trânsito", sem_avanco_automatico=True))
        self.erro("adm", "post", f"/api/simulador/viagens/{v}/avancar", None, 409, "Modo manual desligado")
        self.ok("adm", "put", "/api/simulador/viagem", {"modo_manual": True})
        self.erro("ope", "post", f"/api/simulador/viagens/{v}/avancar", None, 403)
        j = self.ok("adm", "post", f"/api/simulador/viagens/{v}/avancar")
        self.assertEqual(j["estado"], "No ponto de entrega")
        self.assertEqual(j["eventos"][-1]["origem"], "Administrador")
        self.erro("adm", "post", f"/api/simulador/viagens/{v}/avancar", None, 409, "comprovante")

    def test_inicio_automatico(self):
        gravar_config_viagem(self.conn(), {"inicio_automatico": True, "espera_inicio_s": 1, "tempo_transito_s": 30})
        v = self.vid(self.pt("CIOT gerado"))
        pid = self.conn().execute("SELECT pedido_id FROM viagens WHERE id=?", (v,)).fetchone()[0]
        self.ok("ope", "post", f"/api/pedidos/{pid}/mdfe")
        servicos.tick(self.conn())
        self.assertEqual(self.ok("ope", "get", f"/api/viagens/{v}")["estado"], "Aguardando saída")
        time.sleep(1.2)
        servicos.tick(self.conn())
        j = self.ok("ope", "get", f"/api/viagens/{v}")
        self.assertEqual((j["estado"], j["eventos"][-1]["origem"]), ("Em trânsito", "Sistema"))


class TestComprovacao(Base):
    DADOS = {"recebedor_nome": "Marcos Almeida", "recebedor_cpf": "529.982.247-25"}

    def test_confirmar_entrega(self):
        v = self.pt("No ponto de entrega")["viagem_id"]
        j = self.ok("ope", "post", f"/api/viagens/{v}/comprovante", self.DADOS)
        self.assertEqual((j["estado"], j["ativa"]), ("Entregue", False))
        self.assertEqual(j["comprovante"]["divergencia"], "Nenhuma")
        self.erro("ope", "put", f"/api/viagens/{v}/comprovante", {"recebedor_nome": "x"}, 405)
        self.erro("ope", "delete", f"/api/viagens/{v}/comprovante", None, 405)

    def test_validacoes(self):
        v = self.pt("No ponto de entrega")["viagem_id"]
        self.erro("ope", "post", f"/api/viagens/{v}/comprovante", {**self.DADOS, "recebedor_nome": ""}, 422, "Nome")
        self.erro("ope", "post", f"/api/viagens/{v}/comprovante", {**self.DADOS, "recebedor_cpf": "111.111.111-11"}, 422, "CPF inválido")
        for div in ("Avaria", "Falta"):
            self.erro("ope", "post", f"/api/viagens/{v}/comprovante", {**self.DADOS, "divergencia": div, "descricao": ""}, 422, "Descreva")
        self.erro("ope", "post", f"/api/viagens/{v}/comprovante",
                  {**self.DADOS, "divergencia": "Avaria", "descricao": "caixas", "desconto": 3500.01}, 422, "maior que o frete")
        j = self.ok("ope", "post", f"/api/viagens/{v}/comprovante",
                    {**self.DADOS, "divergencia": "Avaria", "descricao": "caixas", "desconto": 3500})
        self.assertEqual(j["comprovante"]["desconto"], 3500)

    def test_so_no_ponto_de_entrega(self):
        v = self.pt("Em trânsito")["viagem_id"]
        self.erro("ope", "post", f"/api/viagens/{v}/comprovante", self.DADOS, 409, "fora do ponto de entrega")


# ======================================================================= PAGAMENTO
class TestPagamento(Base):
    def test_pagar_e_concluir(self):
        r = self.pt("MDF-e encerrado")
        j = self.ok("fin", "post", f"/api/viagens/{r['viagem_id']}/pagamento")
        self.assertEqual(j["situacao"], "Pago")
        self.assertEqual(j["valor_a_pagar"], 3500)
        self.assertEqual(self.pedido(r["pedido_id"])["status"], "Concluído")
        self.erro("fin", "post", f"/api/viagens/{r['viagem_id']}/pagamento", None, 409, "já paga")

    def test_bloqueios(self):
        self.erro("fin", "post", f"/api/viagens/{self.pt('No ponto de entrega')['viagem_id']}/pagamento", None, 409, "Entrega não comprovada")
        self.erro("fin", "post", f"/api/viagens/{self.pt('Entregue')['viagem_id']}/pagamento", None, 409, "MDF-e não encerrado")
        v = self.pt("MDF-e encerrado")["viagem_id"]
        self.erro("ope", "post", f"/api/viagens/{v}/pagamento", None, 403)
        self.erro("adm", "post", f"/api/viagens/{v}/pagamento", None, 403)

    def test_desconto_por_avaria(self):
        r = self.pt("MDF-e encerrado", divergencia="Avaria", desconto=200)
        p = self.ok("fin", "get", f"/api/viagens/{r['viagem_id']}/pagamento")
        self.assertEqual((p["frete"], p["desconto"], p["valor_a_pagar"]), (3500, 200, 3300))
        j = self.ok("fin", "post", f"/api/viagens/{r['viagem_id']}/pagamento")
        self.assertEqual(j["tentativas"][-1]["valor"], 3300)

    def test_recusado_pendente_e_nova_tentativa(self):
        v = self.pt("MDF-e encerrado")["viagem_id"]
        self.cenario("pagamento", "rejeitar", escopo="proxima")
        j = self.ok("fin", "post", f"/api/viagens/{v}/pagamento")
        self.assertEqual((j["situacao"], j["tentativas"][-1]["status"]), ("Pendente", "Recusado"))
        j = self.ok("fin", "post", f"/api/viagens/{v}/pagamento")
        self.assertEqual(j["situacao"], "Pago")
        self.assertEqual([t["status"] for t in j["tentativas"]], ["Recusado", "Pago"])

    def test_sem_resposta_nao_duplica_cobranca(self):
        v = self.pt("MDF-e encerrado")["viagem_id"]
        self.cenario("pagamento", "demorar", atraso_s=3, escopo="proxima")
        j = self.ok("fin", "post", f"/api/viagens/{v}/pagamento")
        self.assertEqual((j["situacao"], j["tentativas"][-1]["status"]), ("Pendente", "Falha de comunicação"))
        time.sleep(1.5)
        j = self.ok("fin", "post", f"/api/viagens/{v}/pagamento")
        self.assertEqual(j["situacao"], "Pago")
        aprovados = [h for h in self.ok("adm", "get", "/api/simulador/historico") if h["resultado"] == "aprovado" and not h["repetida"]]
        self.assertEqual(len(aprovados), 1)

    def test_lista_mostra_viagens_em_qualquer_situacao(self):
        self.pt("Em trânsito")
        self.pt("MDF-e encerrado")
        sit = {p["situacao"] for p in self.ok("fin", "get", "/api/pagamentos")}
        self.assertEqual(sit, {"Aguardando entrega", "Pronto para pagar"})


# ======================================================================= SIMULADOR
class TestSimulador(Base):
    def test_cenarios_independentes_e_proxima_chamada(self):
        a, b = self.pt("Contratado")["pedido_id"], self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "rejeitar", codigo="225", escopo="proxima")
        self.ok("ope", "post", f"/api/pedidos/{a}/cte")
        self.ok("ope", "post", f"/api/pedidos/{b}/cte")
        self.assertEqual((self.doc(a, "CTE")["status"], self.doc(b, "CTE")["status"]), ("Rejeitado", "Autorizado"))
        self.cenario("sefaz", "rejeitar", codigo="225")          # todas as chamadas
        c, d = self.pt("Contratado")["pedido_id"], self.pt("Contratado")["pedido_id"]
        self.ok("ope", "post", f"/api/pedidos/{c}/cte")
        self.ok("ope", "post", f"/api/pedidos/{d}/cte")
        self.assertEqual((self.doc(c, "CTE")["status"], self.doc(d, "CTE")["status"]), ("Rejeitado", "Rejeitado"))
        est = self.ok("adm", "get", "/api/simulador/estado")
        self.assertEqual(est["cenarios"]["ciot"]["modo"], "autorizar")

    def test_demora_e_fora_do_ar_no_simulador(self):
        self.cenario("sefaz", "demorar", atraso_s=1)
        t0 = time.time()
        r = requests.post(self.sim_url + "/sefaz/cte", json={"chave": "x"})
        self.assertGreaterEqual(time.time() - t0, 1)
        self.assertEqual(r.json()["status"], "autorizado")
        self.cenario("sefaz", "fora_do_ar")
        self.assertEqual(requests.get(self.sim_url + "/sefaz/status").status_code, 503)

    def test_historico_e_restaurar(self):
        pid = self.pt("Contratado")["pedido_id"]
        self.cenario("sefaz", "rejeitar", codigo="204")
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        h = self.ok("adm", "get", "/api/simulador/historico")
        self.assertEqual((h[0]["servico"], h[0]["tipo"], h[0]["resultado"], h[0]["codigo"]), ("sefaz", "CT-e", "rejeitado", "204"))
        self.ok("adm", "post", "/api/simulador/restaurar")
        self.assertEqual(self.ok("adm", "get", "/api/simulador/historico"), [])
        self.assertEqual(self.ok("adm", "get", "/api/simulador/estado")["cenarios"]["sefaz"]["modo"], "autorizar")
        self.assertEqual(self.ok("adm", "get", "/api/pedidos"), [])

    def test_configurar_viagem(self):
        j = self.ok("adm", "put", "/api/simulador/viagem", {"tempo_transito_s": 20, "previsao_chegada_s": 30, "fator_aceleracao": 60})
        self.assertEqual((j["tempo_transito_s"], j["fator_aceleracao"]), (20, 60))
        self.erro("adm", "put", "/api/simulador/viagem", {"tempo_transito_s": 0}, 422)

    def test_painel_so_admin_e_com_simulador_ativo(self):
        self.erro("ope", "get", "/api/simulador/estado", None, 403)
        self.erro("fin", "put", "/api/simulador/viagem", {}, 403)
        self.cfg.simulador_ativo = False
        self.erro("adm", "get", "/api/simulador/estado", None, 404)
        self.assertFalse(self.cli.get("/api/config").json["simulador_ativo"])

    def test_pedidos_de_teste_em_todos_os_estados(self):
        ids = set()
        for estado in seed.ESTADOS_TESTE:
            r = self.pt(estado)
            ids.add(r["pedido_id"])
            self.assertEqual(self.pedido(r["pedido_id"])["id"], r["pedido_id"])
        self.assertEqual(len(ids), len(seed.ESTADOS_TESTE))
        self.erro("adm", "post", "/api/simulador/pedidos-teste", {"estado": "Inexistente"}, 422)
        d = self.pt("CT-e autorizado", idade_cte_dias=8)["pedido_id"]
        self.assertGreater(self.doc(d, "CTE")["prazo_cancelamento_restante_s"] * -1, 0)


# ======================================================================= PONTA A PONTA
class TestPontaAPonta(Base):
    def test_fluxo_completo_do_pedido_ao_pagamento(self):
        gravar_config_viagem(self.conn(), {"tempo_transito_s": 1, "previsao_chegada_s": 30})
        pid = self.ok("ope", "post", "/api/pedidos", TestPedido.BASE)["id"]
        self.ok("ope", "post", f"/api/pedidos/{pid}/nfe", {"nfe_chave": NFE[1]})
        self.ok("ope", "post", f"/api/pedidos/{pid}/enviar")
        self.assertEqual(self.contratar(pid)[0], 200)
        self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        self.ok("ope", "post", f"/api/pedidos/{pid}/ciot")
        self.ok("ope", "post", f"/api/pedidos/{pid}/mdfe")
        v = self.pedido(pid)["viagem"]["id"]
        self.ok("ope", "post", f"/api/viagens/{v}/iniciar")
        time.sleep(1.2)
        servicos.tick(self.conn())
        self.ok("ope", "post", f"/api/viagens/{v}/comprovante", TestComprovacao.DADOS)
        self.ok("ope", "post", f"/api/documentos/{self.doc(pid, 'MDFE')['id']}/encerrar")
        self.ok("fin", "post", f"/api/viagens/{v}/pagamento")
        p = self.pedido(pid)
        self.assertEqual(p["status"], "Concluído")
        textos = [e["texto"] for e in p["eventos"]]
        for trecho in ("Pedido criado", "Contratação", "CT-e autorizado", "CIOT gerado", "MDF-e autorizado", "Viagem iniciada",
                       "Chegada ao ponto", "Entrega comprovada", "MDF-e encerrado", "Pagamento aprovado"):
            self.assertTrue(any(trecho in t for t in textos), trecho)

    def test_dois_pedidos_simultaneos_nao_se_misturam(self):
        a, b = self.pt("Contratado")["pedido_id"], self.pt("Contratado")["pedido_id"]
        for pid in (a, b):
            self.ok("ope", "post", f"/api/pedidos/{pid}/cte")
        self.assertNotEqual(self.doc(a, "CTE")["chave"], self.doc(b, "CTE")["chave"])
        self.assertNotEqual(self.pedido(a)["viagem"]["numero"], self.pedido(b)["viagem"]["numero"])


class TestDocumentacao(Base):
    def test_openapi_e_swagger(self):
        spec = self.cli.get("/openapi.json").json
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("/api/pedidos/{pid}/contratar", spec["paths"])
        self.assertIn("post", spec["paths"]["/api/auth/login"])
        self.assertEqual(self.cli.get("/docs").status_code, 200)
        sim = requests.get(self.sim_url + "/openapi.json").json()
        self.assertIn("/sefaz/cte", sim["paths"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
