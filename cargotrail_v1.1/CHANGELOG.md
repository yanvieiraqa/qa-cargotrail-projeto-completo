# Histórico de versões

## 1.1.0 — 12/11/2026
Versão do ciclo 2 de testes: correções dos defeitos do ciclo 1 e tela de ajuda.

**Novidades**
- Botão **? Como usar** na faixa superior (e na tela de login), com o guia de uso em sete módulos: Visão geral, Login e acesso, Administrador, Operador, Financeiro, Receitas rápidas e Glossário. Também abre pelo endereço `#/ajuda`.
- Versão do programa visível na tela de ajuda e no Swagger.

**Correções**
- BUG-001: as telas agora são bloqueadas por perfil e mostram "Acesso negado" quando o endereço é digitado na barra do navegador.
- BUG-002: o botão "Enviar para contratação" fica ativo e mostra a mensagem "Vincule uma NF-e antes de enviar".
- BUG-003: o botão "Cancelar pedido" fica ativo e mostra a mensagem "Cancele o CT-e antes de cancelar o pedido".
- BUG-004: os campos de valor (frete, valor da carga e desconto) aceitam formato de moeda, com ponto de milhar e vírgula (ex.: 3.119,99).
- BUG-005: pedidos criados pelo painel do simulador passam a ter o XML completo; "Ver MDF-e" mostra os dados corretos.
- BUG-006: o encerramento do MDF-e pede confirmação.

**Outras mudanças**
- A liberação de pagamento pede confirmação com o valor.
- Mensagens negativas (rejeição, indisponibilidade, recusa, sessão expirada e outras) aparecem sempre em vermelho.
- "Restaurar padrão" do simulador passou a ser uma operação única, sem apagar e recriar as tabelas.
- No Docker, o banco passou a ficar em um volume do próprio Docker (`dados`), em vez de uma pasta do Windows, para evitar falhas intermitentes do SQLite. Para zerar tudo: `docker compose down -v`. Modo do banco configurável em `SQLITE_JOURNAL_MODE` (padrão WAL).
- O painel do simulador ("Preparar pedido de teste") passou a devolver também os ids do CT-e e do MDF-e criados (`cte_id` e `mdfe_id`).
- Erros internos agora voltam como mensagem do sistema e ficam registrados em `docker compose logs sistema`.
- Exemplo comentado de `SESSAO_TIMEOUT_S` no `docker-compose.yml`.

## 1.0.0 — 31/10/2026
Primeira versão: sistema, simulador (SEFAZ, CIOT e pagamento) e interface web. Versão testada no ciclo 1.
