/* Plataforma de Protección de Datos — SPA sin dependencias (funciona sin internet). */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let user = null, entityTypes = [], files = [], entityMode = "policy", pollTimer = null;
let rev = null; // estado de la vista de revisión
const TREATMENTS = {
  redact: "Tachar", anonymize: "Anonimizar", pseudonymize: "Seudonimizar",
  mask_full: "Enmascarar total", mask_partial: "Enmascarar parcial"
};
const TREATMENT_EXAMPLES = {
  redact: "Ejemplo: ■■■", anonymize: "Ejemplo: [NOMBRE]",
  pseudonymize: "Ejemplo: Persona-01", mask_full: "Ejemplo: ********",
  mask_partial: "Ejemplo RUT: 12.3**.***-*"
};
const ANALYSIS_LEVELS = { fast: "Rápido", balanced: "Equilibrado", exhaustive: "Exhaustivo" };

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401 && !path.includes("/auth/login")) { showLogin(); throw new Error("Sesión expirada"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error?.message || msg; } catch {}
    throw new Error(msg);
  }
  return res;
}
const json = (path, body, method = "POST") =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json());
const labelOf = code => (entityTypes.find(t => t.code === code) || {}).label || code;
const STATUS = { uploaded: ["subido", "pendiente"], processing: ["procesando", "procesando"], review: ["en revisión", "revision"],
  submitted: ["enviado a aprobación", "procesando"], pending_approval: ["aprobación pendiente", "revision"],
  approved: ["aprobado", "procesando"], protecting: ["protegiendo", "procesando"], protected: ["protegido", "listo"],
  protected_with_warnings: ["generado con alertas", "revision"],
  rejected: ["rechazado", "error"], failed: ["error", "error"], verification_failed: ["verificación fallida", "error"] };
const VERIFICATION_DISCLAIMER = "La verificación automática detectó posibles datos personales o comprobaciones pendientes. Puede descargar el archivo, pero no se garantiza su anonimización completa.";
const hasWarning = d => {
  const v = typeof d.verification === "string" ? JSON.parse(d.verification) : d.verification;
  return Boolean(v && !v.ok);
};
const warningNotice = d => hasWarning(d) ? `<p class="verification-warning"><strong>Advertencia:</strong> ${VERIFICATION_DISCLAIMER}</p>` : "";
const badge = st => { const [t, c] = STATUS[st] || [st, "pendiente"]; return `<span class="status ${c}">${t}</span>`; };
const fmtDate = (s, seconds = false) => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s.replace("T", " ").slice(0, seconds ? 19 : 16);
  return new Date(d.getTime() - 3 * 3600000).toISOString().replace("T", " ").slice(0, seconds ? 19 : 16);
};
const esc = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/* ---------- Sesión ---------- */
function showLogin() { user = null; clearInterval(pollTimer); $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); }
function showShell() {
  $("#login").classList.add("hidden"); $("#shell").classList.remove("hidden");
  $("#userName").textContent = user.display_name; $("#userRole").textContent = user.role === "regular" ? "Regular" : user.role;
  $$("#nav a").forEach(a => { const roles = a.dataset.roles; a.classList.toggle("hidden", !!roles && !roles.split(",").includes(user.role)); });
  route();
}
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#loginError").textContent = "";
  const f = new FormData(e.target);
  try { user = await json("/api/auth/login", { username: f.get("username"), password: f.get("password") }); e.target.reset(); await loadCatalogs(); showShell(); }
  catch (err) { $("#loginError").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => {
  let out = {};
  try { out = await api("/api/auth/logout", { method: "POST" }).then(r => r.json()); } catch {}
  if (out.logout_url) location.href = out.logout_url; else showLogin();
});
// Keycloak (OIDC): el botón aparece solo si el servidor lo tiene configurado.
fetch("/api/auth/oidc").then(r => r.json()).then(o => {
  if (o.enabled) { $("#oidcLogin").textContent = o.label; $("#oidcLogin").classList.remove("hidden"); }
}).catch(() => {});
{
  const err = new URLSearchParams(location.search).get("login_error");
  if (err) {
    $("#loginError").textContent = err === "inactivo" ? "Tu usuario está desactivado en la plataforma."
      : "No se pudo ingresar con la cuenta institucional. Si el problema sigue, pide a TI que revise tu rol en Keycloak.";
    history.replaceState(null, "", location.pathname);
  }
}

/* ---------- Navegación ---------- */
const loaders = { procesar: loadDocs, lotes: loadBatches, aprobaciones: loadApprovals, bitacora: loadAudit, usuarios: loadUsers, politicas: loadPolicies, api: loadTokens };
function route() {
  const view = (location.hash || "#procesar").slice(1).split("/")[0];
  if (view === "revision") { return; } // se abre solo vía openReview
  clearInterval(pollTimer);
  const link = $(`#nav a[data-view="${view}"]`);
  if (!link || link.classList.contains("hidden")) { location.hash = "#procesar"; return; }
  $$("#nav a").forEach(a => a.classList.toggle("active", a === link));
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "view-" + view));
  loaders[view]?.();
}
window.addEventListener("hashchange", route);

// Volver a pulsar el ítem del menú de la sección en la que ya se está reinicia esa
// sección (equivale a "← Volver" si se estaba en la vista de revisión). Sin esto el
// hash no cambia, no hay evento hashchange y el clic no hacía nada.
$("#nav").addEventListener("click", e => {
  const a = e.target.closest("a[data-view]");
  if (!a) return;
  e.preventDefault();
  rev = null;
  const target = a.getAttribute("href");
  if (location.hash === target || (!location.hash && target === "#procesar")) route();
  else location.hash = target; // dispara hashchange -> route()
});

async function loadCatalogs() {
  entityTypes = await api("/api/v1/entity-types").then(r => r.json());
  const menu = $("#entityMenu");
  $$("label", menu).forEach(l => l.remove());
  entityTypes.forEach(t => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="checkbox" value="${t.code}" checked> ${t.label}`;
    menu.appendChild(l);
  });
  menu.onchange = () => { entityMode = "manual"; updateEntitySummary(); };
  const types = await api("/api/document-types").then(r => r.json());
  $("#docType").innerHTML = types.map(t => `<option value="${t.code}" ${t.code === "otro" ? "selected" : ""}>${t.name}</option>`).join("");
  $("#docType").onchange = () => updateEntitySummary();
  $("#manualType").innerHTML = entityTypes.map(t => `<option value="${t.code}">${t.label}</option>`).join("");
  $("#manualTypeLabel").textContent = labelOf($("#manualType").value);
  updateEntitySummary();
}
const selectedEntities = () => $$("#entityMenu input:checked").map(i => i.value);
const analysisLevelInputs = () => $$("input[name='analysisLevel']");
const selectedAnalysisLevel = () => analysisLevelInputs().find(i => i.checked)?.value || "fast";

// En modo «según política» la lista real la decide el servidor, no las casillas: se
// consulta y se refleja. Antes se mostraban las quince marcadas pasara lo que pasara,
// así que un tipo excluido por la política se veía igual que uno incluido.
const politicaCache = {};
function entidadesDePolitica(tipo) {
  politicaCache[tipo] ||= api(`/api/v1/effective-entities?document_type=${encodeURIComponent(tipo)}`)
    .then(r => r.json()).then(d => d.entities).catch(() => null);
  return politicaCache[tipo];
}
async function updateEntitySummary() {
  if (entityMode !== "policy") {
    $("#entitySummary").textContent = `Datos sensibles: ${selectedEntities().length} seleccionados`;
    refreshButtons();
    return;
  }
  $("#entitySummary").textContent = "Datos sensibles: según política";
  refreshButtons();
  const tipo = $("#docType").value;
  const codes = await entidadesDePolitica(tipo);
  // Si el usuario cambió otra vez el tipo antes de que terminara la petición,
  // esta respuesta ya no representa la política que se debe mostrar.
  if (!codes || entityMode !== "policy" || $("#docType").value !== tipo) return;
  $$("#entityMenu input").forEach(i => i.checked = codes.includes(i.value));
  $("#entitySummary").textContent = codes.length === entityTypes.length
    ? "Datos sensibles: según política (todos)"
    : `Datos sensibles: según política (${codes.length} de ${entityTypes.length})`;
  refreshButtons();
}
$("#selAll").onclick = () => { entityMode = "manual"; $$("#entityMenu input").forEach(i => i.checked = true); updateEntitySummary(); };
$("#selNone").onclick = () => { entityMode = "manual"; $$("#entityMenu input").forEach(i => i.checked = false); updateEntitySummary(); };
$("#selPolicy").onclick = () => { entityMode = "policy"; $$("#entityMenu input").forEach(i => i.checked = true); updateEntitySummary(); };
document.addEventListener("click", e => { const d = $("#entityDropdown"); if (d.open && !d.contains(e.target)) d.open = false; });

/* ---------- Carga ---------- */
const dz = $("#dropzone"), input = $("#fileInput");
$("#browse").onclick = e => { e.stopPropagation(); input.click(); };
dz.onclick = () => input.click();
dz.onkeydown = e => { if (e.key === "Enter" || e.key === " ") input.click(); };
["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", e => addFiles(e.dataTransfer.files));
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
function addFiles(list) {
  for (const f of list) if (!files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
  const tb = $("#pendingTable tbody");
  tb.innerHTML = files.map(f => `<tr><td>${f.name}</td><td>${fmtSize(f.size)}</td></tr>`).join("");
  $("#pendingTable").classList.toggle("hidden", !files.length);
  refreshButtons();
}
function refreshButtons() {
  $("#process").disabled = !files.length || (entityMode === "manual" && !selectedEntities().length);
  $("#clearFiles").disabled = !files.length;
}
$("#clearFiles").onclick = () => { files = []; addFiles([]); };
$("#process").onclick = async () => {
  $("#process").disabled = true; $("#uploadMsg").textContent = "Subiendo…";
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  fd.append("document_type", $("#docType").value);
  fd.append("analysis_level", selectedAnalysisLevel());
  fd.append("entities", entityMode === "policy" ? "" : selectedEntities().join(","));
  fd.append("batch_name", $("#batchName").value);
  try {
    const r = await api("/api/v1/documents", { method: "POST", body: fd }).then(r => r.json());
    $("#uploadMsg").textContent = `${r.documents.length} documento(s) en proceso.` +
      (r.skipped.length ? ` Omitidos: ${r.skipped.join("; ")}` : "");
    files = []; addFiles([]); $("#batchName").value = "";
    loadDocs();
  } catch (err) { $("#uploadMsg").innerHTML = `<span class="error">${err.message}</span>`; }
  refreshButtons();
};

/* ---------- Lista de documentos + polling ---------- */
async function loadDocs() {
  const rows = await api("/api/v1/documents").then(r => r.json());
  $("#docsTable tbody").innerHTML = rows.map(d => {
    let actions = "";
    if (d.status === "protected_with_warnings") actions = `<a class="btn small" href="/api/v1/documents/${d.id}/protected">Descargar con alertas</a> <button class="btn small ghost" onclick="openReview('${d.id}')">Revisar</button>`;
    else if (["review", "verification_failed"].includes(d.status)) actions = `<button class="btn small primary" onclick="openReview('${d.id}')">Revisar</button>`;
    else if (d.status === "protected") actions = `<a class="btn small" href="/api/v1/documents/${d.id}/protected">Descargar</a> <button class="btn small ghost" onclick="openReview('${d.id}')">Ver</button>`;
    else if (d.status === "pending_approval") actions = `${user.role === "administrador" ? `<a class="btn small" href="/api/v1/documents/${d.id}/protected">Descargar</a> ` : ""}<button class="btn small ghost" onclick="openReview('${d.id}')">Ver</button>`;
    else if (d.status === "rejected") actions = `${user.role === "administrador" ? `<a class="btn small" href="/api/v1/documents/${d.id}/protected">Descargar</a> ` : ""}<button class="btn small ghost" onclick="openReview('${d.id}')">Ver</button>`;
    else if (["submitted", "protecting"].includes(d.status)) actions = `<button class="btn small ghost" onclick="openReview('${d.id}')">Ver</button>`;
    else if (d.status === "failed") actions = `<small class="error">${d.error || ""}</small>`;
    return `<tr><td>${esc(d.original_name)}</td><td>${esc(d.document_type)}</td><td>${badge(d.status)}${warningNotice(d)}</td>
      <td>${d.n_findings ? `${d.n_accepted}/${d.n_findings}` : "—"}</td><td>${fmtDate(d.created_at)}</td><td>${actions}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="hint">Sin documentos</td></tr>`;
  const busy = rows.some(d => ["uploaded", "processing", "submitted", "approved", "protecting"].includes(d.status));
  clearInterval(pollTimer);
  if (busy) pollTimer = setInterval(loadDocs, 2500);
}
window.openReview = openReview;

/* ---------- Revisión ---------- */
async function openReview(docId, returnTo = "procesar") {
  clearInterval(pollTimer);
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "view-revision"));
  rev = { id: docId, page: 1, returnTo, startedCategories: new Set() };
  await refreshReview(true);
}
$("#backToDocs").onclick = () => { const target = rev?.returnTo || "procesar"; rev = null; location.hash = "#" + target; route(); };

async function refreshReview(full) {
  const d = await api(`/api/v1/documents/${rev.id}`).then(r => r.json());
  rev.doc = d;
  $("#revTitle").textContent = d.original_name;
  const [t, c] = STATUS[d.status] || [d.status, ""];
  $("#revStatus").textContent = t; $("#revStatus").className = "status " + c;
  const editable = ["review", "verification_failed", "protected_with_warnings"].includes(d.status) && user.role !== "auditor";
  $("#approveBtn").classList.toggle("hidden", !editable);
  $("#treatmentWrap").classList.toggle("hidden", !editable && !d.treatment);
  $("#treatmentSelect").disabled = !editable;
  $("#treatmentSelect").value = d.treatment || "";
  $("#treatmentExample").textContent = TREATMENT_EXAMPLES[d.treatment] || "";
  $("#approveBtn").textContent = user.role === "regular" ? "Pasar a revisión" : "Generar documento protegido";
  $("#manualWrap").classList.toggle("hidden", !editable);
  const adminDraft = user.role === "administrador" && ["pending_approval", "rejected"].includes(d.status);
  $("#downloadBtn").classList.toggle("hidden", !["protected", "protected_with_warnings"].includes(d.status) && !adminDraft);
  $("#downloadBtn").textContent = hasWarning(d) ? "Descargar con alertas" : "Descargar protegido";
  $("#downloadBtn").href = `/api/v1/documents/${rev.id}/protected`;
  const decision = user.role === "administrador" && d.status === "pending_approval";
  $("#approvalActions").classList.toggle("hidden", !decision);
  $("#approvalDownload").href = `/api/v1/documents/${rev.id}/protected`;
  const vr = $("#verifyReport");
  if (d.verification) {
    vr.classList.remove("hidden");
    vr.classList.toggle("verification-warning", !d.verification.ok);
    vr.innerHTML = `<h3>Informe del verificador — ${d.verification.ok ? "✔ correcto" : "⚠ Advertencias de verificación"}</h3>` +
      (hasWarning(d) ? `<p><strong>Advertencia:</strong> ${VERIFICATION_DISCLAIMER}</p>` : "") +
      d.verification.checks.map(ch => `<div>${ch.ok ? "✔" : "⚠"} <b>${esc(ch.name)}</b> — ${esc(ch.detail)}</div>`).join("");
  } else vr.classList.add("hidden");
  if (["approved", "submitted", "protecting", "processing"].includes(d.status)) setTimeout(() => rev && refreshReview(full), 2000);
  if (full) {
    rev.content = await api(`/api/v1/documents/${rev.id}/content`).then(r => r.json()).catch(() => null);
  }
  rev.findings = await api(`/api/v1/documents/${rev.id}/findings`).then(r => r.json());
  renderReview();
}

function renderReview() {
  const paged = rev.content?.kind === "paged";
  $("#pageWrap").classList.toggle("hidden", !paged);
  $("#textWrap").classList.toggle("hidden", paged);
  $("#pageNav").classList.toggle("hidden", !paged || rev.content.pages.length < 2);
  $("#pageNavBottom").classList.toggle("hidden", !paged || rev.content.pages.length < 2);
  $("#manualHint").textContent = paged ? "(dibuje un rectángulo en la página)" : "(seleccione texto en el documento)";
  $("#findingsCount").textContent = `(${rev.findings.filter(f => ["accepted", "edited"].includes(f.status)).length}/${rev.findings.length} aceptados)`;
  if (paged) renderPage(); else renderTextBlocks();
  renderFindingsPanel();
}

/* --- páginas (PDF / imágenes) --- */
const pageImg = $("#pageImg");
const pageUrlFor = n => `/api/v1/documents/${rev.id}/pages/${n}.png`;

function setPageState(state) {           // cargando | ok | error
  $("#pageWrap").dataset.state = state;
  $("#pageError").classList.toggle("hidden", state !== "error");
}

/* Carga de la imagen de la página con reintento automático: una respuesta cortada
   o incompleta (más probable en escaneados, que pesan bastante más) ya no deja la
   revisión sin vista previa. */
function loadPageImage(force) {
  const url = pageUrlFor(rev.page);
  const same = pageImg.dataset.pageUrl === url;
  if (same && !force) {
    if (!pageImg.complete) return;                              // sigue cargando
    if (pageImg.naturalWidth) { setPageState("ok"); return; }   // ya está cargada
  }
  pageImg.dataset.pageUrl = url;
  pageImg.dataset.tries = "0";
  setPageState("cargando");
  pageImg.src = same || force ? `${url}?r=${Date.now()}` : url;
}
pageImg.addEventListener("load", () => { if (pageImg.naturalWidth) setPageState("ok"); });
pageImg.addEventListener("error", () => {
  const url = pageImg.dataset.pageUrl;
  if (!url) return;
  const tries = Number(pageImg.dataset.tries || 0);
  if (tries < 2) {
    pageImg.dataset.tries = String(tries + 1);
    setTimeout(() => { if (pageImg.dataset.pageUrl === url) pageImg.src = `${url}?r=${Date.now()}`; }, 500 * (tries + 1));
    return;
  }
  setPageState("error");
});
$("#pageRetry").onclick = () => loadPageImage(true);

function renderPage() {
  const p = rev.content.pages[rev.page - 1];
  $("#pageLabel").textContent = `Página ${rev.page} / ${rev.content.pages.length}`;
  $("#pageLabelBottom").textContent = `Página ${rev.page} / ${rev.content.pages.length}`;
  [$("#prevPage"), $("#prevPageBottom")].forEach(b => b.disabled = rev.page <= 1);
  [$("#nextPage"), $("#nextPageBottom")].forEach(b => b.disabled = rev.page >= rev.content.pages.length);
  // La caja de la página conserva la proporción real del documento y cada recuadro
  // se posiciona en porcentaje: quedan bien situados aunque la imagen todavía no
  // haya cargado y no se desplazan al cambiar el tamaño de la ventana.
  pageImg.style.aspectRatio = `${p.width} / ${p.height}`;
  loadPageImage(false);
  const ov = $("#overlay");
  ov.innerHTML = "";
  rev.findings.filter(f => f.page === rev.page && f.boxes).forEach(f => {
    (f.boxes || []).forEach(b => {
      const el = document.createElement("div");
      el.className = "fbox " + (["accepted", "edited"].includes(f.status) ? "acc" : "rej");
      el.style.cssText = `left:${b[0] / p.width * 100}%;top:${b[1] / p.height * 100}%;` +
        `width:${(b[2] - b[0]) / p.width * 100}%;height:${(b[3] - b[1]) / p.height * 100}%`;
      el.title = `${labelOf(f.entity_code)}: ${f.text} (clic para ${f.status === "accepted" ? "rechazar" : "aceptar"})`;
      el.onclick = ev => { ev.stopPropagation(); toggleFinding(f); };
      ov.appendChild(el);
    });
  });
}
$("#prevPage").onclick = () => { if (rev.page > 1) { rev.page--; renderPage(); } };
$("#nextPage").onclick = () => { if (rev.page < rev.content.pages.length) { rev.page++; renderPage(); } };
$("#prevPageBottom").onclick = $("#prevPage").onclick;
$("#nextPageBottom").onclick = $("#nextPage").onclick;

// dibujo de hallazgo manual (rectángulo)
(() => {
  const ov = $("#overlay");
  let start = null, rubber = null;
  ov.addEventListener("mousedown", e => {
    if (e.target !== ov || !["review", "verification_failed", "protected_with_warnings"].includes(rev?.doc?.status)) return;
    const r = ov.getBoundingClientRect();
    start = [e.clientX - r.left, e.clientY - r.top];
    rubber = document.createElement("div"); rubber.className = "rubber"; ov.appendChild(rubber);
    e.preventDefault();
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    const r = ov.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    rubber.style.cssText = `left:${Math.min(start[0], x)}px;top:${Math.min(start[1], y)}px;width:${Math.abs(x - start[0])}px;height:${Math.abs(y - start[1])}px`;
  });
  window.addEventListener("mouseup", async e => {
    if (!start) return;
    const r = ov.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const [x0, y0, x1, y1] = [Math.min(start[0], x), Math.min(start[1], y), Math.max(start[0], x), Math.max(start[1], y)];
    start = null; rubber.remove();
    if (x1 - x0 < 8 || y1 - y0 < 8) return;
    const p = rev.content.pages[rev.page - 1];
    const img = $("#pageImg");
    if (!img.clientWidth || !img.clientHeight) return;
    const scaleX = p.width / img.clientWidth;
    const scaleY = p.height / img.clientHeight;
    try {
      await json(`/api/v1/documents/${rev.id}/findings`, {
        entity_code: $("#manualType").value, page: rev.page,
        box: [Math.round(x0 * scaleX), Math.round(y0 * scaleY),
          Math.round(x1 * scaleX), Math.round(y1 * scaleY)],
      });
      await refreshReview(false);
    } catch (err) { alert(err.message); }
  });
})();

/* --- texto (DOCX / XLSX / TXT) --- */
function renderTextBlocks() {
  const wrap = $("#textWrap");
  wrap.innerHTML = "";
  const byLoc = {};
  rev.findings.filter(f => f.location != null).forEach(f => (byLoc[f.location] ||= []).push(f));
  rev.content.blocks.forEach(b => {
    const div = document.createElement("div");
    div.className = "tblock"; div.dataset.location = b.location;
    const fs = (byLoc[b.location] || []).sort((a, x) => a.start_off - x.start_off);
    let pos = 0, html = "";
    const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    fs.forEach(f => {
      if (f.start_off < pos) return; // solapado
      html += esc(b.text.slice(pos, f.start_off));
      html += `<mark class="${["accepted", "edited"].includes(f.status) ? "acc" : "rej"}" data-fid="${f.id}" title="${labelOf(f.entity_code)}">${esc(b.text.slice(f.start_off, f.end_off))}</mark>`;
      pos = f.end_off;
    });
    html += esc(b.text.slice(pos));
    if (rev.content.format === "xlsx") html = `<span class="loc">${b.location}</span> ` + html;
    div.innerHTML = html;
    wrap.appendChild(div);
  });
  wrap.onclick = e => {
    const m = e.target.closest("mark");
    if (!m) return;
    const f = rev.findings.find(f => f.id === m.dataset.fid);
    if (f) toggleFinding(f);
  };
  wrap.onmouseup = async () => {
    if (!["review", "verification_failed", "protected_with_warnings"].includes(rev?.doc?.status)) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    const range = sel.getRangeAt(0);
    const block = range.startContainer.parentElement.closest(".tblock");
    if (!block || !block.contains(range.endContainer)) return;
    // offset dentro del texto plano del bloque
    const pre = range.cloneRange();
    pre.selectNodeContents(block);
    pre.setEnd(range.startContainer, range.startOffset);
    let startOff = pre.toString().length;
    let text = range.toString();
    const locPrefix = block.querySelector(".loc")?.textContent;
    if (locPrefix) startOff -= locPrefix.length + 1;
    if (!text.trim() || startOff < 0) return;
    sel.removeAllRanges();
    try {
      await json(`/api/v1/documents/${rev.id}/findings`, {
        entity_code: $("#manualType").value, location: block.dataset.location,
        start: startOff, end: startOff + text.length, text,
      });
      await refreshReview(false);
    } catch (err) { alert(err.message); }
  };
}

/* --- panel de hallazgos --- */
function renderFindingsPanel() {
  const groups = {};
  rev.findings.forEach(f => (groups[f.entity_code] ||= []).push(f));
  (rev.startedCategories || new Set()).forEach(code => (groups[code] ||= []));
  const editable = ["review", "verification_failed", "protected_with_warnings"].includes(rev.doc.status) && user.role !== "auditor";
  const orderedCodes = entityTypes.map(t => t.code).filter(code => groups[code]);
  const missing = entityTypes.filter(t => !groups[t.code]);
  // Un tipo que no se buscó y uno que se buscó sin encontrar nada se ven igual en el
  // panel: sin aviso, la ausencia de un correo parece un fallo del motor y no lo que es,
  // una selección o una política que lo dejó fuera.
  const buscados = rev.doc.entities || entityTypes.map(t => t.code);
  const noBuscados = entityTypes.filter(t => !buscados.includes(t.code));
  const aviso = noBuscados.length ? `<div class="no-buscado" title="No formaban parte de la selección al subir el documento (o la política del tipo documental los excluye), así que el motor no los buscó.">
      <b>No se buscaron:</b> ${noBuscados.map(t => esc(t.label)).join(", ")}</div>` : "";
  // Cada hallazgo lleva su propio botón a la derecha. Los manuales se eliminan; los
  // detectados por el motor no se pueden borrar (el backend los conserva para la
  // trazabilidad), así que el botón los rechaza —o los vuelve a incluir si ya estaban
  // rechazados—, que es el equivalente a quitarlos del tachado.
  const botonHallazgo = f => {
    if (!editable) return "";
    if (f.source === "manual")
      return `<button class="finding-remove" data-remove="${f.id}" title="Eliminar hallazgo manual" aria-label="Eliminar hallazgo manual">×</button>`;
    return ["accepted", "edited"].includes(f.status)
      ? `<button class="finding-remove" data-reject="${f.id}" title="Rechazar: no tachar este hallazgo" aria-label="Rechazar hallazgo">×</button>`
      : `<button class="finding-remove restore" data-restore="${f.id}" title="Volver a incluir: tachar este hallazgo" aria-label="Volver a incluir hallazgo">↺</button>`;
  };
  const cuerpo = orderedCodes.map(code => {
    const fs = groups[code];
    return `
    <div class="fgroup ${$("#manualType").value === code ? "manual-selected" : ""}">
      <div class="fgroup-head"><b>${labelOf(code)}</b> <span>${fs.filter(f => ["accepted", "edited"].includes(f.status)).length}/${fs.length}</span>
        ${editable && fs.length ? `<button class="link" data-bulk="accepted" data-code="${code}">✓ todos</button>
        <button class="link" data-bulk="rejected" data-code="${code}">✗ todos</button>` : ""}</div>
      ${fs.map(f => `<div class="fitem ${["accepted", "edited"].includes(f.status) ? "acc" : "rej"}" data-fid="${f.id}">
        <span class="ftext">${esc((f.text || "").slice(0, 48))}</span>
        <span class="fmeta"><small>${f.source === "manual" ? "manual" : "p." + (f.page ?? "—") + " · " + Math.round((f.score || 0) * 100) + "%"}</small>
        ${botonHallazgo(f)}</span></div>`).join("")}
      ${editable ? `<button class="add-finding" data-add="${code}">Agregar +</button>` : ""}
    </div>`;
  }).join("") + (editable && missing.length ? `
    <label class="start-category">Empezar categoría
      <select id="startCategory"><option value="">Seleccione…</option>${missing.map(t => `<option value="${t.code}">${t.label}</option>`).join("")}</select>
    </label>` : "") || `<p class="hint">Sin hallazgos.</p>`;
  $("#findingsGroups").innerHTML = aviso + cuerpo;
  $$("#findingsGroups .fitem").forEach(el => el.onclick = () => {
    const f = rev.findings.find(f => f.id === el.dataset.fid);
    if (f?.page && rev.content.kind === "paged" && f.page !== rev.page) { rev.page = f.page; renderPage(); }
    if (f) toggleFinding(f);
  });
  $$("#findingsGroups [data-bulk]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    try { await json(`/api/v1/documents/${rev.id}/findings/bulk`, { status: b.dataset.bulk, entity_code: b.dataset.code }); await refreshReview(false); }
    catch (err) { alert(err.message); }
  });
  $$("#findingsGroups [data-add]").forEach(b => b.onclick = e => {
    e.stopPropagation();
    setManualType(b.dataset.add);
  });
  $$("#findingsGroups [data-reject]").forEach(b => b.onclick = e => {
    e.stopPropagation();
    setFindingStatus(b.dataset.reject, "rejected");
  });
  $$("#findingsGroups [data-restore]").forEach(b => b.onclick = e => {
    e.stopPropagation();
    setFindingStatus(b.dataset.restore, "accepted");
  });
  $$("#findingsGroups [data-remove]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    try {
      await api(`/api/v1/documents/${rev.id}/findings/${b.dataset.remove}`, { method: "DELETE" });
      await refreshReview(false);
    } catch (err) { alert(err.message); }
  });
  const start = $("#startCategory");
  if (start) start.onchange = () => {
    if (!start.value) return;
    rev.startedCategories.add(start.value);
    setManualType(start.value);
  };
}

function setManualType(code) {
  $("#manualType").value = code;
  $("#manualTypeLabel").textContent = labelOf(code);
  $("#manualWrap").classList.add("manual-flash");
  setTimeout(() => $("#manualWrap").classList.remove("manual-flash"), 500);
  renderFindingsPanel();
}

async function setFindingStatus(fid, status) {
  const f = rev.findings.find(x => x.id === fid);
  if (!f || !["review", "verification_failed", "protected_with_warnings"].includes(rev.doc.status) || user.role === "auditor") return;
  try {
    await json(`/api/v1/documents/${rev.id}/findings/${f.id}`, { status }, "PATCH");
    await refreshReview(false);
  } catch (err) { alert(err.message); }
}

async function toggleFinding(f) {
  if (!["review", "verification_failed", "protected_with_warnings"].includes(rev.doc.status) || user.role === "auditor") return;
  const status = ["accepted", "edited"].includes(f.status) ? "rejected" : "accepted";
  try {
    await json(`/api/v1/documents/${rev.id}/findings/${f.id}`, { status }, "PATCH");
    await refreshReview(false);
  } catch (err) { alert(err.message); }
}

$("#approveBtn").onclick = async () => {
  const treatment = $("#treatmentSelect").value;
  if (!treatment) { alert("Seleccione el tipo de tratamiento antes de continuar."); return; }
  $("#approveBtn").disabled = true;
  const endpoint = user.role === "regular" ? "submit-review" : "approve";
  try { await json(`/api/v1/documents/${rev.id}/${endpoint}`, { treatment }); await refreshReview(false); }
  catch (err) { alert(err.message); }
  $("#approveBtn").disabled = false;
};
$("#treatmentSelect").onchange = event => {
  $("#treatmentExample").textContent = TREATMENT_EXAMPLES[event.target.value] || "";
};

/* ---------- Aprobaciones administrativas ---------- */
async function loadApprovals() {
  const denied = user.role !== "administrador";
  $("#approvalsDenied").classList.toggle("hidden", !denied);
  $("#approvalsContent").classList.toggle("hidden", denied);
  if (denied) return;
  const rows = await api("/api/v1/pending-approvals").then(r => r.json());
  $("#approvalsBody").innerHTML = rows.map(d => {
    const ready = d.status === "pending_approval";
    return `<tr><td><b>${esc(d.original_name)}</b></td><td>${esc(d.uploaded_by)}</td><td>${esc(d.document_type)}</td>
      <td>${fmtDate(d.submitted_at)}</td><td>${d.n_accepted}/${d.n_findings}</td><td>${badge(d.status)}${warningNotice(d)}</td>
      <td class="approval-row-actions"><button class="btn small ghost" data-review="${d.id}">Revisar</button>
        ${ready ? `<a class="btn small ghost" href="/api/v1/documents/${d.id}/protected">Descargar</a>
        <button class="btn small" data-decision="return" data-id="${d.id}">Devolver</button>
        <button class="btn small danger" data-decision="reject" data-id="${d.id}">Rechazar</button>
        <button class="btn small danger" data-decision="delete" data-id="${d.id}">Eliminar</button>
        <button class="btn small primary" data-decision="approve" data-id="${d.id}">Aprobar</button>` : `<small class="hint">Preparando borrador…</small>`}
      </td></tr>`;
  }).join("") || `<tr><td colspan="7" class="permission-empty compact">No hay aprobaciones pendientes</td></tr>`;
  $$("#approvalsBody [data-review]").forEach(b => b.onclick = () => openReview(b.dataset.review, "aprobaciones"));
  $$("#approvalsBody [data-decision]").forEach(b => b.onclick = () => approvalDecision(b.dataset.id, b.dataset.decision));
  clearInterval(pollTimer);
  if (rows.some(d => ["submitted", "protecting"].includes(d.status))) pollTimer = setInterval(loadApprovals, 2500);
}

async function approvalDecision(docId, action, fromReview = false) {
  let note = "";
  if (action === "return") {
    const answer = prompt("Motivo o indicación para devolver a revisión (opcional):");
    if (answer === null) return; note = answer;
  }
  if (action === "reject") {
    const answer = prompt("Motivo del rechazo (opcional):");
    if (answer === null) return; note = answer;
  }
  if (action === "delete" && !confirm("¿Eliminar definitivamente este documento y sus hallazgos?")) return;
  try {
    if (action === "delete") await api(`/api/v1/pending-approvals/${docId}`, { method: "DELETE" });
    else await json(`/api/v1/pending-approvals/${docId}/${action}`, { note });
    if (fromReview && ["return", "delete"].includes(action)) {
      rev = null; location.hash = "#aprobaciones"; route();
    } else if (fromReview) {
      await refreshReview(false);
    } else {
      await loadApprovals();
    }
  } catch (err) { alert(err.message); }
}

$("#approvalApprove").onclick = () => approvalDecision(rev.id, "approve", true);
$("#approvalReturn").onclick = () => approvalDecision(rev.id, "return", true);
$("#approvalReject").onclick = () => approvalDecision(rev.id, "reject", true);
$("#approvalDelete").onclick = () => approvalDecision(rev.id, "delete", true);

/* ---------- Lotes ---------- */
async function loadBatches() {
  const rows = await api("/api/v1/batches").then(r => r.json());
  $("#batchesBody").innerHTML = rows.map(b => {
    const warnings = b.counts.protected_with_warnings || 0;
    const done = (b.counts.protected || 0) + warnings, failed = (b.counts.failed || 0) + (b.counts.verification_failed || 0);
    return `<tr><td><b>${b.name}</b></td><td>${b.document_type || ""}</td><td>${b.created_by}</td>
      <td>${done}/${b.total} generados${warnings ? ` · ${warnings} con alertas<p class="verification-warning">${VERIFICATION_DISCLAIMER}</p>` : ""}${failed ? ` · <span class="error">${failed} con error</span>` : ""}
          ${b.counts.review ? ` · ${b.counts.review} en revisión` : ""}</td>
      <td>${fmtDate(b.created_at)}</td>
      <td>${done ? `<a class="btn small" href="/api/v1/batches/${b.id}/protected.zip">ZIP</a>` : ""}
          <a class="btn small ghost" href="/api/v1/batches/${b.id}/report.csv">CSV</a></td></tr>`;
  }).join("") || `<tr><td colspan="6" class="hint">Sin lotes</td></tr>`;
}

/* ---------- Bitácora ---------- */
async function loadAudit() {
  const q = new URLSearchParams({ username: $("#auditUser").value, event: $("#auditEvent").value });
  const rows = await api("/api/audit?" + q).then(r => r.json());
  $("#auditBody").innerHTML = rows.map(r => {
    let detail = {}; try { detail = JSON.parse(r.details); } catch {}
    const treatment = detail.treatment || detail.stats?.treatment || "";
    const analysisLevel = r.analysis_level || detail.analysis_level || detail.stats?.analysis_level || "";
    return `<tr><td>${fmtDate(r.ts, true)}</td><td>${esc(r.username)}</td><td>${esc(r.ip || "")}</td><td><code>${esc(r.event)}</code></td><td>${esc(TREATMENTS[treatment] || treatment || "—")}</td><td>${esc(ANALYSIS_LEVELS[analysisLevel] || analysisLevel || "—")}</td><td class="detail">${esc(r.details)}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="hint">Sin registros</td></tr>`;
}
$("#auditSearch").onclick = loadAudit;
$("#auditVerify").onclick = async () => { const v = await api("/api/audit/verify").then(r => r.json()); $("#auditVerifyResult").textContent = v.ok ? "✔ Cadena de hashes íntegra: la bitácora no ha sido alterada." : `✖ Cadena rota en el registro #${v.broken_at}`; };

/* ---------- Usuarios ---------- */
async function loadUsers() {
  const rows = await api("/api/users").then(r => r.json());
  $("#usersBody").innerHTML = rows.map(u => `<tr>
    <td><b>${u.username}</b></td><td>${u.display_name}</td><td>${u.auth_source || "local"}</td>
    <td><select data-id="${u.id}" class="roleSel">${["regular","operador","revisor","aprobador","auditor","administrador"].map(r => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r === "regular" ? "Regular" : r}</option>`).join("")}</select></td>
    <td><span class="status ${u.active ? "listo" : "error"}">${u.active ? "activo" : "inactivo"}</span></td>
    <td>${fmtDate(u.last_login_at)}</td>
    <td><button class="btn small toggle" data-id="${u.id}" data-active="${u.active ? 0 : 1}">${u.active ? "Desactivar" : "Activar"}</button>
        ${(u.auth_source || "local") === "local" ? `<button class="btn small pw" data-id="${u.id}">Nueva contraseña</button>` : ""}</td></tr>`).join("");
  $$(".roleSel").forEach(s => s.onchange = () => patchUser(s.dataset.id, { role: s.value }));
  $$(".toggle").forEach(b => b.onclick = () => patchUser(b.dataset.id, { active: b.dataset.active === "1" }));
  $$(".pw").forEach(b => b.onclick = () => { const p = prompt("Nueva contraseña (≥10 caracteres, una mayúscula y un número):"); if (p) patchUser(b.dataset.id, { password: p }); });
}
async function patchUser(id, body) {
  $("#userError").textContent = "";
  try { await json("/api/users/" + id, body, "PATCH"); } catch (e) { $("#userError").textContent = e.message; }
  loadUsers();
}
$("#userForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#userError").textContent = "";
  const f = Object.fromEntries(new FormData(e.target));
  try { await json("/api/users", f); e.target.reset(); loadUsers(); } catch (err) { $("#userError").textContent = err.message; }
});

/* ---------- Políticas ---------- */
async function loadPolicies() {
  const p = await api("/api/policies").then(r => r.json());
  const typeName = t => t === "*" ? "＊ (por defecto)" : t;
  $("#policyTable thead").innerHTML = `<tr><th>Tipo documental</th>${p.entities.map(e => `<th class="rot"><span>${e.label}</span></th>`).join("")}</tr>`;
  $("#policyTable tbody").innerHTML = p.types.map(t => `<tr><td><b>${typeName(t)}</b></td>${p.entities.map(e =>
    `<td class="center"><input type="checkbox" data-type="${t}" data-code="${e.code}" ${p.matrix[t][e.code] ? "checked" : ""}></td>`).join("")}</tr>`).join("");
}
$("#savePolicies").onclick = async () => {
  const matrix = {};
  $$("#policyTable input[type=checkbox]").forEach(i => (matrix[i.dataset.type] ||= {})[i.dataset.code] = i.checked);
  try { await json("/api/policies", { matrix }, "PUT"); $("#policyMsg").textContent = "Políticas guardadas."; }
  catch (err) { $("#policyMsg").innerHTML = `<span class="error">${err.message}</span>`; }
};
$("#typeForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  try { await json("/api/document-types", f); e.target.reset(); await loadCatalogs(); loadPolicies(); }
  catch (err) { $("#policyMsg").innerHTML = `<span class="error">${err.message}</span>`; }
});

/* ---------- Tokens ---------- */
async function loadTokens() {
  const rows = await api("/api/tokens").then(r => r.json());
  $("#tokensBody").innerHTML = rows.map(t => `<tr><td><b>${t.name}</b></td><td>${t.created_by}</td><td>${t.created_at.slice(0, 10)}</td><td>${fmtDate(t.last_used_at)}</td>
    <td><span class="status ${t.active ? "listo" : "error"}">${t.active ? "activo" : "revocado"}</span></td>
    <td>${t.active ? `<button class="btn small danger revoke" data-id="${t.id}">Revocar</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="hint">Sin tokens</td></tr>`;
  $$(".revoke").forEach(b => b.onclick = async () => { await api("/api/tokens/" + b.dataset.id, { method: "DELETE" }); loadTokens(); });
}
$("#tokenForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  try { const t = await json("/api/tokens", f); $("#tokenNew").innerHTML = `Token creado (cópielo ahora, no se volverá a mostrar): <code>${t.token}</code>`; e.target.reset(); loadTokens(); }
  catch (err) { $("#tokenNew").innerHTML = `<span class="error">${err.message}</span>`; }
});

/* ---------- Mi cuenta ---------- */
const pwForm = $("#pwForm");
const EYE_SHOW = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7.5 11-7.5S23 12 23 12s-4 7.5-11 7.5S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_HIDE = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19.5C5 19.5 1 12 1 12a18.6 18.6 0 0 1 5.1-5.9M9.9 4.7A10.5 10.5 0 0 1 12 4.5c7 0 11 7.5 11 7.5a18.7 18.7 0 0 1-2.2 3.2m-6.7-1.1a3 3 0 1 1-4.2-4.2"/><line x1="2" y1="2" x2="22" y2="22"/></svg>`;

// Botón "ojo" en cada casilla de contraseña.
$$(".pw-field", pwForm).forEach(wrap => {
  const input = $("input", wrap);
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "pw-eye"; btn.innerHTML = EYE_SHOW;
  btn.title = "Mostrar contraseña"; btn.setAttribute("aria-label", "Mostrar contraseña");
  btn.addEventListener("click", () => {
    const ver = input.type === "password";
    input.type = ver ? "text" : "password";
    btn.innerHTML = ver ? EYE_HIDE : EYE_SHOW;
    const t = ver ? "Ocultar contraseña" : "Mostrar contraseña";
    btn.title = t; btn.setAttribute("aria-label", t);
    input.focus();
  });
  wrap.appendChild(btn);
});

// Reglas de la nueva contraseña (las mismas que valida el servidor).
const PW_RULES = {
  len: v => v.length >= 10,
  upper: v => /[A-ZÁÉÍÓÚÜÑ]/.test(v),
  digit: v => /[0-9]/.test(v),
};
const pwEls = pwForm.elements;
const fieldError = name => $(`.field-error[data-error-for="${name}"]`, pwForm);

function pwRenderRules() {
  const v = pwEls.password.value;
  $$("#pwRules li").forEach(li => {
    const ok = PW_RULES[li.dataset.rule](v);
    li.classList.toggle("ok", v !== "" && ok);
    li.classList.toggle("bad", v !== "" && !ok);
  });
}
function pwRenderMatch(name, origen) {
  const el = fieldError(name);
  if (!el) return;
  const conf = pwEls[name].value, base = pwEls[origen].value;
  el.textContent = (conf && base !== conf) ? "Las contraseñas deben coincidir" : "";
}
function pwRenderAll() {
  pwRenderRules();
  pwRenderMatch("current_confirm", "current");
  pwRenderMatch("password_confirm", "password");
}
["current", "current_confirm", "password", "password_confirm"].forEach(n =>
  pwEls[n].addEventListener("input", () => { $("#pwMsg").textContent = ""; pwRenderAll(); }));

function pwReset() {
  pwForm.reset();
  $$(".pw-field input", pwForm).forEach(i => i.type = "password");
  $$(".pw-eye", pwForm).forEach(b => { b.innerHTML = EYE_SHOW; b.title = "Mostrar contraseña"; b.setAttribute("aria-label", "Mostrar contraseña"); });
  $$("#pwRules li").forEach(li => li.classList.remove("ok", "bad"));
  $$(".field-error", pwForm).forEach(el => el.textContent = "");
}

pwForm.addEventListener("submit", async e => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const fail = (msg, campo) => { $("#pwMsg").innerHTML = `<span class="error">${msg}</span>`; if (campo) pwEls[campo].focus(); };
  $("#pwMsg").textContent = "";
  pwRenderAll();
  // Doble digitación: ambas contraseñas actuales y ambas nuevas deben coincidir.
  if (f.current !== f.current_confirm) return fail("Las dos contraseñas actuales no coinciden.", "current_confirm");
  if (!Object.values(PW_RULES).every(r => r(f.password)))
    return fail("La nueva contraseña debe tener al menos 10 caracteres, una mayúscula y un número.", "password");
  if (f.password !== f.password_confirm) return fail("Las dos contraseñas nuevas no coinciden.", "password_confirm");
  if (f.password === f.current) return fail("La nueva contraseña debe ser distinta de la actual.", "password");
  try { await json("/api/me/password", f); $("#pwMsg").textContent = "Contraseña actualizada."; pwReset(); }
  catch (err) { fail(err.message); }
});

/* ---------- Arranque ---------- */
(async () => {
  try { user = await api("/api/me").then(r => r.json()); await loadCatalogs(); showShell(); } catch { showLogin(); }
})();
