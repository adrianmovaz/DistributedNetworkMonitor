"use strict";
const $ = (id) => document.getElementById(id);
const titles = {
  overview: ["Vista general", "El estado de tu red y sus últimas novedades, en un solo lugar."],
  devices: ["Dispositivos", "Registra tu infraestructura y consulta su última disponibilidad."],
  monitoring: ["Monitoreo", "Sigue las tareas y revisa los resultados de cada consulta."],
  incidents: ["Incidentes", "Consulta fallas, anomalías y recuperaciones de la infraestructura."],
  reports: ["Reportes", "Consulta los documentos generados y verifica su entrega por FTP."],
  notifications: ["Notificaciones", "Revisa la entrega de correos y reintenta los envíos fallidos."],
  dns: ["Consultas DNS", "Resuelve nombres y consulta registros de tu infraestructura."],
};
const labels = {up:"Disponible",down:"Sin respuesta",abnormal:"Anomalía",unknown:"Sin monitoreo",error:"Error de consulta",queued:"En cola",running:"En curso",completed:"Completada",cancelled:"Cancelada",failed:"Fallida",pending:"Pendiente",generating:"Generando",ready:"Generado",uploading:"Subiendo",uploaded:"Verificado",sending:"Enviando",sent:"Aceptado por SMTP",recovered:"Recuperación",monitoring_error:"Error de monitoreo",report_generated:"Reporte generado"};
const dateFormat = new Intl.DateTimeFormat("es-MX",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"});
const numberFormat = new Intl.NumberFormat("es-MX");
const state = {view:"overview",jobs:[],dns:[],pages:{},data:{},refreshing:false,again:false};
for (const view of ["devices","results","incidents","reports","notifications"]) state.pages[view]={offset:0,more:false};
function node(tag, text, cls) { const item=document.createElement(tag); if(text!==undefined && text!==null)item.textContent=String(text); if(cls)item.className=cls; return item; }
function stamp(value) { return value ? dateFormat.format(new Date(value)) : "Sin consultas"; }
function badge(value, custom) { return node("span",custom||labels[value]||value,"badge "+({up:"good",uploaded:"good",sent:"good",recovered:"good",completed:"good",down:"bad",failed:"bad",error:"bad",abnormal:"warn",pending:"warn",queued:"info",running:"info",generating:"info",uploading:"info",sending:"info"}[value]||"")); }
function empty(title,description) { const box=node("div",null,"empty-state"); box.append(node("strong",title),node("span",description)); return box; }
function action(text,type,id) { const button=node("button",text,"action-button"); button.type="button"; button.dataset.action=type; button.dataset.id=id; return button; }
function actions(...items) { const box=node("div",null,"row-actions"); box.append(...items.filter(Boolean)); return box; }
function nameCell(name,sub) { const box=node("div",name,"name-cell"); if(sub)box.append(node("small",sub,"mono")); return box; }
function table(target,headers,rows,title,description) {
  const box=$(target); box.replaceChildren();
  if(!rows.length){box.append(empty(title,description));return;}
  const grid=node("table"),thead=node("thead"),head=node("tr"),body=node("tbody");
  for(const label of headers){const th=node("th",label);th.scope="col";head.append(th);} thead.append(head);
  for(const cells of rows){const tr=node("tr");for(const content of cells){const td=node("td");td.append(content instanceof Node?content:document.createTextNode(String(content??"—")));tr.append(td);}body.append(tr);}
  grid.append(thead,body);box.append(grid);
}
let toastTimer;
function toast(message) { $("toast").textContent=message;$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$("toast").hidden=true;},6000); }
async function api(path,body) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
  try { const response=await fetch(path,{signal:controller.signal,cache:"no-store",headers:body===undefined?{}:{"Content-Type":"application/json"},method:body===undefined?"GET":"POST",body:body===undefined?undefined:JSON.stringify(body)});
    let data;try{data=await response.json();}catch{throw new Error("El servidor devolvió una respuesta inesperada.");}
    if(!response.ok)throw new Error(typeof data.error==="string"?data.error:data.error?.message||"No se pudo completar la solicitud.");
    return {data,more:response.headers.get("X-Has-More")==="true"};
  } catch(error) { if(error.name==="AbortError")throw new Error("El servidor tardó demasiado en responder. Intenta actualizar.");throw error; } finally {clearTimeout(timer);}
}
function renderStatistics(stats) {
  $("metric-devices").textContent=numberFormat.format(stats.devices.total);
  $("metric-available").textContent=`${stats.devices.up||0} disponibles · ${stats.devices.unknown||0} sin monitoreo`;
  $("metric-monitoring").textContent=numberFormat.format(stats.monitoring.total);
  $("metric-incidents").textContent=numberFormat.format(stats.incidents.total);
  $("metric-outages").textContent=`${stats.incidents.down||0} sin respuesta · ${stats.incidents.recovered||0} recuperaciones`;
  $("metric-reports").textContent=numberFormat.format(stats.reports.uploaded||0);
  $("metric-delivery").textContent=`${stats.reports.total} generados o en proceso`;
  const states=[["up","#269d78"],["down","#dc7783"],["abnormal","#dba24c"],["unknown","#cdd7e5"]];let position=0;const segments=[];
  $("health-legend").replaceChildren();
  for(const [key,color] of states){const count=stats.devices[key]||0;const end=position+(stats.devices.total?count/stats.devices.total*100:0);segments.push(`${color} ${position}% ${end}%`);position=end;const row=node("div");const dot=node("span",null,"swatch");dot.style.background=color;row.append(dot,node("span",labels[key]),node("b",count));$("health-legend").append(row);}
  $("health-ring").style.background=stats.devices.total?`conic-gradient(${segments.join(",")})`:"#e8edf4";
  $("health-count").textContent=stats.devices.up||0;
  const chart=$("activity-chart");chart.replaceChildren();const maximum=Math.max(1,...stats.activity.map(item=>item.total));let total=0;
  for(const item of stats.activity){total+=item.total;const column=node("div",null,"chart-column");const good=item.up+item.abnormal,bad=item.down+item.error;column.title=`${item.at}: ${good} con respuesta, ${bad} sin respuesta/error`;
    if(!item.total)column.append(node("span",null,"bar empty"));
    else {if(bad){const bar=node("span",null,"bar bad");bar.style.height=`${bad/maximum*95}%`;column.append(bar);}if(good){const bar=node("span",null,"bar");bar.style.height=`${good/maximum*95}%`;column.append(bar);}}
    chart.append(column);
  }
  chart.setAttribute("aria-label",`${total} consultas en las últimas 24 horas. ${stats.activity.reduce((sum,item)=>sum+item.down+item.error,0)} sin respuesta o con error.`);
  $("activity-summary").textContent=total?`${total} consultas en este período. Última observación: ${stamp(stats.last_observed_at)}.`:"Aún no hay consultas en este período. Solicita un monitoreo desde Dispositivos.";
  $("chart-labels").replaceChildren(...[0,6,12,18,23].map(index=>node("span",`${new Date(stats.activity[index].at).getUTCHours().toString().padStart(2,"0")}:00`)));
}
function renderIncidents(rows,target="incidents-table") {
  table(target,["Dispositivo","Evento","Fecha local","Descripción","Reporte"],rows.map(item=>[
    nameCell(item.device_name,item.device_host),badge(item.event_type),stamp(item.observed_at),node("span",item.description,"description-cell"),
    item.report_id?action("Ver reporte","report",item.report_id):action("Generar reporte","queue-report",item.id),
  ]),"Sin incidentes registrados","Las fallas, anomalías y recuperaciones aparecerán aquí.");
}
function renderDevices(rows) {
  const active=new Set(state.jobs.filter(job=>["queued","running"].includes(job.status)).map(job=>job.device_id));
  table("devices-table",["Dispositivo","Dirección","Puerto SNMP","Estado","Última consulta","Acciones"],rows.map(item=>{
    const button=action(active.has(item.id)?"En curso…":"Monitorear","monitor",item.id);button.disabled=active.has(item.id);
    return [nameCell(item.name),node("span",item.host,"mono"),item.snmp_port,badge(item.status),stamp(item.last_observed_at),actions(button,action("Detalle","device",item.id))];
  }),"Todavía no hay dispositivos","Registra un dispositivo o cambia los filtros para empezar.");
}
function renderJobs() {
  table("jobs-table",["Dispositivo","Estado de tarea","Resultado","Solicitada","Acciones"],state.jobs.map(job=>[
    node("span",job.device_name||state.deviceNames?.[job.device_id]||job.device_id.slice(0,8),"mono"),badge(job.status),job.outcome?badge(job.outcome):node("span",job.error?.message||"—","muted"),stamp(job.submitted_at),action("Detalle","job",job.id),
  ]),"No hay tareas recientes","Selecciona un dispositivo y solicita su monitoreo.");
}
function renderResults(rows) {
  table("results-table",["Dispositivo","Resultado","Dirección consultada","Fecha local","Acciones"],rows.map(item=>[
    nameCell(item.device_name,item.device_host),badge(item.outcome),node("span",item.resolved_address||"No resuelta","mono"),stamp(item.observed_at),action("Detalle","result",item.id),
  ]),"Aún no hay resultados","Las observaciones aparecerán después del primer monitoreo.");
}
function renderReports(rows) {
  table("reports-table",["Dispositivo","Estado","Generado","SHA-256","Acciones"],rows.map(item=>{
    let download;if(item.generated_at){download=node("a","Descargar","action-button");download.href=`/reports/${encodeURIComponent(item.id)}/download`;download.download=item.filename;}
    const hash=node("span",item.sha256?item.sha256.slice(0,12)+"…":"—","mono");if(item.sha256)hash.title=item.sha256;
    const status=badge(item.status,item.status==="pending"&&item.generated_at?"Entrega pendiente":undefined);if(item.last_error)status.title=item.last_error.message;
    return [nameCell(item.device_name,item.filename),status,item.generated_at?stamp(item.generated_at):"Pendiente",hash,actions(action("Detalle","report",item.id),download,item.status==="failed"?action("Reintentar","retry-report",item.id):null)];
  }),"Todavía no hay reportes","Se generará un reporte cuando se registre un incidente.");
}
function renderNotifications(rows) {
  table("notifications-table",["Dispositivo","Evento","Entrega","Intentos","Último error","Acciones"],rows.map(item=>[
    nameCell(item.device_name),labels[item.kind]||item.kind,badge(item.status),item.attempts,node("span",item.last_error?.message||"—","description-cell"),item.status==="failed"?action("Reintentar","retry-notification",item.id):"—",
  ]),"No hay notificaciones","Los incidentes y reportes nuevos generarán avisos por correo.");
}
function renderDNS() {
  const container=$("dns-results");container.replaceChildren();
  if(!state.dns.length){container.append(empty("Todavía no has hecho consultas","Ingresa un dominio y selecciona A, AAAA o MX."));return;}
  for(const query of state.dns){const item=node("article",null,"dns-item"),heading=node("div",null,"dns-item-heading");heading.append(nameCell(query.name,query.record_type),badge(query.status));item.append(heading);
    if(query.result){const values=node("div",null,"dns-values");for(const value of query.result.values)values.append(node("code",value));item.append(values,node("p",`TTL: ${query.result.ttl} s · Nombre canónico: ${query.result.canonical_name}`,"footnote"));}
    if(query.error)item.append(node("p",query.error.message,"form-error"));container.append(item);
  }
}
function pagination(resource,more) {
  state.pages[resource].more=more;
  $(resource+"-prev").disabled=state.pages[resource].offset===0;
  $(resource+"-next").disabled=!more;
  $(resource+"-page").textContent=`Página ${Math.floor(state.pages[resource].offset/20)+1}`;
}
async function loadView(view) {
  if(view==="overview"){const {data}=await api("/incidents?limit=5");if(state.view===view)renderIncidents(data,"recent-incidents");return;}
  if(view==="dns"){const pending=state.dns.filter(query=>["queued","running"].includes(query.status));await Promise.all(pending.map(async query=>{const {data}=await api(`/dns/queries/${encodeURIComponent(query.id)}`);Object.assign(query,data);}));if(state.view===view)renderDNS();return;}
  if(view==="monitoring"){renderJobs();view="results";}
  const page=state.pages[view];if(!page)return;
  const params=new URLSearchParams({limit:"20",offset:String(page.offset)});
  if(view==="devices"){if($("device-search").value.trim())params.set("q",$("device-search").value.trim());if($("device-status").value)params.set("status",$("device-status").value);}
  const key=params.toString(),{data,more}=await api(`/${view}?${params}`);
  if(page.offset!==Number(params.get("offset"))||(state.view!==view&&!(view==="results"&&state.view==="monitoring")))return;
  if(view==="devices"){const current=new URLSearchParams({limit:"20",offset:String(page.offset)});if($("device-search").value.trim())current.set("q",$("device-search").value.trim());if($("device-status").value)current.set("status",$("device-status").value);if(current.toString()!==key)return;state.deviceNames=Object.fromEntries(data.map(item=>[item.id,item.name]));}
  state.data[view]=data;
  ({devices:renderDevices,results:renderResults,incidents:renderIncidents,reports:renderReports,notifications:renderNotifications}[view])(data);
  pagination(view,more);
}
async function refresh() {
  if(state.refreshing){state.again=true;return;}state.refreshing=true;$("refresh").disabled=true;
  try {
    const [health,stats,jobs]=await Promise.all([api("/health"),api("/statistics"),api("/monitoring/jobs?limit=100")]);
    state.jobs=jobs.data;renderStatistics(stats.data);
    $("server-mode").textContent=health.data.mode;$("connection-state").textContent="Servidor disponible";$("connection-indicator").className="connection-dot online";
    await loadView(state.view);$("global-error").hidden=true;$("updated-at").textContent=`Actualizado ${new Date().toLocaleTimeString("es-MX",{hour:"2-digit",minute:"2-digit"})}`;
  } catch(error) {$("global-error").textContent=`No se pudo actualizar la información. ${error.message}`;$("global-error").hidden=false;$("connection-state").textContent="Datos no disponibles";$("connection-indicator").className="connection-dot offline";}
  finally {state.refreshing=false;$("refresh").disabled=false;if(state.again){state.again=false;refresh();}}
}
function navigate() {
  const requested=location.hash.slice(1);state.view=titles[requested]?requested:"overview";
  const [title,description]=titles[state.view];$("page-title").textContent=title;$("page-description").textContent=description;document.title=`DNM · ${title}`;
  for(const section of document.querySelectorAll("[data-view]"))section.hidden=section.dataset.view!==state.view;
  for(const link of document.querySelectorAll("[data-nav]")){if(link.dataset.nav===state.view)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");}
  refresh();
}
function detail(title,data,description="") {$("detail-title").textContent=title;$("detail-description").textContent=description;$("detail-content").textContent=JSON.stringify(data,null,2);$("detail-dialog").showModal();}
document.addEventListener("click",async event=>{
  const register=event.target.closest("[data-register]");if(register){$("register-form").reset();$("register-error").hidden=true;$("register-dialog").showModal();return;}
  const close=event.target.closest("[data-close]");if(close){close.closest("dialog").close();return;}
  const button=event.target.closest("[data-action]");if(!button)return;const id=encodeURIComponent(button.dataset.id);button.disabled=true;
  try {switch(button.dataset.action){
    case "monitor": {await api("/monitoring/jobs",{device_id:button.dataset.id});toast("Monitoreo solicitado. Sigue su progreso en Monitoreo.");break;}
    case "device": {const {data}=await api(`/devices/${id}`);detail(data.device.name,data,data.latest_result?"Último resultado de monitoreo guardado.":"Este dispositivo todavía no tiene resultados de monitoreo.");break;}
    case "result": {const {data}=await api(`/results/${id}`);detail("Resultado de monitoreo",data,"Los contadores son acumulados; los valores grandes se muestran sin pérdida de precisión.");break;}
    case "job": {const {data}=await api(`/monitoring/jobs/${id}`);detail("Detalle de tarea",data);break;}
    case "report": {const {data}=await api(`/reports/${id}`);detail("Reporte de incidente",data,data.generated_at?"Documento generado y estado de su entrega.":"El documento está pendiente de generación.");break;}
    case "queue-report": {await api("/reports",{incident_id:button.dataset.id});toast("Reporte en cola de generación.");break;}
    case "retry-report": {await api(`/reports/${id}/retry`,{});toast("Reintento de reporte en cola.");break;}
    case "retry-notification": {await api(`/notifications/${id}/retry`,{});toast("Reintento de correo en cola.");break;}
  } await refresh();}catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
$("register-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type="submit"]');button.disabled=true;$("register-error").hidden=true;
  try {const fields=new FormData(form);await api("/devices",{name:fields.get("name"),host:fields.get("host"),snmp_port:Number(fields.get("snmp_port"))});$("register-dialog").close();toast("Dispositivo registrado. Ya puedes solicitar su monitoreo.");$("device-search").value="";$("device-status").value="";state.pages.devices.offset=0;if(state.view!=="devices")location.hash="devices";else await refresh();}
  catch(error){$("register-error").textContent=error.message;$("register-error").hidden=false;}finally{button.disabled=false;}
});
$("dns-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector("button");button.disabled=true;
  try {const {data}=await api("/dns/queries",{name:$("dns-name").value,record_type:$("dns-type").value});state.dns.unshift(data);state.dns=state.dns.slice(0,10);renderDNS();await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}
});
for(const resource of Object.keys(state.pages)){for(const direction of ["prev","next"]){$(resource+"-"+direction).addEventListener("click",()=>{state.pages[resource].offset+=direction==="next"?20:-20;refresh();});}}
let searchTimer;$("device-search").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.pages.devices.offset=0;refresh();},300);});
$("device-status").addEventListener("change",()=>{state.pages.devices.offset=0;refresh();});
$("refresh").addEventListener("click",refresh);window.addEventListener("hashchange",navigate);
setInterval(()=>{if(!document.hidden)refresh();},3000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});navigate();
