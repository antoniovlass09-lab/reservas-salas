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
$$("td.libre").forEach(td=>td.addEventListener("click",()=>{
  const [h,m]=td.dataset.hora.split(":").map(Number),f=h*60+m+60,p=n=>String(n).padStart(2,"0");
  $("#f-fecha").value=td.dataset.fecha;$("#f-ini").value=td.dataset.hora;$("#f-fin").value=p(Math.floor(f/60))+":"+p(f%60);abrir()}));
$("#abrir")?.addEventListener("click",abrir);$("#cerrar")?.addEventListener("click",cerrar);
modal?.addEventListener("click",e=>{if(e.target===modal)cerrar()});addEventListener("keydown",e=>e.key==="Escape"&&cerrar());
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
