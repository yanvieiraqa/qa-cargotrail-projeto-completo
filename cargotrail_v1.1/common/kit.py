"""Documentação OpenAPI automática (sem dependências extras além do Flask).

Cada rota é registrada com `doc(...)`, que alimenta o arquivo /openapi.json e a
página Swagger UI em /docs (o Swagger UI é carregado de um CDN público, de graça).
"""
import re

SWAGGER_VERSAO = "5.17.14"
_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")

PAGINA_DOCS = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>{titulo} — Documentação da API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{v}/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{v}/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({{url: '/openapi.json', dom_id: '#swagger-ui'}});</script>
</body></html>"""


def _schema_campo(texto):
    """'string*' -> obrigatório. 'number', 'integer', 'boolean', 'object', 'string' (opcional)."""
    obrigatorio = texto.endswith("*")
    tipo = texto.rstrip("*")
    return {"type": tipo}, obrigatorio


class OpenApi:
    def __init__(self, titulo, versao, descricao):
        self.titulo = titulo
        self.spec = {
            "openapi": "3.0.3",
            "info": {"title": titulo, "version": versao, "description": descricao},
            "paths": {},
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
        }

    def doc(self, regra, metodos, resumo, tag, corpo=None, autenticada=False, respostas=None):
        caminho = _PARAM.sub(lambda m: "{" + m.group(2) + "}", regra)
        params = [
            {"name": nome, "in": "path", "required": True,
             "schema": {"type": "integer" if tipo == "int" else "string"}}
            for tipo, nome in _PARAM.findall(regra)
        ]
        item = self.spec["paths"].setdefault(caminho, {})
        for metodo in metodos:
            if metodo in ("OPTIONS", "HEAD"):
                continue
            op = {"summary": resumo, "tags": [tag], "parameters": params,
                  "responses": {str(c): {"description": d} for c, d in (respostas or {"200": "Sucesso"}).items()}}
            if autenticada:
                op["security"] = [{"bearer": []}]
            if corpo and metodo in ("POST", "PUT", "PATCH"):
                props, req = {}, []
                for campo, texto in corpo.items():
                    props[campo], obrigatorio = _schema_campo(texto)
                    if obrigatorio:
                        req.append(campo)
                esquema = {"type": "object", "properties": props}
                if req:
                    esquema["required"] = req
                op["requestBody"] = {"required": bool(req), "content": {"application/json": {"schema": esquema}}}
            item[metodo.lower()] = op

    def registrar_paginas(self, app):
        from flask import jsonify

        @app.get("/openapi.json")
        def openapi_json():
            return jsonify(self.spec)

        @app.get("/docs")
        def docs():
            return PAGINA_DOCS.format(titulo=self.titulo, v=SWAGGER_VERSAO)
