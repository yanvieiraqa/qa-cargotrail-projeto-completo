# CargoTrail — TMS Simulator & QA Portfolio

Projeto de portfólio de QA com um sistema de gestão de transportes (TMS) **fictício**, construído para praticar testes de software em um domínio real da logística: do pedido ao pagamento do frete, passando pela emissão de CT-e, CIOT e MDF-e, pelo acompanhamento da viagem e pela comprovação da entrega. Os serviços externos (SEFAZ, CIOT e pagamento) são **simulados** por um serviço próprio, e nenhum documento oficial é emitido.

O projeto foi pensado para dois usos: demonstrar planejamento, execução e documentação de testes e servir de **ambiente de treino** para quem quer estudar QA.

## Objetivo

Praticar o ciclo completo de testes de um sistema com regras de negócio encadeadas: análise das regras, plano de testes, casos manuais, testes de API no Postman, registro de bugs, evidências, rastreabilidade e, na próxima etapa, automação.

## Escopo

- **Acesso:** login, perfis (Administrador, Operador e Financeiro), bloqueio de conta e expiração de sessão.
- **Cadastros:** embarcadores, transportadoras (RNTRC), motoristas (CNH) e veículos.
- **Pedido e carga:** dados da carga, chave de NF-e e estados do pedido.
- **Contratação:** transportadora, motorista, veículo, capacidade e piso mínimo de frete.
- **Execução:** CT-e, CIOT e MDF-e, ICMS, chave de acesso, cancelamento, encerramento e falhas da SEFAZ simulada.
- **Monitoramento da viagem:** estados, avanço automático por tempo, ocorrências e alerta de atraso.
- **Comprovação de entrega** e **Pagamento do frete:** divergências, desconto por avaria, duplicidade e recusa.
- **Painel do simulador:** cenários (autorizar, rejeitar, demorar, fora do ar), pedidos de teste e restauração do padrão.
- **Ponta a ponta:** jornada completa do pedido ao pagamento.

## Ferramentas e ambiente

- Testes manuais e de API: navegador, Swagger e **Postman**
- Programa: Python (Flask), SQLite, HTML, CSS e JavaScript
- Execução: **Docker** e Docker Compose
- Automação (próxima etapa): Python (pytest e Playwright) e, depois, Java (RestAssured e Selenium)

## Resultado registrado

| Indicador | Ciclo 1 (v1.0.0) | Resultado final (v1.1.0) |
|---|---:|---:|
| Regras de negócio com casos de teste | 60 | 60 |
| Casos executados | 159 | 159 |
| Passaram | 152 | 159 |
| Falharam | 7 | 0 |
| Não executados | 0 | 0 |
| Bugs registrados | 6 | 6 corrigidos e retestados |
| Casos de API no Postman | 31 | 31 (Run sem falhas) |

No ciclo 1 (testes manuais na v1.0.0), 7 casos falharam. Eles viraram 6 bugs (BUG-001 a BUG-006) e 1 ajuste de caso de teste (CT-ACE-09, que não trazia o passo a passo para configurar o tempo da sessão). Todos foram corrigidos na **v1.1.0**, que também traz a tela de ajuda "? Como usar", e retestados com sucesso.

No Postman, o Run da v1.0.0 passou em todos os casos. Na primeira execução na v1.1.0, 3 requisições falharam; depois de ajustes preventivos o Run foi reexecutado sem falhas, e a causa raiz não foi confirmada. Uma observação segue em acompanhamento: um erro inesperado que ocorreu uma vez e não foi reproduzido (BUG-007). Os detalhes estão no Registro de Bugs e Observações.

A regressão completa será feita pela **automação**, que é a próxima etapa do projeto.

## Estrutura do repositório

```text
01 - Casos de Teste/
02 - Documentação/
03 - Evidências/
04 - Postman/
05 - Aplicação/
```

- `01 - Casos de Teste/`: os 159 casos, com passos, resultado esperado, resultado obtido e evidências.
- `02 - Documentação/`: plano de testes, regras de negócio, registro de bugs e observações, matriz de rastreabilidade e controle de execução.
- `03 - Evidências/`: uma pasta por módulo, com vídeos (.mp4) e prints (.png). Nomes no padrão `CT-MÓDULO-NÚMERO[_complemento].extensão`.
- `04 - Postman/`: collection, ambiente e a pasta `RUN/` com as execuções.
- `05 - Aplicação/`: o programa (`app/`, `simulator/`, `common/`), `docker-compose.yml`, `CHANGELOG.md` e `dev_tests/`.

## Documentação

- `Casos_de_Teste_CargoTrail.pdf`: casos detalhados, passos, resultados e evidências.
- `01_Plano_de_Testes_CargoTrail.pdf`: objetivo, escopo, estratégia, automação, critérios, riscos e resumo da execução.
- `02_Regras_de_Negocio_CargoTrail.pdf`: regras numeradas, estados, simulação da viagem e parâmetros fictícios.
- `03_Registro_de_Bugs_e_Observacoes_CargoTrail.pdf`: defeitos e observações.
- `04_Matriz_de_Rastreabilidade_CargoTrail.xlsx`: matriz regra × casos, controle de execução por ciclo e registro de bugs.
- `CHANGELOG.md`: histórico de versões do programa.

## Como executar o programa

Precisa do **Docker Desktop**. Na pasta `05 - Aplicação`:

```bash
docker compose up --build
```

- Sistema: <http://localhost:8000> — documentação da API em <http://localhost:8000/docs>
- Simulador: <http://localhost:8001/docs>
- Usuários de teste (senha `Teste@123`): `admin@cargotrail.test`, `operador@cargotrail.test` e `financeiro@cargotrail.test`
- Guia de uso dentro do programa, com os usuários de teste: botão **? Como usar**, na faixa superior de todas as telas
- Para zerar os dados: `docker compose down -v` e subir de novo (ou use "Restaurar padrão" no painel do simulador)

Sem Docker, no Windows com Python 3.10 ou mais novo, use o arquivo `iniciar.bat`.

## Como usar este projeto para estudar

1. Leia as **regras de negócio** e entenda a jornada do pedido ao pagamento.
2. Use o botão **? Como usar** e o painel do simulador para provocar rejeição, demora, indisponibilidade e recusa de pagamento.
3. Execute os casos de teste e compare com as evidências.
4. Importe a collection e o ambiente do **Postman** e rode os casos de API no Runner.
5. Escreva casos novos: o enunciado completo das regras está no documento de regras.

## Principais aprendizados

- Análise de regras de negócio encadeadas e criação de casos positivos, negativos e de limite.
- Uso de um simulador para testar falhas externas, como rejeição, demora e serviço fora do ar.
- Preparação de massa de teste e controle de estado entre os casos.
- Testes de API com Postman: status HTTP, mensagens e permissões por perfil.
- Registro de bugs com caso, severidade, versão e evidência, e reteste após a correção.
- Rastreabilidade entre regras de negócio, casos de teste e defeitos.

## Observações e limitações

- Todos os dados, documentos fiscais, empresas e serviços são fictícios. Valores de prazos, pisos de frete e alíquotas são de estudo.
- O programa foi construído com apoio de IA. O trabalho de QA (análise, casos, execução, evidências, bugs e documentação) é do responsável.
- A pasta `05 - Aplicação/dev_tests` traz testes de desenvolvimento do programa, separados da suíte de QA do projeto.
- Este projeto demonstra prática de QA e não representa certificação completa do sistema nem garantia de ausência de defeitos.

## Responsável

Yan Vieira — Projeto de estudo para portfólio de QA.
