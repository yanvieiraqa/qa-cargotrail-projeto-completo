/* CargoTrail — interface web. JavaScript puro, sem dependências. */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = (n) => (n == null ? "—" : Number(n).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }));
const fmtCnpj = (v) => String(v || "").replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5");
const fmtCpf = (v) => String(v || "").replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, "$1.$2.$3-$4");
const fmtData = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "—");
const fmtDia = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");

const state = {
  token: sessionStorage.getItem("ct_token"),
  user: JSON.parse(sessionStorage.getItem("ct_user") || "null"),
  cfg: { simulador_ativo: false },
  timer: null,
  aba: "dados",
};

// ------------------------------------------------------------------ infraestrutura
async function api(metodo, caminho, corpo) {
  const r = await fetch(caminho, {
    method: metodo,
    headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: "Bearer " + state.token } : {}) },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  let j = null;
  try { j = await r.json(); } catch (e) { /* sem corpo */ }
  if (r.status === 401 && state.token) {
    sair((j && j.erro) === "Sessão expirada" ? "Sessão expirada. Entre novamente." : "Entre novamente.");
    throw { status: 401, erro: "Sessão expirada" };
  }
  if (!r.ok) throw { status: r.status, erro: (j && j.erro) || "Erro inesperado", campos: j && j.campos };
  return j;
}

function toast(msg, tipo = "") {
  const t = document.createElement("div");
  t.className = "toast " + tipo;
  t.dataset.testid = "toast";
  t.textContent = msg;
  const caixa = $("#toasts");
  caixa.appendChild(t);
  while (caixa.children.length > 3) caixa.firstChild.remove();
  setTimeout(() => t.remove(), 6000);
}
const falha = (e) => { if (e.status !== 401) toast(e.erro || "Erro inesperado", "erro"); };

function sair(aviso) {
  if (state.token) fetch("/api/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + state.token } }).catch(() => {});
  state.token = null; state.user = null;
  sessionStorage.removeItem("ct_token"); sessionStorage.removeItem("ct_user");
  location.hash = "";
  rota();
  if (aviso) toast(aviso, "aviso");
}

function selo(texto, tipo) {
  const mapa = { Autorizado: "ok", Encerrado: "info", Cancelado: "erro", Rejeitado: "erro", "Pendente de envio": "pend", Pago: "ok", Pendente: "pend",
    "Pronto para pagar": "laranja", Concluído: "ok", Rascunho: "", "Aguardando contratação": "pend", Contratado: "info", "Em execução": "laranja",
    Ativo: "ok", Inativo: "", Bloqueado: "erro", "Em trânsito": "laranja", "No ponto de entrega": "info", Entregue: "ok", "Aguardando saída": "pend" };
  return `<span class="selo ${tipo ?? mapa[texto] ?? ""}">${esc(texto)}</span>`;
}

// ------------------------------------------------------------------ formulários e janelas
function campo(c, valor = "") {
  const v = valor ?? "";
  const id = `f_${c.n}`;
  let input;
  if (c.tipo === "select") {
    input = `<select name="${c.n}" id="${id}" data-testid="campo-${c.n}"><option value="">Selecione…</option>` +
      c.opcoes.map((o) => `<option value="${esc(o.v)}" ${String(o.v) === String(v) ? "selected" : ""} ${o.off ? "disabled" : ""}>${esc(o.t)}</option>`).join("") + "</select>";
  } else if (c.tipo === "textarea") {
    input = `<textarea name="${c.n}" id="${id}" rows="3" data-testid="campo-${c.n}">${esc(v)}</textarea>`;
  } else if (c.tipo === "checkbox") {
    return `<label class="campo marca"><input type="checkbox" name="${c.n}" data-testid="campo-${c.n}" ${v ? "checked" : ""}>${esc(c.rotulo)}</label>`;
  } else {
    input = `<input name="${c.n}" id="${id}" type="${c.tipo || "text"}" ${c.step ? `step="${c.step}"` : ""} value="${esc(v)}" placeholder="${esc(c.ph || "")}" data-testid="campo-${c.n}">`;
  }
  return `<label class="campo">${esc(c.rotulo)}${input}<span class="msg-erro" data-erro="${c.n}" data-testid="erro-${c.n}"></span></label>`;
}

function lerForm(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    o[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return o;
}

function janela(titulo, html, aoEnviar, { rotuloOk = "Salvar", testid = "janela" } = {}) {
  const fundo = document.createElement("div");
  fundo.className = "fundo";
  fundo.dataset.testid = testid;
  fundo.innerHTML = `<form class="janela" novalidate><h2>${esc(titulo)}</h2><div class="erro-geral" data-testid="erro-geral"></div>${html}
    <div class="barra"><button type="button" class="btn sec" data-fechar data-testid="janela-cancelar">Cancelar</button>
    <button type="submit" class="btn" data-testid="janela-ok">${esc(rotuloOk)}</button></div></form>`;
  document.body.appendChild(fundo);
  const form = $("form", fundo);
  const fechar = () => fundo.remove();
  $("[data-fechar]", fundo).onclick = fechar;
  const primeiro = form.querySelector("input,select,textarea");
  if (primeiro) primeiro.focus();
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    form.querySelectorAll("[data-erro]").forEach((s) => (s.textContent = ""));
    $(".erro-geral", form).classList.remove("vis");
    try {
      await aoEnviar(lerForm(form), form);
      fechar();
    } catch (e) {
      if (e.status === 401) return fechar();
      if (e.campos) {
        let algum = false;
        for (const [k, m] of Object.entries(e.campos)) {
          const s = form.querySelector(`[data-erro="${k}"]`);
          if (s) { s.textContent = m; algum = true; }
        }
        if (algum) return;
      }
      const g = $(".erro-geral", form);
      g.textContent = e.erro || "Erro inesperado";
      g.classList.add("vis");
    }
  };
  return fechar;
}

// ------------------------------------------------------------------ roteamento e estrutura
const MENU = {
  Administrador: [["#/painel", "Painel"], ["#/cadastros/embarcadores", "Cadastros"], ["#/usuarios", "Usuários"], ["#/pedidos", "Pedidos"], ["#/simulador", "Simulador"]],
  Operador: [["#/painel", "Painel"], ["#/pedidos", "Pedidos"]],
  Financeiro: [["#/painel", "Painel"], ["#/pagamentos", "Pagamentos"]],
};

async function rota() {
  clearInterval(state.timer);
  document.querySelectorAll(".fundo").forEach((f) => f.remove());
  try { state.cfg = await (await fetch("/api/config")).json(); } catch (e) { /* mantém */ }
  if (!state.token) return telaLogin();
  const hash = location.hash || "#/painel";
  const partes = hash.replace("#/", "").split("/");
  estrutura(partes[0]);
  const main = $("#main");
  try {
    if (partes[0] === "painel") await pgPainel(main);
    else if (partes[0] === "cadastros") await pgCadastros(main, partes[1] || "embarcadores");
    else if (partes[0] === "usuarios") await pgUsuarios(main);
    else if (partes[0] === "pedidos" && partes[1]) await pgPedido(main, Number(partes[1]));
    else if (partes[0] === "pedidos") await pgPedidos(main);
    else if (partes[0] === "pagamentos") await pgPagamentos(main);
    else if (partes[0] === "simulador") await pgSimulador(main);
    else main.innerHTML = `<p class="vazio">Página não encontrada.</p>`;
  } catch (e) {
    if (e.status === 403) main.innerHTML = `<h1>Acesso negado</h1><p data-testid="acesso-negado">Seu perfil não tem acesso a esta área.</p>`;
    else if (e.status !== 401) main.innerHTML = `<p class="vazio">${esc(e.erro || "Erro inesperado")}</p>`;
  }
}

function estrutura(atual) {
  const menu = MENU[state.user.perfil] || [];
  $("#app").innerHTML = `
    ${state.cfg.simulador_ativo ? `<div id="faixa" data-testid="faixa-simulacao">Ambiente de simulação — dados fictícios</div>` : ""}
    <header class="topo"><div class="logo">cargotrail<small>TMS Simulator</small></div>
      <div class="usuario"><span data-testid="usuario-nome">${esc(state.user.nome)}</span><span class="perfil" data-testid="usuario-perfil">${esc(state.user.perfil)}</span>
      <button class="btn sec peq" id="sair" data-testid="btn-sair">Sair</button></div></header>
    <div class="corpo"><nav data-testid="menu">${menu.map(([h, t]) => `<a href="${h}" class="${h.startsWith("#/" + atual) ? "ativo" : ""}" data-testid="menu-${t.toLowerCase()}">${t}</a>`).join("")}</nav>
    <main id="main"></main></div>`;
  $("#sair").onclick = () => sair();
}

function telaLogin() {
  $("#app").innerHTML = `
    ${state.cfg.simulador_ativo ? `<div id="faixa" data-testid="faixa-simulacao">Ambiente de simulação — dados fictícios</div>` : ""}
    <div class="login"><div class="lado-img"><span class="rot">Transportation management system</span>
      <h1>Do pedido<br>ao pagamento.</h1><p>Sistema fictício de gestão de transportes, criado para praticar testes de software. Todos os dados e documentos fiscais são simulados.</p></div>
      <div class="lado-form"><div class="logo">cargotrail</div><h2>Entrar</h2>
      <form id="login" novalidate><div class="erro-geral" id="erro-login" data-testid="erro-login"></div>
        ${campo({ n: "email", rotulo: "E-mail", tipo: "email" })}${campo({ n: "senha", rotulo: "Senha", tipo: "password" })}
        <button class="btn" type="submit" data-testid="login-submit">Entrar</button></form>
      <div class="dica"><b>Usuários de teste</b> (senha <code>Teste@123</code>):<br><code>admin@cargotrail.test</code> · <code>operador@cargotrail.test</code> · <code>financeiro@cargotrail.test</code></div></div></div>`;
  $("#login").onsubmit = async (ev) => {
    ev.preventDefault();
    const d = lerForm(ev.target);
    const box = $("#erro-login");
    box.classList.remove("vis");
    try {
      const r = await api("POST", "/api/auth/login", { email: d.email, senha: d.senha });
      state.token = r.token; state.user = r.usuario;
      sessionStorage.setItem("ct_token", r.token); sessionStorage.setItem("ct_user", JSON.stringify(r.usuario));
      location.hash = "#/painel";
      rota();
    } catch (e) { box.textContent = e.erro; box.classList.add("vis"); }
  };
}
window.addEventListener("hashchange", rota);

// ------------------------------------------------------------------ painel
async function pgPainel(main) {
  let cartoes = "";
  if (state.user.perfil === "Financeiro") {
    const l = await api("GET", "/api/pagamentos");
    const c = {};
    l.forEach((p) => (c[p.situacao] = (c[p.situacao] || 0) + 1));
    cartoes = ["Aguardando entrega", "Aguardando encerramento do MDF-e", "Pronto para pagar", "Pendente", "Pago"].map((s) => `<div class="cartao"><div class="n">${c[s] || 0}</div><div class="r">${s}</div></div>`).join("");
  } else {
    const l = await api("GET", "/api/pedidos");
    const c = {};
    l.forEach((p) => (c[p.status] = (c[p.status] || 0) + 1));
    cartoes = ["Rascunho", "Aguardando contratação", "Contratado", "Em execução", "Concluído", "Cancelado"].map((s) => `<div class="cartao"><div class="n">${c[s] || 0}</div><div class="r">${s}</div></div>`).join("");
  }
  main.innerHTML = `<div class="painel-hero"><h1>Olá, ${esc(state.user.nome)}.</h1><p>Do pedido ao pagamento, tudo num único fluxo. Ambiente educacional com serviços simulados.</p></div>
    <div class="cartoes" data-testid="cartoes">${cartoes}</div>`;
}

// ------------------------------------------------------------------ cadastros
const TIPOS_VEICULO = ["Van", "Caminhão 3/4", "Truck", "Carreta"];
const CAD = {
  embarcadores: { titulo: "Embarcadores", um: "embarcador", novo: "Novo embarcador", cols: [["razao_social", "Razão social"], ["cnpj", "CNPJ", fmtCnpj], ["email", "E-mail"], ["telefone", "Telefone"], ["cep", "CEP"]],
    campos: [{ n: "razao_social", rotulo: "Razão social" }, { n: "cnpj", rotulo: "CNPJ", ph: "00.000.000/0000-00" }, { n: "email", rotulo: "E-mail" }, { n: "telefone", rotulo: "Telefone", ph: "(11) 4000-0000" }, { n: "cep", rotulo: "CEP", ph: "00000-000" }] },
  transportadoras: { titulo: "Transportadoras", um: "transportadora", novo: "Nova transportadora", cols: [["razao_social", "Razão social"], ["cnpj", "CNPJ", fmtCnpj], ["rntrc", "RNTRC"], ["rntrc_validade", "Validade RNTRC", (v, r) => fmtDia(v) + (r.rntrc_vencido ? " ⚠ vencido" : "")]],
    campos: [{ n: "razao_social", rotulo: "Razão social" }, { n: "cnpj", rotulo: "CNPJ" }, { n: "rntrc", rotulo: "RNTRC" }, { n: "rntrc_validade", rotulo: "Validade do RNTRC", tipo: "date" }, { n: "email", rotulo: "E-mail" }, { n: "telefone", rotulo: "Telefone" }, { n: "cep", rotulo: "CEP" }] },
  motoristas: { titulo: "Motoristas", um: "motorista", novo: "Novo motorista", cols: [["nome", "Nome"], ["cpf", "CPF", fmtCpf], ["cnh_categoria", "CNH"], ["cnh_validade", "Validade CNH", fmtDia], ["transportadora_id", "Transportadora", (v, r, ctx) => ctx.transp[v] || v]],
    campos: [{ n: "nome", rotulo: "Nome" }, { n: "cpf", rotulo: "CPF" }, { n: "cnh_categoria", rotulo: "Categoria da CNH", tipo: "select", opcoes: ["A", "B", "C", "D", "E"].map((x) => ({ v: x, t: x })) }, { n: "cnh_validade", rotulo: "Validade da CNH", tipo: "date" }, { n: "transportadora_id", rotulo: "Transportadora", tipo: "select", fonte: "transportadoras" }] },
  veiculos: { titulo: "Veículos", um: "veículo", novo: "Novo veículo", cols: [["placa", "Placa"], ["tipo", "Tipo"], ["capacidade_kg", "Capacidade (kg)", (v) => Number(v).toLocaleString("pt-BR")], ["transportadora_id", "Transportadora", (v, r, ctx) => ctx.transp[v] || v]],
    campos: [{ n: "placa", rotulo: "Placa", ph: "ABC1D23 ou ABC-1234" }, { n: "tipo", rotulo: "Tipo", tipo: "select", opcoes: TIPOS_VEICULO.map((x) => ({ v: x, t: x })) }, { n: "capacidade_kg", rotulo: "Capacidade (kg)", tipo: "number" }, { n: "transportadora_id", rotulo: "Transportadora", tipo: "select", fonte: "transportadoras" }] },
};

async function pgCadastros(main, ent) {
  const cfg = CAD[ent];
  if (!cfg) { main.innerHTML = `<p class="vazio">Cadastro desconhecido.</p>`; return; }
  const [lista, transp] = await Promise.all([api("GET", `/api/${ent}`), api("GET", "/api/transportadoras")]);
  const ctx = { transp: Object.fromEntries(transp.map((t) => [t.id, t.razao_social])) };
  const abas = Object.entries(CAD).map(([k, c]) => `<a class="aba ${k === ent ? "ativa" : ""}" href="#/cadastros/${k}" data-testid="aba-${k}">${c.titulo}</a>`).join("");
  main.innerHTML = `<h1>Cadastros</h1><div class="abas">${abas}</div>
    <div class="barra"><button class="btn" id="novo" data-testid="btn-novo">${cfg.novo}</button></div>
    <table data-testid="tabela-${ent}"><thead><tr>${cfg.cols.map((c) => `<th>${c[1]}</th>`).join("")}<th>Situação</th><th></th></tr></thead><tbody>
    ${lista.map((r) => `<tr data-testid="linha-${ent}-${r.id}">${cfg.cols.map(([k, , f]) => `<td>${esc(f ? f(r[k], r, ctx) : r[k])}</td>`).join("")}<td>${selo(r.status)}${r.bloqueada_contratacao && r.ativo ? " " + selo("Bloqueada p/ contratação", "erro") : ""}</td>
    <td><button class="btn sec peq" data-acao="editar" data-id="${r.id}" data-testid="btn-editar-${r.id}">Editar</button>
    ${r.ativo ? `<button class="btn perigo peq" data-acao="inativar" data-id="${r.id}" data-testid="btn-inativar-${r.id}">Inativar</button>` : `<button class="btn sec peq" data-acao="ativar" data-id="${r.id}" data-testid="btn-ativar-${r.id}">Reativar</button>`}</td></tr>`).join("") || `<tr><td class="vazio" colspan="9">Nenhum registro.</td></tr>`}</tbody></table>`;

  const form = (titulo, registro) => {
    const html = cfg.campos.map((c) => campo(c.fonte ? { ...c, opcoes: transp.map((t) => ({ v: t.id, t: t.razao_social })) } : c, registro ? registro[c.n] : "")).join("");
    janela(titulo, html, async (d) => {
      const corpo = { ...d };
      if (corpo.transportadora_id !== undefined) corpo.transportadora_id = corpo.transportadora_id === "" ? "" : Number(corpo.transportadora_id);
      if (registro) await api("PUT", `/api/${ent}/${registro.id}`, corpo); else await api("POST", `/api/${ent}`, corpo);
      toast(registro ? "Cadastro atualizado." : "Cadastro criado.");
      rota();
    }, { testid: "janela-cadastro" });
  };
  $("#novo").onclick = () => form(cfg.novo, null);
  main.onclick = async (ev) => {
    const b = ev.target.closest("[data-acao]");
    if (!b) return;
    const reg = lista.find((x) => x.id === Number(b.dataset.id));
    try {
      if (b.dataset.acao === "editar") form(`Editar ${cfg.um}`, reg);
      else if (b.dataset.acao === "inativar") { if (!confirm("Inativar este cadastro?")) return; await api("POST", `/api/${ent}/${reg.id}/inativar`); toast("Cadastro inativado."); rota(); }
      else if (b.dataset.acao === "ativar") { await api("POST", `/api/${ent}/${reg.id}/ativar`); toast("Cadastro reativado."); rota(); }
    } catch (e) { falha(e); }
  };
}

async function pgUsuarios(main) {
  const l = await api("GET", "/api/usuarios");
  main.innerHTML = `<h1>Usuários</h1><table data-testid="tabela-usuarios"><thead><tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Situação</th><th></th></tr></thead><tbody>
    ${l.map((u) => `<tr><td>${esc(u.nome)}</td><td>${esc(u.email)}</td><td>${esc(u.perfil)}</td><td>${selo(u.status)}</td>
    <td>${u.status === "Bloqueado" ? `<button class="btn peq" data-acao="desbloquear" data-id="${u.id}" data-testid="btn-desbloquear-${u.id}">Desbloquear</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
  main.onclick = async (ev) => {
    const b = ev.target.closest("[data-acao='desbloquear']");
    if (!b || !confirm("Desbloquear este usuário?")) return;
    try { await api("POST", `/api/usuarios/${b.dataset.id}/desbloquear`); toast("Usuário desbloqueado."); rota(); } catch (e) { falha(e); }
  };
}

// ------------------------------------------------------------------ pedidos
const ESTADOS_PEDIDO = ["Rascunho", "Aguardando contratação", "Contratado", "Em execução", "Concluído", "Cancelado"];

async function pgPedidos(main) {
  const filtro = state.filtroStatus || "";
  const [lista, emb] = await Promise.all([api("GET", "/api/pedidos" + (filtro ? `?status=${encodeURIComponent(filtro)}` : "")), api("GET", "/api/embarcadores")]);
  const op = state.user.perfil === "Operador";
  main.innerHTML = `<h1>Pedidos</h1>
    <div class="barra">${op ? `<button class="btn" id="novo" data-testid="btn-novo-pedido">Novo pedido</button>` : ""}
    <select id="filtro" data-testid="filtro-status"><option value="">Todos os status</option>${ESTADOS_PEDIDO.map((s) => `<option ${s === filtro ? "selected" : ""}>${s}</option>`).join("")}</select></div>
    <table data-testid="tabela-pedidos"><thead><tr><th>Nº</th><th>Embarcador</th><th>Rota</th><th>Peso (kg)</th><th>Valor da carga</th><th>Status</th><th>Viagem</th></tr></thead><tbody>
    ${lista.map((p) => `<tr data-testid="linha-pedido-${p.id}"><td><a href="#/pedidos/${p.id}" data-testid="link-pedido-${p.id}">#${p.id}</a></td><td>${esc(p.embarcador.razao_social)}</td><td>${esc(p.origem)} → ${esc(p.destino)}</td>
    <td>${Number(p.peso_kg).toLocaleString("pt-BR")}</td><td>${brl(p.valor_carga)}</td><td>${selo(p.status)}</td><td>${p.viagem ? `${esc(p.viagem.numero)} ${selo(p.viagem.estado)}` : "—"}</td></tr>`).join("") || `<tr><td colspan="7" class="vazio">Nenhum pedido.</td></tr>`}</tbody></table>`;
  $("#filtro").onchange = (e) => { state.filtroStatus = e.target.value; rota(); };
  if (op) $("#novo").onclick = () => {
    const campos = [{ n: "embarcador_id", rotulo: "Embarcador", tipo: "select", opcoes: emb.filter((e) => e.ativo).map((e) => ({ v: e.id, t: e.razao_social })) },
      { n: "origem", rotulo: "Origem", ph: "São Paulo/SP" }, { n: "destino", rotulo: "Destino", ph: "Belo Horizonte/MG" },
      { n: "distancia_km", rotulo: "Distância (km)", tipo: "number" }, { n: "produto", rotulo: "Produto" }, { n: "peso_kg", rotulo: "Peso (kg)", tipo: "number" },
      { n: "valor_carga", rotulo: "Valor da carga (R$)", tipo: "number", step: "0.01" }, { n: "nfe_chave", rotulo: "Chave da NF-e (opcional)", ph: "44 dígitos" }];
    janela("Novo pedido", campos.map((c) => campo(c)).join(""), async (d) => {
      if (d.embarcador_id) d.embarcador_id = Number(d.embarcador_id);
      if (!d.nfe_chave) delete d.nfe_chave;
      const p = await api("POST", "/api/pedidos", d);
      toast(`Pedido #${p.id} criado.`);
      location.hash = `#/pedidos/${p.id}`;
    }, { testid: "janela-pedido" });
  };
}

const ETAPAS = ["Pedido", "Contratação", "CT-e", "CIOT", "MDF-e", "Viagem", "Comprovação", "Pagamento"];
function etapaAtual(p) {
  const docs = p.documentos || [];
  const aut = (t) => docs.some((d) => d.tipo_codigo === t && ["Autorizado", "Encerrado"].includes(d.status));
  const v = p.viagem;
  if (p.status === "Concluído") return 8;
  if (v && v.estado === "Entregue") return 7;
  if (v && ["Em trânsito", "No ponto de entrega"].includes(v.estado)) return v.estado === "Em trânsito" ? 5 : 6;
  if (aut("MDFE")) return 5;
  if (v && v.ciot) return 4;
  if (aut("CTE")) return 3;
  if (p.status === "Contratado") return 2;
  if (p.status === "Aguardando contratação") return 1;
  return 0;
}

async function pgPedido(main, id) {
  let p = await api("GET", `/api/pedidos/${id}`);
  const op = state.user.perfil === "Operador";
  const cad = op ? await Promise.all([api("GET", "/api/transportadoras"), api("GET", "/api/motoristas"), api("GET", "/api/veiculos")]) : null;
  const PISO = { Van: 2.2, "Caminhão 3/4": 3.1, Truck: 4.0, Carreta: 5.2 };
  const recarregar = async () => { p = await api("GET", `/api/pedidos/${id}`); desenhar(); };
  const agir = (fn, ok) => async () => { try { const r = await fn(); if (ok || (r && r.mensagem)) toast((r && r.mensagem) || ok); await recarregar(); } catch (e) { falha(e); } };
  const pedirMotivo = (titulo, enviar) => janela(titulo, campo({ n: "motivo", rotulo: "Motivo", tipo: "textarea" }), async (d) => { await enviar(d.motivo); await recarregar(); }, { rotuloOk: "Confirmar", testid: "janela-motivo" });

  function desenhar() {
    const a = p.acoes, v = p.viagem;
    const atual = etapaAtual(p);
    const etapas = ETAPAS.map((e, i) => `<div class="etapa ${i < atual ? "feita" : i === atual ? "atual" : ""}" data-testid="etapa-${i}">${e}</div>`).join("");
    const abas = [["dados", "Dados e NF-e"], ["contratacao", "Contratação"], ["documentos", "Documentos"], ["viagem", "Viagem"], ["historico", "Histórico"]];
    main.innerHTML = `<p><a href="#/pedidos">← Pedidos</a></p>
      <h1>Pedido #${p.id} ${selo(p.status).replace("<span", '<span data-testid="status-pedido"')}</h1>
      <div class="etapas" data-testid="linha-do-tempo">${etapas}</div>
      <div class="abas">${abas.map(([k, t]) => `<button class="aba ${state.aba === k ? "ativa" : ""}" data-aba="${k}" data-testid="aba-${k}">${t}</button>`).join("")}</div>
      <div id="aba-conteudo"></div>`;
    main.querySelectorAll("[data-aba]").forEach((b) => (b.onclick = () => { state.aba = b.dataset.aba; desenhar(); }));
    ({ dados: abaDados, contratacao: abaContratacao, documentos: abaDocumentos, viagem: abaViagem, historico: abaHistorico })[state.aba]($("#aba-conteudo"));
    clearInterval(state.timer);
    if (state.aba === "viagem" && v && ["Aguardando saída", "Em trânsito"].includes(v.estado)) {
      state.timer = setInterval(async () => {
        try { const novo = await api("GET", `/api/pedidos/${id}`); if (JSON.stringify(novo) !== JSON.stringify(p) && !document.querySelector(".fundo")) { p = novo; desenhar(); } } catch (e) { /* ignora */ }
      }, 2000);
    }
  }

  const dado = (r, v, tid) => `<div class="dado"><div class="r">${r}</div><div class="v" ${tid ? `data-testid="${tid}"` : ""}>${v}</div></div>`;

  function abaDados(el) {
    const a = p.acoes;
    el.innerHTML = `<div class="bloco"><div class="grade">${dado("Embarcador", esc(p.embarcador.razao_social))}${dado("Origem", esc(p.origem))}${dado("Destino", esc(p.destino))}
      ${dado("Distância", p.distancia_km + " km")}${dado("Produto", esc(p.produto))}${dado("Peso", Number(p.peso_kg).toLocaleString("pt-BR") + " kg")}${dado("Valor da carga", brl(p.valor_carga))}
      ${dado("Chave da NF-e", p.nfe_chave ? `<span class="mono">${esc(p.nfe_chave)}</span>` : "—", "nfe-chave")}</div></div>
      ${op ? `<div class="bloco"><h2>NF-e</h2><div class="barra"><input id="nfe" style="min-width:360px;padding:9px" placeholder="Chave de acesso com 44 dígitos" data-testid="input-nfe" ${a.vincular_nfe ? "" : "disabled"}>
      <button class="btn" data-acao="nfe" data-testid="btn-vincular-nfe" ${a.vincular_nfe ? "" : "disabled"}>Vincular NF-e</button></div><span class="msg-erro" id="erro-nfe" data-testid="erro-nfe"></span></div>
      <div class="barra"><button class="btn" data-acao="enviar" data-testid="btn-enviar-contratacao" ${a.enviar_contratacao ? "" : "disabled"}>Enviar para contratação</button>
      <button class="btn perigo" data-acao="cancelar" data-testid="btn-cancelar-pedido" ${a.cancelar ? "" : "disabled"}>Cancelar pedido</button></div>` : ""}
      ${p.motivo_cancelamento ? `<div class="alerta">Pedido cancelado. Motivo: ${esc(p.motivo_cancelamento)}</div>` : ""}`;
    el.onclick = async (ev) => {
      const b = ev.target.closest("[data-acao]");
      if (!b || b.disabled) return;
      if (b.dataset.acao === "nfe") {
        $("#erro-nfe").textContent = "";
        try { await api("POST", `/api/pedidos/${id}/nfe`, { nfe_chave: $("#nfe").value }); toast("NF-e vinculada."); await recarregar(); }
        catch (e) { if (e.campos && e.campos.nfe_chave) $("#erro-nfe").textContent = e.campos.nfe_chave; else falha(e); }
      } else if (b.dataset.acao === "enviar") agir(() => api("POST", `/api/pedidos/${id}/enviar`), "Pedido enviado para contratação.")();
      else if (b.dataset.acao === "cancelar") pedirMotivo("Cancelar pedido", async (m) => { await api("POST", `/api/pedidos/${id}/cancelar`, { motivo: m }); toast("Pedido cancelado."); });
    };
  }

  function abaContratacao(el) {
    const a = p.acoes, v = p.viagem;
    let html = "";
    if (v) {
      html += `<div class="bloco"><h2>Viagem ${esc(v.numero)}</h2><div class="grade">${dado("Transportadora", esc(v.transportadora.razao_social))}${dado("Motorista", esc(v.motorista.nome))}
        ${dado("Veículo", esc(v.veiculo.placa) + " · " + esc(v.veiculo.tipo))}${dado("Frete", brl(v.frete), "frete-valor")}</div>
        ${op ? `<div class="barra"><button class="btn sec" data-acao="frete" data-testid="btn-alterar-frete" ${a.alterar_frete ? "" : "disabled"}>Alterar frete</button></div>` : ""}</div>`;
    } else if (op && a.contratar) {
      const [ts] = cad;
      html += `<div class="bloco"><h2>Contratar</h2><form id="form-contratar" novalidate><div class="erro-geral" data-testid="erro-geral"></div><div class="grade">
        ${campo({ n: "transportadora_id", rotulo: "Transportadora", tipo: "select", opcoes: ts.map((t) => ({ v: t.id, t: t.razao_social + (!t.ativo ? " (inativa)" : t.rntrc_vencido ? " (bloqueada — RNTRC vencido)" : ""), off: !t.ativo || t.rntrc_vencido })) })}
        ${campo({ n: "motorista_id", rotulo: "Motorista", tipo: "select", opcoes: [] })}${campo({ n: "veiculo_id", rotulo: "Veículo", tipo: "select", opcoes: [] })}
        ${campo({ n: "frete", rotulo: "Frete (R$)", tipo: "number", step: "0.01" })}</div>
        <p class="vazio" id="piso" data-testid="piso-minimo">Escolha o veículo para ver o piso mínimo de frete.</p>
        <button class="btn" type="submit" data-testid="btn-contratar">Confirmar contratação</button></form></div>`;
    } else html += `<p class="vazio">O pedido ainda não foi contratado.</p>`;
    el.innerHTML = html;
    if (op && a.contratar) {
      const [ts, ms, vs] = cad, f = $("#form-contratar");
      const atualizar = () => {
        const t = Number(f.transportadora_id.value);
        const opt = (arr, tx) => `<option value="">Selecione…</option>` + arr.filter((x) => x.transportadora_id === t).map((x) => `<option value="${x.id}" ${x.ativo ? "" : "disabled"}>${esc(tx(x))}${x.ativo ? "" : " (inativo)"}</option>`).join("");
        f.motorista_id.innerHTML = opt(ms, (m) => `${m.nome} — CNH ${m.cnh_categoria}`);
        f.veiculo_id.innerHTML = opt(vs, (x) => `${x.placa} — ${x.tipo} (${Number(x.capacidade_kg).toLocaleString("pt-BR")} kg)`);
        piso();
      };
      const piso = () => {
        const x = vs.find((y) => y.id === Number(f.veiculo_id.value));
        $("#piso").textContent = x ? `Piso mínimo para ${x.tipo} em ${p.distancia_km} km: ${brl(p.distancia_km * PISO[x.tipo])}` : "Escolha o veículo para ver o piso mínimo de frete.";
      };
      f.transportadora_id.onchange = atualizar; f.veiculo_id.onchange = piso;
      f.onsubmit = async (ev) => {
        ev.preventDefault();
        const d = lerForm(f), g = $(".erro-geral", f);
        g.classList.remove("vis");
        try {
          await api("POST", `/api/pedidos/${id}/contratar`, { transportadora_id: Number(d.transportadora_id) || "", motorista_id: Number(d.motorista_id) || "", veiculo_id: Number(d.veiculo_id) || "", frete: d.frete });
          toast("Contratação confirmada."); state.aba = "contratacao"; await recarregar();
        } catch (e) {
          if (e.status === 401) return;
          g.textContent = e.erro; g.classList.add("vis");
          if (e.campos) Object.entries(e.campos).forEach(([k, m]) => { const s = f.querySelector(`[data-erro="${k}"]`); if (s) s.textContent = m; });
        }
      };
    }
    el.onclick = (ev) => {
      const b = ev.target.closest("[data-acao='frete']");
      if (!b || b.disabled) return;
      janela("Alterar frete", campo({ n: "frete", rotulo: "Novo frete (R$)", tipo: "number", step: "0.01" }, p.viagem.frete), async (d) => {
        await api("POST", `/api/pedidos/${id}/frete`, { frete: d.frete }); toast("Frete alterado."); await recarregar();
      }, { testid: "janela-frete" });
    };
  }

  function abaDocumentos(el) {
    const a = p.acoes;
    const docs = p.documentos;
    el.innerHTML = `${op ? `<div class="barra"><button class="btn" data-acao="cte" data-testid="btn-emitir-cte" ${a.emitir_cte ? "" : "disabled"}>Emitir CT-e</button>
      <button class="btn" data-acao="ciot" data-testid="btn-gerar-ciot" ${a.gerar_ciot ? "" : "disabled"}>Gerar CIOT</button>
      <button class="btn" data-acao="mdfe" data-testid="btn-emitir-mdfe" ${a.emitir_mdfe ? "" : "disabled"}>Emitir MDF-e</button></div>` : ""}
      ${p.viagem && p.viagem.ciot ? `<p>CIOT: <b class="mono" data-testid="ciot-numero">${esc(p.viagem.ciot)}</b></p>` : ""}
      <table data-testid="tabela-documentos"><thead><tr><th>Tipo</th><th>Status</th><th>Chave de acesso</th><th>Protocolo</th><th>Detalhes</th><th></th></tr></thead><tbody>
      ${docs.map((d) => `<tr data-testid="doc-${d.id}"><td><b>${d.tipo}</b></td><td>${selo(d.status).replace("<span", '<span data-testid="doc-status-' + d.id + '"')}</td><td class="mono">${esc(d.chave)}</td><td class="mono">${esc(d.protocolo || "—")}</td>
      <td>${d.rejeicao_codigo ? `<span data-testid="doc-rejeicao-${d.id}">Rejeição ${esc(d.rejeicao_codigo)}: ${esc(d.rejeicao_motivo)}</span><br>` : ""}${d.correcao ? `Correção: ${esc(d.correcao)}<br>` : ""}
      ${d.icms_valor != null ? `ICMS ${d.icms_aliquota}%: ${brl(d.icms_valor)}<br>` : ""}${d.autorizado_em ? `Autorizado em ${fmtData(d.autorizado_em)}` : ""}</td>
      <td>${op ? `<button class="btn sec peq" data-acao="ver" data-id="${d.id}" data-testid="btn-ver-${d.id}">Ver ${d.tipo}</button>
      <button class="btn sec peq" data-acao="xml" data-id="${d.id}" data-chave="${esc(d.chave)}" data-testid="btn-xml-${d.id}">XML</button>
      <button class="btn sec peq" data-acao="reenviar" data-id="${d.id}" data-status="${d.status}" data-testid="btn-reenviar-${d.id}" ${d.acoes.reenviar ? "" : "disabled"}>Reenviar ${d.tipo}</button>
      <button class="btn perigo peq" data-acao="cancelar-doc" data-id="${d.id}" data-testid="btn-cancelar-doc-${d.id}" ${d.acoes.cancelar ? "" : "disabled"}>Cancelar ${d.tipo}</button>
      ${d.tipo_codigo === "MDFE" ? `<button class="btn peq" data-acao="encerrar" data-id="${d.id}" data-testid="btn-encerrar-${d.id}" ${d.acoes.encerrar ? "" : "disabled"}>Encerrar MDF-e</button>` : ""}` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="vazio">Nenhum documento emitido.</td></tr>`}</tbody></table>`;
    el.onclick = async (ev) => {
      const b = ev.target.closest("[data-acao]");
      if (!b || b.disabled) return;
      const did = b.dataset.id;
      const ac = b.dataset.acao;
      if (ac === "xml") baixarXml(did, b.dataset.chave);
      else if (ac === "ver") verDocumento(did);
      else if (ac === "cte") agir(() => api("POST", `/api/pedidos/${id}/cte`))();
      else if (ac === "ciot") agir(() => api("POST", `/api/pedidos/${id}/ciot`))();
      else if (ac === "mdfe") agir(() => api("POST", `/api/pedidos/${id}/mdfe`))();
      else if (ac === "encerrar") agir(() => api("POST", `/api/documentos/${did}/encerrar`))();
      else if (ac === "cancelar-doc") pedirMotivo("Cancelar documento", async (m) => { const r = await api("POST", `/api/documentos/${did}/cancelar`, { motivo: m }); toast(r.mensagem); });
      else if (ac === "reenviar") {
        if (b.dataset.status === "Rejeitado") janela("Reenviar documento rejeitado", campo({ n: "correcao", rotulo: "Correção realizada", tipo: "textarea" }), async (d) => { const r = await api("POST", `/api/documentos/${did}/reenviar`, { correcao: d.correcao }); toast(r.mensagem); await recarregar(); }, { rotuloOk: "Reenviar", testid: "janela-reenvio" });
        else agir(() => api("POST", `/api/documentos/${did}/reenviar`, {}))();
      }
    };
  }

  function abaViagem(el) {
    const v = p.viagem;
    if (!v) { el.innerHTML = `<p class="vazio">A viagem é criada na contratação.</p>`; return; }
    const ordem = ["Aguardando saída", "Em trânsito", "No ponto de entrega", "Entregue"];
    const i = ordem.indexOf(v.estado);
    el.innerHTML = `<div class="bloco"><h2>${esc(v.numero)} — <span data-testid="estado-viagem">${esc(v.estado)}</span></h2>
      <div class="etapas">${ordem.map((e, k) => `<div class="etapa ${k < i ? "feita" : k === i ? "atual" : ""}">${e}</div>`).join("")}</div>
      ${v.atraso ? `<div class="alerta atraso" data-testid="alerta-atraso">Viagem em atraso: passou da previsão de chegada.</div>` : ""}
      <div class="grade">${dado("Iniciada em", fmtData(v.iniciada_em))}${dado("Previsão de chegada", fmtData(v.previsao_chegada_em))}${dado("Chegada ao ponto de entrega", fmtData(v.chegada_em))}</div>
      ${op ? `<div class="barra"><button class="btn" data-acao="iniciar" data-testid="btn-iniciar-viagem" ${v.acoes.iniciar ? "" : "disabled"}>Iniciar viagem</button>
      <button class="btn sec" data-acao="ocorrencia" data-testid="btn-registrar-ocorrencia" ${v.acoes.registrar_ocorrencia ? "" : "disabled"}>Registrar ocorrência</button>
      <button class="btn" data-acao="entrega" data-testid="btn-confirmar-entrega" ${v.acoes.confirmar_entrega ? "" : "disabled"}>Confirmar entrega</button></div>` : ""}</div>
      ${v.comprovante ? `<div class="bloco" data-testid="comprovante"><h2>Comprovante de entrega</h2><div class="grade">${dado("Recebedor", esc(v.comprovante.recebedor_nome))}${dado("CPF", fmtCpf(v.comprovante.recebedor_cpf))}
        ${dado("Divergência", esc(v.comprovante.divergencia))}${dado("Desconto", brl(v.comprovante.desconto))}${dado("Registrado em", fmtData(v.comprovante.criado_em))}${v.comprovante.descricao ? dado("Descrição", esc(v.comprovante.descricao)) : ""}</div></div>` : ""}
      <div class="bloco"><h2>Ocorrências</h2>${v.ocorrencias.length ? `<table data-testid="tabela-ocorrencias"><tbody>${v.ocorrencias.map((o) => `<tr><td>${selo(o.tipo, "pend")}</td><td>${esc(o.descricao)}</td><td>${fmtData(o.criado_em)}</td><td>${esc(o.usuario)}</td></tr>`).join("")}</tbody></table>` : `<p class="vazio">Nenhuma ocorrência registrada.</p>`}</div>`;
    el.onclick = (ev) => {
      const b = ev.target.closest("[data-acao]");
      if (!b || b.disabled) return;
      if (b.dataset.acao === "iniciar") agir(() => api("POST", `/api/viagens/${v.id}/iniciar`), "Viagem iniciada.")();
      else if (b.dataset.acao === "ocorrencia") janela("Registrar ocorrência", campo({ n: "tipo", rotulo: "Tipo", tipo: "select", opcoes: ["Atraso", "Avaria", "Parada"].map((x) => ({ v: x, t: x })) }) + campo({ n: "descricao", rotulo: "Descrição", tipo: "textarea" }),
        async (d) => { await api("POST", `/api/viagens/${v.id}/ocorrencias`, d); toast("Ocorrência registrada."); await recarregar(); }, { testid: "janela-ocorrencia" });
      else if (b.dataset.acao === "entrega") janela("Confirmar entrega", campo({ n: "recebedor_nome", rotulo: "Nome de quem recebeu" }) + campo({ n: "recebedor_cpf", rotulo: "CPF de quem recebeu" }) +
        campo({ n: "divergencia", rotulo: "Divergência", tipo: "select", opcoes: ["Nenhuma", "Avaria", "Falta"].map((x) => ({ v: x, t: x })) }, "Nenhuma") + campo({ n: "descricao", rotulo: "Descrição da divergência", tipo: "textarea" }) +
        campo({ n: "desconto", rotulo: "Desconto no frete por avaria (R$)", tipo: "number", step: "0.01" }),
        async (d) => { await api("POST", `/api/viagens/${v.id}/comprovante`, d); toast("Entrega comprovada."); await recarregar(); }, { rotuloOk: "Confirmar", testid: "janela-entrega" });
    };
  }

  function abaHistorico(el) {
    el.innerHTML = `<ul class="eventos" data-testid="linha-do-tempo-eventos">${p.eventos.map((e) => `<li><div>${esc(e.texto)}</div><div class="h">${fmtData(e.criado_em)} · ${esc(e.origem)}</div></li>`).join("")}</ul>`;
  }

  state.aba = state.abaPedido === id ? state.aba : "dados";
  state.abaPedido = id;
  desenhar();
}


async function buscarXml(did) {
  const r = await fetch(`/api/documentos/${did}/xml`, { headers: { Authorization: "Bearer " + state.token } });
  if (!r.ok) throw { status: r.status, erro: "Não foi possível obter o XML" };
  return r.text();
}
async function baixarXml(did, chave) {
  try {
    const url = URL.createObjectURL(new Blob([await buscarXml(did)], { type: "application/xml" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${chave}.xml`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) { falha(e); }
}
async function verDocumento(did) {
  try {
    const xml = new DOMParser().parseFromString(await buscarXml(did), "application/xml");
    const g = (s) => (xml.querySelector(s) ? xml.querySelector(s).textContent : "—");
    const mdfe = !!xml.querySelector("MDFe");
    const linhas = mdfe
      ? [["Chave do MDF-e", g("chave")], ["Chave do CT-e", g("cte")], ["Motorista", g("motorista > nome")], ["CPF do motorista", fmtCpf(g("motorista > cpf"))], ["Veículo (placa)", g("veiculo > placa")], ["Origem", g("origem")], ["Destino", g("destino")]]
      : [["Chave do CT-e", g("chave")], ["Emitente", g("emitente > nome")], ["CNPJ", fmtCnpj(g("emitente > cnpj"))], ["Origem", g("origem")], ["Destino", g("destino")], ["NF-e", g("nfe")], ["Valor do frete", brl(g("valorFrete"))], ["ICMS", `${g("icms > aliquota")}% = ${brl(g("icms > valor"))}`]];
    janela(mdfe ? "MDF-e" : "CT-e", `<div class="grade" data-testid="detalhe-documento">${linhas.map(([r, v]) => `<div class="dado"><div class="r">${esc(r)}</div><div class="v mono">${esc(v)}</div></div>`).join("")}</div>`, async () => {}, { rotuloOk: "Fechar", testid: "janela-documento" });
  } catch (e) { falha(e); }
}

// ------------------------------------------------------------------ pagamentos
async function pgPagamentos(main) {
  const l = await api("GET", "/api/pagamentos");
  main.innerHTML = `<h1>Pagamentos</h1><table data-testid="tabela-pagamentos"><thead><tr><th>Viagem</th><th>Pedido</th><th>Frete</th><th>Desconto</th><th>A pagar</th><th>Situação</th><th></th></tr></thead><tbody>
    ${l.map((p) => `<tr data-testid="pag-${p.viagem_id}"><td>${esc(p.numero)}</td><td>#${p.pedido_id}</td><td>${brl(p.frete)}</td><td>${brl(p.desconto)}</td><td><b>${brl(p.valor_a_pagar)}</b></td>
    <td>${selo(p.situacao).replace("<span", '<span data-testid="pag-situacao-' + p.viagem_id + '"')}${p.motivo_bloqueio ? `<br><small>${esc(p.motivo_bloqueio)}</small>` : ""}
    ${p.tentativas.length ? `<details><summary>${p.tentativas.length} tentativa(s)</summary>${p.tentativas.map((t) => `<div><small>${fmtData(t.criado_em)} — ${esc(t.status)} ${t.motivo ? "(" + esc(t.motivo) + ")" : ""}</small></div>`).join("")}</details>` : ""}</td>
    <td><button class="btn peq" data-id="${p.viagem_id}" data-testid="btn-pagar-${p.viagem_id}" ${p.pode_pagar ? "" : "disabled"}>${p.situacao === "Pendente" ? "Tentar novamente" : "Liberar pagamento"}</button></td></tr>`).join("") || `<tr><td colspan="7" class="vazio">Nenhuma viagem.</td></tr>`}</tbody></table>`;
  main.onclick = async (ev) => {
    const b = ev.target.closest("button[data-id]");
    if (!b || b.disabled) return;
    try {
      const r = await api("POST", `/api/viagens/${b.dataset.id}/pagamento`);
      toast(r.mensagem, r.situacao === "Pago" ? "" : "aviso");
      rota();
    } catch (e) { falha(e); }
  };
}

// ------------------------------------------------------------------ painel do simulador
async function pgSimulador(main) {
  const [est, hist, viagens] = await Promise.all([api("GET", "/api/simulador/estado"), api("GET", "/api/simulador/historico"), api("GET", "/api/viagens")]);
  const SERV = [["sefaz", "SEFAZ (CT-e e MDF-e)"], ["ciot", "CIOT"], ["pagamento", "Pagamento"]];
  const v = est.viagem;
  const bloco = ([s, nome]) => {
    const c = est.cenarios[s], pr = est.proxima[s];
    return `<form class="bloco" data-servico="${s}" novalidate><h2>${nome}</h2>
      <p>Cenário atual: <b data-testid="cenario-atual-${s}">${esc(c.modo)}${c.codigo ? " — " + esc(c.codigo) : ""}</b>${pr ? ` · próxima chamada: <b>${esc(pr.modo)}${pr.codigo ? " — " + esc(pr.codigo) : ""}</b>` : ""}</p>
      <div class="erro-geral"></div><div class="grade">
      ${campo({ n: "modo", rotulo: "Comportamento", tipo: "select", opcoes: [["autorizar", "Autorizar / aprovar"], ["rejeitar", "Rejeitar"], ["demorar", "Demorar"], ["fora_do_ar", "Fora do ar"]].map(([x, t]) => ({ v: x, t })) }, "autorizar")}
      ${campo({ n: "codigo", rotulo: "Código de rejeição", tipo: "select", opcoes: Object.entries(est.rejeicoes).map(([k, t]) => ({ v: k, t: `${k} — ${t}` })) })}
      ${campo({ n: "atraso_s", rotulo: "Atraso (segundos)", tipo: "number", step: "0.5" })}
      ${campo({ n: "escopo", rotulo: "Aplicar a", tipo: "select", opcoes: [{ v: "todas", t: "Todas as chamadas" }, { v: "proxima", t: "Somente a próxima chamada" }] }, "todas")}</div>
      <button class="btn" type="submit" data-testid="btn-aplicar-${s}">Aplicar</button></form>`;
  };
  main.innerHTML = `<h1>Painel do simulador</h1><p>Ferramenta de teste: escolha o que cada serviço externo (fictício) responde.</p>
    <div class="barra"><button class="btn perigo" id="restaurar" data-testid="btn-restaurar">Restaurar padrão</button></div>
    ${SERV.map(bloco).join("")}
    <form class="bloco" id="form-viagem" novalidate><h2>Simulação da viagem</h2><div class="erro-geral"></div><div class="grade">
      ${campo({ n: "inicio_automatico", rotulo: "Início automático da viagem", tipo: "checkbox" }, v.inicio_automatico)}
      ${campo({ n: "espera_inicio_s", rotulo: "Espera para o início automático (s)", tipo: "number", step: "0.5" }, v.espera_inicio_s)}
      ${campo({ n: "tempo_transito_s", rotulo: "Tempo de trânsito (s)", tipo: "number", step: "0.5" }, v.tempo_transito_s)}
      ${campo({ n: "previsao_chegada_s", rotulo: "Previsão de chegada (s)", tipo: "number", step: "0.5" }, v.previsao_chegada_s)}
      ${campo({ n: "fator_aceleracao", rotulo: "Fator de aceleração", tipo: "number", step: "1" }, v.fator_aceleracao)}
      ${campo({ n: "modo_manual", rotulo: "Modo manual (Administrador avança as etapas)", tipo: "checkbox" }, v.modo_manual)}</div>
      <button class="btn" type="submit" data-testid="btn-salvar-viagem">Salvar</button></form>
    ${v.modo_manual ? `<div class="bloco"><h2>Modo manual — avançar etapa</h2><table data-testid="tabela-manual"><tbody>${viagens.filter((x) => ["Aguardando saída", "Em trânsito"].includes(x.estado) && x.ativa).map((x) => `<tr><td>${esc(x.numero)}</td><td>${selo(x.estado)}</td>
      <td><button class="btn peq" data-avancar="${x.id}" data-testid="btn-avancar-${x.id}">Avançar etapa</button></td></tr>`).join("") || `<tr><td class="vazio">Nenhuma viagem para avançar.</td></tr>`}</tbody></table></div>` : ""}
    <form class="bloco" id="form-pedido" novalidate><h2>Preparar pedido de teste</h2><div class="erro-geral"></div><div class="grade">
      ${campo({ n: "estado", rotulo: "Estado do pedido", tipo: "select", opcoes: est.estados_teste.map((x) => ({ v: x, t: x })) }, "Contratado")}
      ${campo({ n: "frete", rotulo: "Frete (R$)", tipo: "number", step: "0.01", ph: "3500" })}${campo({ n: "peso_kg", rotulo: "Peso (kg)", tipo: "number", ph: "20000" })}
      ${campo({ n: "tipo_veiculo", rotulo: "Tipo de veículo", tipo: "select", opcoes: TIPOS_VEICULO.map((x) => ({ v: x, t: x })) })}
      ${campo({ n: "idade_cte_dias", rotulo: "CT-e autorizado há (dias)", tipo: "number", step: "0.01" })}${campo({ n: "idade_mdfe_horas", rotulo: "MDF-e autorizado há (horas)", tipo: "number", step: "0.01" })}
      ${campo({ n: "divergencia", rotulo: "Divergência na entrega", tipo: "select", opcoes: ["Nenhuma", "Avaria", "Falta"].map((x) => ({ v: x, t: x })) })}${campo({ n: "desconto", rotulo: "Desconto por avaria (R$)", tipo: "number", step: "0.01" })}
      ${campo({ n: "sem_nfe", rotulo: "Sem NF-e vinculada", tipo: "checkbox" })}${campo({ n: "com_nfe", rotulo: "Rascunho já com NF-e", tipo: "checkbox" })}${campo({ n: "sem_avanco_automatico", rotulo: "Em trânsito sem avanço automático", tipo: "checkbox" })}</div>
      <button class="btn" type="submit" data-testid="btn-criar-pedido-teste">Criar pedido de teste</button> <span id="resultado-pedido" data-testid="resultado-pedido"></span></form>
    <div class="bloco"><h2>Histórico de requisições</h2><div class="barra"><button class="btn sec peq" id="atualizar" data-testid="btn-atualizar-historico">Atualizar</button><button class="btn perigo peq" id="limpar" data-testid="btn-limpar-historico">Limpar histórico</button></div>
    <table data-testid="tabela-historico"><thead><tr><th>#</th><th>Hora</th><th>Serviço</th><th>Tipo</th><th>Referência</th><th>Resultado</th></tr></thead><tbody>
    ${hist.slice().reverse().map((h) => `<tr><td>${h.id}</td><td>${fmtData(h.hora)}</td><td>${esc(h.servico)}</td><td>${esc(h.tipo)}</td><td class="mono">${esc(h.referencia)}</td><td>${esc(h.resultado)}${h.codigo ? " " + esc(h.codigo) : ""}${h.repetida ? " (repetida)" : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="vazio">Nenhuma requisição ainda.</td></tr>`}</tbody></table></div>`;

  main.querySelectorAll("form[data-servico]").forEach((f) => (f.onsubmit = async (ev) => {
    ev.preventDefault();
    const d = lerForm(f);
    try { await api("PUT", `/api/simulador/cenarios/${f.dataset.servico}`, { ...d, atraso_s: Number(d.atraso_s) || 0 }); toast("Cenário gravado."); rota(); } catch (e) { falha(e); }
  }));
  $("#form-viagem").onsubmit = async (ev) => {
    ev.preventDefault();
    const d = lerForm(ev.target);
    try { await api("PUT", "/api/simulador/viagem", d); toast("Configuração da viagem salva."); rota(); } catch (e) { if (e.campos) toast(Object.values(e.campos).join(" "), "erro"); else falha(e); }
  };
  $("#form-pedido").onsubmit = async (ev) => {
    ev.preventDefault();
    const d = lerForm(ev.target), corpo = {};
    Object.entries(d).forEach(([k, val]) => { if (val !== "" && val !== false) corpo[k] = val; });
    try {
      const r = await api("POST", "/api/simulador/pedidos-teste", corpo);
      $("#resultado-pedido").innerHTML = `Criado: <a href="#/pedidos/${r.pedido_id}" data-testid="link-pedido-teste">pedido #${r.pedido_id}</a>${r.numero_viagem ? " · " + r.numero_viagem : ""}`;
      toast(`Pedido de teste #${r.pedido_id} criado.`);
    } catch (e) { falha(e); }
  };
  $("#restaurar").onclick = async () => { if (!confirm("Restaurar o padrão? Cenários, histórico e dados de teste voltam ao início.")) return; try { await api("POST", "/api/simulador/restaurar"); toast("Padrão restaurado: cenários, histórico e dados de teste."); rota(); } catch (e) { falha(e); } };
  $("#atualizar").onclick = () => rota();
  $("#limpar").onclick = async () => { try { await api("DELETE", "/api/simulador/historico"); rota(); } catch (e) { falha(e); } };
  main.querySelectorAll("[data-avancar]").forEach((b) => (b.onclick = async () => { try { await api("POST", `/api/simulador/viagens/${b.dataset.avancar}/avancar`); toast("Etapa avançada."); rota(); } catch (e) { falha(e); } }));
}

rota();
