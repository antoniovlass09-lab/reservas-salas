const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
// Tema claro/oscuro
const root=document.documentElement;
try{root.dataset.theme=localStorage.getItem("tema")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light")}catch(e){}
$("#tema")?.addEventListener("click",()=>{root.dataset.theme=root.dataset.theme==="dark"?"light":"dark";try{localStorage.setItem("tema",root.dataset.theme)}catch(e){}});
// Avisos (toasts)
$$("[data-toast]").forEach((t,i)=>setTimeout(()=>{t.style.transition=".4s";t.style.opacity=0;setTimeout(()=>t.remove(),400)},4500+i*400));
// Modal de reservación desde el calendario
const modal=$("#modal");
const abrir=()=>{modal.classList.add("on");verificar()},cerrar=()=>modal.classList.remove("on");
$$("td.libre").forEach(td=>td.addEventListener("click",()=>{
  const [h,m]=td.dataset.hora.split(":").map(Number),f=h*60+m+30,p=n=>String(n).padStart(2,"0");
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
