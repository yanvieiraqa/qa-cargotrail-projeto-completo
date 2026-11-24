"""Regras puras do CargoTrail: validações, chaves de acesso, frete e ICMS.

Valores de piso, alíquotas e prazos são FICTÍCIOS, de estudo (ver documento de Regras de Negócio).
"""
import random
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TIPOS_VEICULO = ("Van", "Caminhão 3/4", "Truck", "Carreta")
CATEGORIAS_CNH = ("A", "B", "C", "D", "E")
# piso mínimo em centavos por km (RN-CON-04)
PISO_CENTAVOS_KM = {"Van": 220, "Caminhão 3/4": 310, "Truck": 400, "Carreta": 520}
# categorias de CNH aceitas por tipo de veículo (RN-CON-02)
CNH_POR_VEICULO = {
    "Van": ("B", "C", "D", "E"),
    "Caminhão 3/4": ("C", "D", "E"),
    "Truck": ("C", "D", "E"),
    "Carreta": ("E",),
}
ALIQUOTA_MESMA_UF = 18
ALIQUOTA_INTERESTADUAL = 12
CODIGO_UF = {"RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17, "MA": 21, "PI": 22, "CE": 23,
             "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29, "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
             "PR": 41, "SC": 42, "RS": 43, "MS": 50, "MT": 51, "GO": 52, "DF": 53}


def so_digitos(valor):
    return re.sub(r"\D", "", str(valor or ""))


def cpf_valido(valor):
    d = so_digitos(valor)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10) % 11 % 10 != int(d[n]):
            return False
    return True


def cnpj_valido(valor):
    d = so_digitos(valor)
    if len(d) != 14 or d == d[0] * 14:
        return False

    def dv(base):
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        resto = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return 0 if resto < 2 else 11 - resto

    return dv(d[:12]) == int(d[12]) and dv(d[:13]) == int(d[13])


def email_valido(valor):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", str(valor or "")))


def telefone_valido(valor):
    return len(so_digitos(valor)) in (10, 11)


def cep_valido(valor):
    return len(so_digitos(valor)) == 8


def normalizar_placa(valor):
    return re.sub(r"[^A-Za-z0-9]", "", str(valor or "")).upper()


def placa_valida(valor):
    p = normalizar_placa(valor)
    return bool(re.fullmatch(r"[A-Z]{3}\d{4}", p) or re.fullmatch(r"[A-Z]{3}\d[A-Z]\d{2}", p))


def parse_local(valor):
    """'São Paulo/SP' -> ('São Paulo', 'SP'). Devolve None se o formato for inválido."""
    m = re.fullmatch(r"\s*(.+?)\s*/\s*([A-Za-z]{2})\s*", str(valor or ""))
    if not m or m.group(2).upper() not in CODIGO_UF:
        return None
    return m.group(1), m.group(2).upper()


# ------------------------------------------------------------------ dinheiro
def centavos(valor):
    """Converte número ou texto em centavos (int). Levanta ValueError se inválido."""
    if isinstance(valor, bool) or valor is None or str(valor).strip() == "":
        raise ValueError("valor inválido")
    try:
        return int((Decimal(str(valor).replace(",", ".")) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("valor inválido")


def brl(c):
    texto = f"{c / 100:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def reais(c):
    return None if c is None else c / 100


def piso_centavos(distancia_km, tipo):
    return int((Decimal(str(distancia_km)) * PISO_CENTAVOS_KM[tipo]).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def aliquota_icms(uf_origem, uf_destino):
    return ALIQUOTA_MESMA_UF if uf_origem == uf_destino else ALIQUOTA_INTERESTADUAL


def icms_centavos(frete_centavos, aliquota):
    return (frete_centavos * aliquota + 50) // 100


# ------------------------------------------------------------------ chave de acesso
def dv_modulo11(numero):
    soma, peso = 0, 2
    for ch in reversed(numero):
        soma += int(ch) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def chave_valida(chave):
    c = str(chave or "")
    return len(c) == 44 and c.isdigit() and dv_modulo11(c[:43]) == int(c[43])


def gerar_chave(uf, cnpj_emitente, modelo, numero, rng=random):
    """44 dígitos: cUF + AAMM + CNPJ + modelo + série + número + tpEmis + código + DV."""
    base = (f"{CODIGO_UF[uf]:02d}{datetime.now().strftime('%y%m')}{so_digitos(cnpj_emitente):0>14}"
            f"{modelo}001{numero:09d}1{rng.randint(0, 99999999):08d}")
    return base + str(dv_modulo11(base))


def novo_chave_nfe(rng=random):
    """Chave de NF-e fictícia e válida (usada pelo simulador para criar pedidos de teste)."""
    return gerar_chave("SP", "11222333000181", "55", rng.randint(1, 999999999), rng)


def gerar_cpf(rng=random):
    """CPF fictício com dígitos verificadores válidos (usado em dados de teste)."""
    while True:
        base = [rng.randint(0, 9) for _ in range(9)]
        for n in (9, 10):
            soma = sum(d * (n + 1 - i) for i, d in enumerate(base))
            base.append((soma * 10) % 11 % 10)
        cpf = "".join(map(str, base))
        if cpf_valido(cpf):
            return cpf


def gerar_placa_mercosul(rng=random):
    letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return (rng.choice(letras) + rng.choice(letras) + rng.choice(letras) + str(rng.randint(0, 9))
            + rng.choice(letras) + str(rng.randint(0, 9)) + str(rng.randint(0, 9)))
