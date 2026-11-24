const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
// Tema claro/oscuro
const root=document.documentElement;
try{root.dataset.theme=localStorage.getItem("tema")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light")}catch(e){}
$("#tema")?.addEventListener("click",()=>{root.dataset.theme=root.dataset.theme==="dark"?"light":"dark";try{localStorage.setItem("tema",root.dataset.theme)}catch(e){}});
// Logos: si la imagen no carga, se muestra la insignia de texto
$$("img[data-fallback]").forEach(i=>{
  const falla=()=>{i.style.display="none";const t=i.nextElementSibling;if(t)t.style.display="block"};
  i.addEventListener("error",falla);
  if(i.complete&&i.naturalWidth===0)falla();
});
// Avisos (toasts)
$$("[data-toast]").forEach((t,i)=>setTimeout(()=>{t.style.transition=".4s";t.style.opacity=0;setTimeout(()=>t.remove(),400)},4500+i*400));
// Selects que envían su formulario al cambiar, confirmaciones e impresión (sin JS en línea por la CSP)
$$("[data-autosubmit]").forEach(s=>s.addEventListener("change",()=>s.form.submit()));
$$("form[data-confirm]").forEach(f=>f.addEventListener("submit",e=>{if(!confirm(f.dataset.confirm))e.preventDefault()}));
$$("[data-print]").forEach(b=>b.addEventListener("click",()=>window.print()));
// Modal de reservación desde el calendario
const modal=$("#modal");
const abrir=()=>{modal?.classList.add("on");verificar()},cerrar=()=>modal?.classList.remove("on");
const prellenar=(fecha,hora)=>{
  const [h,m]=hora.split(":").map(Number),f=h*60+m+60,p=n=>String(n).padStart(2,"0");
  $("#f-fecha").value=fecha;$("#f-ini").value=hora;$("#f-fin").value=p(Math.floor(f/60))+":"+p(f%60);abrir()};
$$("td.libre").forEach(td=>td.addEventListener("click",()=>prellenar(td.dataset.fecha,td.dataset.hora)));
$("#abrir")?.addEventListener("click",abrir);$("#cerrar")?.addEventListener("click",cerrar);
modal?.addEventListener("click",e=>{if(e.target===modal)cerrar()});addEventListener("keydown",e=>e.key==="Escape"&&cerrar());
// Detalle de una clase o reservación del calendario (materia, docente, clave, cancelar)
const modalClase=$("#modal-clase");
const cerrarClase=()=>modalClase?.classList.remove("on");
const abrirClase=td=>{
  const d=td.dataset,esClase=d.tipo==="clase",cancelada=!!d.estado&&d.estado==="Cancelada";
  $("#cl-materia").textContent=d.materia;
  $("#cl-horario").textContent=d.horario;
  $("#cl-docente").textContent=d.docente||"Sin asignar";
  $("#cl-clave").textContent=d.clave||"Pendiente";
  $("#cl-clave-fila").hidden=!esClase;
  $("#cl-grupo").textContent=d.grupo;
  $("#cl-grupo-fila").hidden=!d.grupo;
  $("#cl-estado").textContent=d.estado||"";
  $("#cl-estado-fila").hidden=!d.estado;
  let motivo=d.motivo||"";
  if(cancelada&&d.por)motivo+=(motivo?" · ":"")+"cancelada por "+d.por;
  $("#cl-motivo-t").textContent=cancelada?"Motivo de la cancelación":"Motivo de la reservación";
  $("#cl-motivo").textContent=motivo;
  $("#cl-motivo-fila").hidden=!motivo;
  const aviso=$("#cl-aviso");aviso.textContent=d.aviso||"";aviso.hidden=!d.aviso;
  // Asistencia: punto verde (en la sala) / rojo (no está) y botones para registrarla
  const as=d.asist||"",puedeMarcar=d.marcar==="1";
  $("#cl-asist-fila").hidden=!(as||puedeMarcar);
  $("#cl-asist-punto").className="punto "+(as||"sin");
  let txt=as==="presente"?"En la sala":as==="ausente"?"No está en la sala":"Sin registro";
  if(as&&d.asistPor)txt+=" · "+d.asistPor+(d.asistHora?", "+d.asistHora:"");
  $("#cl-asist-txt").textContent=txt;
  const av=$("#cl-aviso-asist");av.textContent=d.avisoMarcar||"";av.hidden=!d.avisoMarcar;
  const fa=$("#cl-form-asistencia");fa.hidden=!puedeMarcar;
  if(puedeMarcar){
    const enc=d.modo==="encargado";
    fa.elements.next.value=location.pathname+location.search;
    fa.elements.tipo.value=d.tipo;fa.elements.id.value=esClase?d.hid:d.rid;fa.elements.fecha.value=d.fecha;
    $("#cl-asist-label").textContent=enc?"¿El docente está en la sala?":"¿Estás en la sala?";
    $("#cl-btn-presente").textContent=enc?"🟢 Está en la sala":"🟢 Estoy en la sala";
    $("#cl-btn-ausente").textContent=enc?"🔴 No está":"🔴 No estoy";
    $("#cl-btn-quitar").hidden=!(enc&&as);
  }
  // Reservar una hora liberada por una clase cancelada
  const rb=$("#cl-reservar");rb.hidden=d.reservable!=="1";
  rb.onclick=()=>{cerrarClase();prellenar(d.fecha,d.hora)};
  // Cancelar clase o reservación
  const fc=$("#cl-form-cancelar");fc.hidden=d.cancelar!=="1";
  if(!fc.hidden){
    fc.action=esClase?fc.dataset.urlClase:fc.dataset.urlReserva.replace("__ID__",d.rid);
    fc.elements.next.value=location.pathname+location.search;
    fc.elements.horario_id.disabled=!esClase;fc.elements.fecha.disabled=!esClase;
    fc.elements.horario_id.value=d.hid||"";fc.elements.fecha.value=d.fecha||"";
    fc.elements.motivo.value="";
    $("#cl-btn-cancelar").textContent=esClase?"Cancelar clase":"Cancelar reservación";
  }
  // Restablecer una clase cancelada (solo encargado)
  const fr=$("#cl-form-restablecer");fr.hidden=d.restablecer!=="1";
  if(!fr.hidden){fr.elements.next.value=location.pathname+location.search;fr.elements.cancelacion_id.value=d.cid}
  modalClase.classList.add("on");
};
$$("td[data-materia]").forEach(td=>{
  td.addEventListener("click",()=>abrirClase(td));
  td.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();abrirClase(td)}});
});
$("#cerrar-clase")?.addEventListener("click",cerrarClase);
modalClase?.addEventListener("click",e=>{if(e.target===modalClase)cerrarClase()});
addEventListener("keydown",e=>e.key==="Escape"&&cerrarClase());
// Verificación de disponibilidad en vivo
async function verificar(){
  const d=$("#disp");if(!d)return;
  const q=new URLSearchParams({sala:$("#f-sala").value,fecha:$("#f-fecha").value,inicio:$("#f-ini").value,fin:$("#f-fin").value});
  if([...q.values()].some(v=>!v)){d.className="disp";return}
  try{
    const r=await (await fetch("/api/disponibilidad?"+q)).json();
    d.className="disp "+(r.libre===null?"":r.libre?"ok":"no");d.textContent=r.msg;
  }catch(e){d.className="disp"}
}
["#f-sala","#f-fecha","#f-ini","#f-fin"].forEach(s=>$(s)?.addEventListener("change",verificar));
// Al elegir la hora de inicio, la de fin se calcula sola (1 hora después)
const iniEl=$("#f-ini"),finEl=$("#f-fin");
const sumaHora=v=>{const [h,m]=v.split(":").map(Number),t=h*60+m+60,p=n=>String(n).padStart(2,"0");return p(Math.floor(t/60))+":"+p(t%60)};
const autoFin=()=>{if(iniEl&&finEl&&/^\d{2}:\d{2}$/.test(iniEl.value)){finEl.value=sumaHora(iniEl.value);verificar()}};
iniEl?.addEventListener("change",autoFin);
iniEl?.addEventListener("input",autoFin);
// Buscador de tablas
$$("[data-filtro]").forEach(i=>i.addEventListener("input",()=>{
  const t=$(i.dataset.filtro),q=i.value.toLowerCase();
  $$("tbody tr",t).forEach(r=>r.style.display=r.textContent.toLowerCase().includes(q)?"":"none")}));
// Pestañas
$$(".tabs button").forEach(b=>b.addEventListener("click",()=>{
  $$(".tabs button").forEach(x=>x.classList.toggle("on",x===b));
  $$(".pane").forEach(p=>p.classList.toggle("on",p.id===b.dataset.tab))}));
// Abrir la pestaña indicada en la dirección (#t8, #t6...) al volver de guardar
if(location.hash){const b=$(`.tabs button[data-tab="${location.hash.slice(1)}"]`);if(b)b.click()}
// Bitácora: hoja carta vertical u horizontal (cambia la orientación de impresión al instante)
const selOrient=$("[data-orient]");
if(selOrient){
  const aplicar=()=>{
    const v=selOrient.value==="vertical",est=$("#estilo-pagina");
    if(est)est.textContent="@page{size:letter "+(v?"portrait":"landscape")+";margin:11mm 10mm 14mm}";
    $(".bit")?.classList.toggle("vertical",v);
  };
  selOrient.addEventListener("change",aplicar);aplicar();
}
// Barras animadas
$$(".bars span").forEach(s=>{const w=s.dataset.w;s.style.width=0;setTimeout(()=>s.style.width=w,150)});
// Mostrar / ocultar contraseña (sin JS en línea por la CSP)
$$("[data-ver-pass]").forEach(b=>b.addEventListener("click",()=>{
  const i=$("input",b.parentElement);if(!i)return;
  const ver=i.type==="password";
  i.type=ver?"text":"password";
  b.textContent=ver?"Ocultar":"Mostrar";
  b.setAttribute("aria-label",ver?"Ocultar contraseña":"Mostrar contraseña");
  b.setAttribute("aria-pressed",ver?"true":"false");
  i.focus();
}));
// Selects con opción "Otra/Otro": muestran su campo de texto solo cuando se elige esa opción
$$("select[data-otro]").forEach(s=>{
  const bloque=$(s.dataset.otro);if(!bloque)return;
  const campo=$("input,textarea",bloque);
  const actualizar=()=>{
    const otra=s.value==="__otra__"||s.value==="Otro";
    bloque.hidden=!otra;
    if(campo)campo.required=otra;
  };
  s.addEventListener("change",()=>{actualizar();const c=$("input,textarea",bloque);if(!bloque.hidden&&c)c.focus()});
  actualizar();
});
// Pantalla «En vivo»: se actualiza sola cada 30 segundos (sin recargar toda la página)
const vivo=$("#vivo");
if(vivo){
  const refrescar=async()=>{
    if(document.hidden)return;
    try{
      const r=await fetch(location.pathname+"?parcial=1",{credentials:"same-origin",headers:{"X-Requested-With":"fetch"}});
      if(r.redirected){location.reload();return}  // la sesión expiró: vuelve al inicio de sesión
      if(r.ok)vivo.innerHTML=await r.text();
    }catch(e){}
  };
  setInterval(refrescar,(+vivo.dataset.refresco||30)*1000);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)refrescar()});
}
