import os
import sqlite3
from datetime import datetime, timedelta


class Config:
    """Configuração lida do ambiente (valores padrão de estudo)."""

    def __init__(self, **sobrescritas):
        env = os.environ.get
        self.db_path = env("DB_PATH", "data/cargotrail.db")
        self.simulador_url = env("SIMULADOR_URL", "http://localhost:8001")
        self.simulador_ativo = env("SIMULADOR_ATIVO", "true").lower() == "true"
        self.sessao_timeout_s = float(env("SESSAO_TIMEOUT_S", "900"))
        self.servico_timeout_s = float(env("SERVICO_TIMEOUT_S", "30"))
        self.prazo_cancelar_cte_s = float(env("PRAZO_CANCELAR_CTE_S", str(7 * 24 * 3600)))
        self.prazo_cancelar_mdfe_s = float(env("PRAZO_CANCELAR_MDFE_S", str(24 * 3600)))
        self.max_tentativas_login = int(env("MAX_TENTATIVAS_LOGIN", "5"))
        self.agendador_intervalo_s = float(env("AGENDADOR_INTERVALO_S", "1"))
        for k, v in sobrescritas.items():
            setattr(self, k, v)


SCHEMA = """
CREATE TABLE usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  senha_hash TEXT NOT NULL, perfil TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Ativo', tentativas INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessoes (token TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL, ultima_atividade REAL NOT NULL);
CREATE TABLE embarcadores (id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL, cnpj TEXT NOT NULL,
  email TEXT, telefone TEXT, cep TEXT, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE transportadoras (id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL, cnpj TEXT NOT NULL,
  rntrc TEXT, rntrc_validade TEXT, email TEXT, telefone TEXT, cep TEXT, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE motoristas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, cpf TEXT NOT NULL UNIQUE,
  cnh_categoria TEXT, cnh_validade TEXT, transportadora_id INTEGER NOT NULL, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE veiculos (id INTEGER PRIMARY KEY AUTOINCREMENT, placa TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL,
  capacidade_kg REAL NOT NULL, transportadora_id INTEGER NOT NULL, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, embarcador_id INTEGER NOT NULL, origem TEXT NOT NULL,
  destino TEXT NOT NULL, distancia_km REAL NOT NULL, produto TEXT NOT NULL, peso_kg REAL NOT NULL,
  valor_carga_centavos INTEGER NOT NULL, nfe_chave TEXT, status TEXT NOT NULL DEFAULT 'Rascunho',
  criado_em TEXT NOT NULL, motivo_cancelamento TEXT);
CREATE TABLE viagens (id INTEGER PRIMARY KEY AUTOINCREMENT, pedido_id INTEGER NOT NULL, transportadora_id INTEGER NOT NULL,
  motorista_id INTEGER NOT NULL, veiculo_id INTEGER NOT NULL, frete_centavos INTEGER NOT NULL,
  estado TEXT NOT NULL DEFAULT 'Aguardando saída', ativa INTEGER NOT NULL DEFAULT 1, iniciada_em REAL,
  chegada_automatica_em REAL, previsao_chegada_em REAL, chegada_em REAL);
CREATE TABLE documentos (id INTEGER PRIMARY KEY AUTOINCREMENT, pedido_id INTEGER NOT NULL, tipo TEXT NOT NULL,
  status TEXT NOT NULL, chave TEXT, protocolo TEXT, rejeicao_codigo TEXT, rejeicao_motivo TEXT, xml TEXT,
  icms_aliquota INTEGER, icms_centavos INTEGER, autorizado_em TEXT, criado_em TEXT NOT NULL, motivo TEXT,
  correcao TEXT);
CREATE TABLE ciots (id INTEGER PRIMARY KEY AUTOINCREMENT, viagem_id INTEGER NOT NULL UNIQUE, numero TEXT NOT NULL,
  criado_em TEXT NOT NULL);
CREATE TABLE ocorrencias (id INTEGER PRIMARY KEY AUTOINCREMENT, viagem_id INTEGER NOT NULL, tipo TEXT NOT NULL,
  descricao TEXT NOT NULL, criado_em TEXT NOT NULL, usuario TEXT NOT NULL);
CREATE TABLE eventos (id INTEGER PRIMARY KEY AUTOINCREMENT, viagem_id INTEGER, pedido_id INTEGER NOT NULL,
  texto TEXT NOT NULL, origem TEXT NOT NULL, criado_em TEXT NOT NULL);
CREATE TABLE comprovantes (id INTEGER PRIMARY KEY AUTOINCREMENT, viagem_id INTEGER NOT NULL UNIQUE,
  recebedor_nome TEXT NOT NULL, recebedor_cpf TEXT NOT NULL, divergencia TEXT NOT NULL, descricao TEXT,
  desconto_centavos INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL);
CREATE TABLE pagamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, viagem_id INTEGER NOT NULL UNIQUE,
  valor_centavos INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Pendente');
CREATE TABLE pagamento_tentativas (id INTEGER PRIMARY KEY AUTOINCREMENT, pagamento_id INTEGER NOT NULL,
  valor_centavos INTEGER NOT NULL, status TEXT NOT NULL, motivo TEXT, criado_em TEXT NOT NULL);
CREATE TABLE config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""

TABELAS = ["usuarios", "sessoes", "embarcadores", "transportadoras", "motoristas", "veiculos", "pedidos", "viagens",
           "documentos", "ciots", "ocorrencias", "eventos", "comprovantes", "pagamentos", "pagamento_tentativas", "config"]

# configuração da simulação da viagem (RN-SIM-04), guardada no banco
VIAGEM_PADRAO = {"inicio_automatico": False, "espera_inicio_s": 10, "tempo_transito_s": 30,
                 "previsao_chegada_s": 30, "fator_aceleracao": 1, "modo_manual": False}


def conectar(caminho):
    pasta = os.path.dirname(caminho)
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def criar_schema(conn):
    for t in TABELAS:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)
    conn.commit()


def agora_iso():
    return datetime.now().isoformat(timespec="seconds")


def iso_de(dt):
    return dt.isoformat(timespec="seconds")


def passou_s(iso):
    """Segundos desde um instante ISO."""
    return (datetime.now() - datetime.fromisoformat(iso)).total_seconds()


def config_viagem(conn):
    import json
    cfg = dict(VIAGEM_PADRAO)
    linha = conn.execute("SELECT valor FROM config WHERE chave='viagem'").fetchone()
    if linha:
        cfg.update(json.loads(linha["valor"]))
    return cfg


def gravar_config_viagem(conn, novo):
    import json
    cfg = config_viagem(conn)
    cfg.update(novo)
    conn.execute("INSERT OR REPLACE INTO config(chave, valor) VALUES('viagem', ?)", (json.dumps(cfg),))
    conn.commit()
    return cfg
