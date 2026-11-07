import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

import database as bd
from database import (ObjectId, ahora_texto, bloqueos, notificaciones, oid,
                      reservaciones, salas, usuarios)

# ------------------------------------------------------------------ configuración
app = Flask(__name__)

_clave = os.environ.get("SECRET_KEY")
if not _clave:
    _clave = secrets.token_hex(32)
    print("AVISO: SECRET_KEY no está definida; se usa una temporal "
          "(las sesiones se cerrarán al reiniciar). Define SECRET_KEY en producción.")
app.secret_key = _clave
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

DOMINIO = os.environ.get("DOMINIO", "").strip().lower()  # ej. "@miuniversidad.edu"
HORA_MIN, HORA_MAX = "07:00", "21:00"
SLOTS = [f"{m // 60:02d}:{m % 60:02d}" for m in range(7 * 60, 21 * 60, 30)]
RE_HORA = re.compile(r"^([01]\d|2[0-3]):(00|30)$")
RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

try:
    bd.conectar()
    bd.inicializar(sembrar_demo=os.environ.get("SEED_DEMO") == "1")
except RuntimeError as e:
    print(f"ERROR: {e}")
    raise SystemExit(1)


# ------------------------------------------------------------------ utilidades
def ahora_hhmm():
    return datetime.now().strftime("%H:%M")


def minutos(h):
    return int(h[:2]) * 60 + int(h[3:])


def _n(d):
    """Agrega 'id' (texto) a un documento de Mongo para usarlo en plantillas."""
    if d is not None:
        d["id"] = str(d["_id"])
    return d


def lista(cursor):
    return [_n(d) for d in cursor]


def nombres(coleccion, ids):
    ids = list(set(ids))
    return {d["_id"]: d["nombre"] for d in coleccion.find({"_id": {"$in": ids}})}


def enriquecer(rs):
    """Agrega nombre de sala y de docente a una lista de reservaciones."""
    sn = nombres(salas, [r["sala_id"] for r in rs])
    un = nombres(usuarios, [r["usuario_id"] for r in rs])
    for r in rs:
        r["sala"] = sn.get(r["sala_id"], "(sala eliminada)")
        r["docente"] = un.get(r["usuario_id"], "(usuario eliminado)")
    return rs


def uid():
    return ObjectId(session["uid"])


def notificar(usuario_id, msg):
    notificaciones.insert_one({"usuario_id": usuario_id, "mensaje": msg,
                               "leida": False, "creada": ahora_texto()})


def notificar_encargados(msg):
    for e in usuarios.find({"rol": "encargado"}):
        notificar(e["_id"], msg)


def conflicto(sala_id, fecha, ini, fin, excluir=None):
    q = {"sala_id": sala_id, "fecha": fecha, "estado": "Aprobada",
         "hora_inicio": {"$lt": fin}, "hora_fin": {"$gt": ini}}
    if excluir:
        q["_id"] = {"$ne": excluir}
    if reservaciones.find_one(q):
        return True
    return bool(bloqueos.find_one({"sala_id": sala_id, "fecha": fecha,
                                   "hora_inicio": {"$lt": fin},
                                   "hora_fin": {"$gt": ini}}))


def validar_horario(fecha_txt, ini, fin, anticipacion=60):
    """Valida fecha y horas. Devuelve (date, None) o (None, mensaje_de_error)."""
    try:
        dia = datetime.strptime(fecha_txt or "", "%Y-%m-%d").date()
    except ValueError:
        return None, "Fecha inválida."
    if not (RE_HORA.match(ini or "") and RE_HORA.match(fin or "")):
        return None, "Usa horas en bloques de 30 minutos (ej. 08:00 o 08:30)."
    if ini < HORA_MIN or fin > HORA_MAX:
        return None, "El horario permitido es de 07:00 a 21:00."
    if fin <= ini:
        return None, "La hora de fin debe ser posterior a la de inicio."
    hoy = date.today()
    if dia < hoy:
        return None, "No puedes usar una fecha pasada."
    if anticipacion and dia > hoy + timedelta(days=anticipacion):
        return None, f"Reserva con hasta {anticipacion} días de anticipación."
    if dia == hoy and ini < ahora_hhmm():
        return None, "Esa hora ya pasó."
    return dia, None


_ultima_limpieza = 0.0


def finalizar_vencidas():
    """Marca como Finalizada/Vencida lo que ya pasó (máx. una vez por minuto)."""
    global _ultima_limpieza
    if time.time() - _ultima_limpieza < 60:
        return
    _ultima_limpieza = time.time()
    hoy, ahora = date.today().isoformat(), ahora_hhmm()
    paso = {"$or": [{"fecha": {"$lt": hoy}},
                    {"fecha": hoy, "hora_fin": {"$lte": ahora}}]}
    reservaciones.update_many({"estado": "Aprobada", **paso},
                              {"$set": {"estado": "Finalizada"}})
    reservaciones.update_many({"estado": "Pendiente", **paso},
                              {"$set": {"estado": "Vencida",
                                        "motivo_rechazo": "No fue atendida a tiempo"}})


# ------------------------------------------------------------------ seguridad
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["csrf_input"] = lambda: Markup(
    f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


@app.before_request
def antes():
    if request.method == "POST":
        esperado = session.get("csrf", "")
        enviado = request.form.get("csrf_token", "")
        if not esperado or not secrets.compare_digest(enviado.encode(), esperado.encode()):
            flash("Formulario inválido o sesión expirada. Intenta de nuevo.", "error")
            return redirect(url_for("calendario" if "uid" in session else "login"))
    if "uid" in session:
        finalizar_vencidas()


@app.after_request
def cabeceras(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


INTENTOS = {}  # (ip, correo) -> [timestamps de intentos fallidos]


def demasiados_intentos(clave):
    ahora = time.time()
    recientes = [t for t in INTENTOS.get(clave, []) if ahora - t < 300]
    INTENTOS[clave] = recientes
    return len(recientes) >= 5


def login_required(rol=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "uid" not in session:
                return redirect(url_for("login"))
            if rol and session["rol"] != rol:
                flash("No tienes permiso para esa sección.", "error")
                return redirect(url_for("calendario"))
            return f(*a, **k)
        return w
    return deco


@app.context_processor
def inject():
    n = 0
    if "uid" in session:
        n = notificaciones.count_documents({"usuario_id": uid(), "leida": False})
    return dict(sesion=session, no_leidas=n)


# ------------------------------------------------------------------ autenticación
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower()
        clave = (request.remote_addr, correo)
        if demasiados_intentos(clave):
            flash("Demasiados intentos fallidos. Espera 5 minutos.", "error")
            return render_template("login.html")
        u = usuarios.find_one({"correo": correo})
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            INTENTOS.pop(clave, None)
            session.clear()  # evita fijación de sesión
            session.permanent = True
            session.update(uid=str(u["_id"]), nombre=u["nombre"], rol=u["rol"])
            return redirect(url_for("calendario"))
        INTENTOS.setdefault(clave, []).append(time.time())
        flash("Correo o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        clave = request.form.get("password", "")
        if not nombre or len(nombre) > 100:
            flash("Escribe tu nombre completo (máx. 100 caracteres).", "error")
        elif not RE_CORREO.match(correo):
            flash("Correo inválido.", "error")
        elif DOMINIO and not correo.endswith(DOMINIO):
            flash(f"Usa tu cuenta institucional ({DOMINIO}).", "error")
        elif not 8 <= len(clave) <= 128:
            flash("La contraseña debe tener entre 8 y 128 caracteres.", "error")
        else:
            try:
                usuarios.insert_one({"nombre": nombre, "correo": correo,
                                     "password": generate_password_hash(clave),
                                     "rol": "docente"})
                flash("Cuenta creada. Inicia sesión.", "ok")
                return redirect(url_for("login"))
            except DuplicateKeyError:
                flash("Ese correo ya está registrado.", "error")
    return render_template("registro.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ calendario
@app.route("/")
@login_required()
def calendario():
    activas = lista(salas.find({"activa": True}).sort("nombre", 1))
    if not activas:
        return "No hay salas activas. Contacta al encargado del Centro de Cómputo.", 503
    sala = next((s for s in activas if s["id"] == request.args.get("sala")), activas[0])

    try:
        ref = datetime.strptime(request.args.get("semana", ""), "%Y-%m-%d").date()
    except ValueError:
        ref = date.today()
    if abs((ref - date.today()).days) > 800:  # evita fechas absurdas
        ref = date.today()
    lunes = ref - timedelta(days=ref.weekday())
    dias = [lunes + timedelta(days=i) for i in range(7)]
    rango = {"$gte": dias[0].isoformat(), "$lte": dias[-1].isoformat()}

    reservas = enriquecer(lista(reservaciones.find(
        {"sala_id": sala["_id"], "fecha": rango,
         "estado": {"$in": ["Aprobada", "Pendiente"]}})))
    bloq = lista(bloqueos.find({"sala_id": sala["_id"], "fecha": rango}))

    celdas = {}

    def marcar(f, ini, fin, texto, clase):
        primero = True
        for s in SLOTS:
            if ini <= s < fin:
                celdas[(f, s)] = (texto, clase, primero)
                primero = False

    # Las pendientes se dibujan primero para que las aprobadas queden encima.
    for r in sorted(reservas, key=lambda r: r["estado"] == "Aprobada"):
        marcar(r["fecha"], r["hora_inicio"], r["hora_fin"],
               f'{r["materia"]} · {r["docente"]}',
               "aprobada" if r["estado"] == "Aprobada" else "pendiente")
    for b in bloq:
        marcar(b["fecha"], b["hora_inicio"], b["hora_fin"],
               "Mantenimiento: " + (b.get("motivo") or ""), "bloqueo")

    return render_template(
        "calendario.html", hoy=date.today().isoformat(), ahora=ahora_hhmm(),
        salas=activas, sala_id=sala["id"], dias=dias, slots=SLOTS, celdas=celdas,
        prev=(lunes - timedelta(days=7)).isoformat(),
        nxt=(lunes + timedelta(days=7)).isoformat())


@app.route("/api/disponibilidad")
@login_required()
def api_disp():
    a = request.args
    sala = oid(a.get("sala"))
    if not sala or not all(a.get(k) for k in ("fecha", "inicio", "fin")):
        return {"libre": None, "msg": ""}
    _, err = validar_horario(a["fecha"], a["inicio"], a["fin"])
    if err:
        return {"libre": False, "msg": err}
    libre = not conflicto(sala, a["fecha"], a["inicio"], a["fin"])
    return {"libre": libre,
            "msg": "Horario disponible ✓" if libre else "Ese horario ya está ocupado o bloqueado."}


# ------------------------------------------------------------------ docente
@app.route("/reservar", methods=["GET", "POST"])
@login_required()
def reservar():
    activas = lista(salas.find({"activa": True}).sort("nombre", 1))
    if request.method == "POST":
        f = request.form
        sala = next((s for s in activas if s["id"] == f.get("sala")), None)
        ini, fin = f.get("hora_inicio", ""), f.get("hora_fin", "")
        materia, motivo = f.get("materia", "").strip(), f.get("motivo", "").strip()
        try:
            alumnos = int(f.get("alumnos", ""))
        except ValueError:
            alumnos = 0
        dia, err = validar_horario(f.get("fecha"), ini, fin)
        if err is None:
            if not sala:
                err = "Selecciona una sala válida."
            elif not materia or not motivo or len(materia) > 100 or len(motivo) > 500:
                err = "Materia (máx. 100) y motivo (máx. 500) son obligatorios."
            elif alumnos < 1:
                err = "Indica el número de alumnos."
            elif (minutos(fin) - minutos(ini)) > 240:
                err = "Máximo 4 horas por reservación."
            elif conflicto(sala["_id"], f["fecha"], ini, fin):
                err = "Ese horario ya está ocupado o bloqueado."
        if err:
            flash(err, "error")
            return render_template("reservar.html", salas=activas, hoy=date.today(),
                                   sala_sel=f.get("sala"), form=f)
        reservaciones.insert_one({
            "usuario_id": uid(), "sala_id": sala["_id"], "fecha": f["fecha"],
            "hora_inicio": ini, "hora_fin": fin, "materia": materia, "motivo": motivo,
            "alumnos": alumnos, "estado": "Pendiente", "motivo_rechazo": None,
            "creada": ahora_texto()})
        notificar_encargados(f'Nueva solicitud de {session["nombre"]} para el {f["fecha"]}.')
        flash("Solicitud enviada. Espera la respuesta del encargado.", "ok")
        return redirect(url_for("mis_reservaciones"))
    return render_template("reservar.html", salas=activas, hoy=date.today(),
                           sala_sel=request.args.get("sala"), form={})


@app.route("/mis-reservaciones")
@login_required()
def mis_reservaciones():
    rs = enriquecer(lista(reservaciones.find({"usuario_id": uid()})
                          .sort([("fecha", -1), ("hora_inicio", 1)])))
    return render_template("mis.html", rs=rs, hoy=date.today().isoformat())


@app.route("/cancelar/<rid>", methods=["POST"])
@login_required()
def cancelar(rid):
    r = reservaciones.find_one({"_id": oid(rid), "usuario_id": uid()})
    if r and r["estado"] in ("Pendiente", "Aprobada") and r["fecha"] >= date.today().isoformat():
        reservaciones.update_one({"_id": r["_id"]}, {"$set": {"estado": "Cancelada"}})
        if r["estado"] == "Aprobada":
            notificar_encargados(f'{session["nombre"]} canceló su reservación del '
                                 f'{r["fecha"]} ({r["hora_inicio"]}-{r["hora_fin"]}).')
        flash("Reservación cancelada.", "ok")
    return redirect(url_for("mis_reservaciones"))


@app.route("/notificaciones", endpoint="notificaciones")
@login_required()
def ver_notificaciones():
    ns = lista(notificaciones.find({"usuario_id": uid()}).sort("_id", -1).limit(50))
    notificaciones.update_many({"usuario_id": uid(), "leida": False}, {"$set": {"leida": True}})
    return render_template("notificaciones.html", ns=ns)


# ------------------------------------------------------------------ encargado
@app.route("/panel")
@login_required("encargado")
def panel():
    hoy = date.today().isoformat()
    pend = enriquecer(lista(reservaciones.find({"estado": "Pendiente"})
                            .sort([("fecha", 1), ("hora_inicio", 1)])))
    apro = enriquecer(lista(reservaciones.find({"estado": "Aprobada", "fecha": {"$gte": hoy}})
                            .sort([("fecha", 1), ("hora_inicio", 1)])))
    todas = lista(salas.find({}).sort("nombre", 1))
    activas = [s for s in todas if s.get("activa")]
    bloq = lista(bloqueos.find({"fecha": {"$gte": hoy}}).sort([("fecha", 1), ("hora_inicio", 1)]))
    sn = {s["_id"]: s["nombre"] for s in todas}
    for b in bloq:
        b["sala"] = sn.get(b["sala_id"], "(sala eliminada)")
    uso = [{"nombre": s["nombre"],
            "total": reservaciones.count_documents(
                {"sala_id": s["_id"], "estado": {"$in": ["Aprobada", "Finalizada"]}})}
           for s in todas]
    docentes = lista(usuarios.find({"rol": "docente"}).sort("nombre", 1))
    return render_template("panel.html", pend=pend, apro=apro, salas=todas,
                           activas=activas, bloq=bloq, uso=uso, docentes=docentes,
                           hoy=date.today())


@app.route("/resolver/<rid>", methods=["POST"])
@login_required("encargado")
def resolver(rid):
    r = reservaciones.find_one({"_id": oid(rid), "estado": "Pendiente"})
    if not r:
        return redirect(url_for("panel"))
    if request.form.get("accion") == "aprobar":
        if conflicto(r["sala_id"], r["fecha"], r["hora_inicio"], r["hora_fin"], r["_id"]):
            flash("No se puede aprobar: hay un traslape con otra reservación o bloqueo.", "error")
            return redirect(url_for("panel"))
        reservaciones.update_one({"_id": r["_id"]}, {"$set": {"estado": "Aprobada"}})
        notificar(r["usuario_id"], f'✅ Tu reservación del {r["fecha"]} '
                                   f'({r["hora_inicio"]}-{r["hora_fin"]}) fue APROBADA.')
    else:
        mot = request.form.get("motivo_rechazo", "").strip()[:200] or "Sin motivo especificado"
        reservaciones.update_one({"_id": r["_id"]},
                                 {"$set": {"estado": "Rechazada", "motivo_rechazo": mot}})
        notificar(r["usuario_id"], f'❌ Tu reservación del {r["fecha"]} fue RECHAZADA. Motivo: {mot}')
    return redirect(url_for("panel"))


@app.route("/bloqueo", methods=["POST"])
@login_required("encargado")
def bloqueo():
    f = request.form
    sala = salas.find_one({"_id": oid(f.get("sala"))})
    ini, fin = f.get("hora_inicio", ""), f.get("hora_fin", "")
    motivo = f.get("motivo", "").strip()[:200]
    _, err = validar_horario(f.get("fecha"), ini, fin, anticipacion=None)
    if not err and not sala:
        err = "Selecciona una sala válida."
    if err:
        flash(err, "error")
        return redirect(url_for("panel"))

    afectadas = list(reservaciones.find({
        "sala_id": sala["_id"], "fecha": f["fecha"],
        "estado": {"$in": ["Aprobada", "Pendiente"]},
        "hora_inicio": {"$lt": fin}, "hora_fin": {"$gt": ini}}))
    if afectadas and not f.get("cancelar_afectadas"):
        flash(f"Hay {len(afectadas)} reservación(es) en ese horario. Marca "
              "«Cancelar reservaciones afectadas» para bloquear de todos modos.", "error")
        return redirect(url_for("panel"))
    for r in afectadas:
        razon = "Sala bloqueada" + (f": {motivo}" if motivo else "")
        reservaciones.update_one({"_id": r["_id"]},
                                 {"$set": {"estado": "Cancelada", "motivo_rechazo": razon}})
        notificar(r["usuario_id"], f'⚠️ Tu reservación del {r["fecha"]} '
                                   f'({r["hora_inicio"]}-{r["hora_fin"]}) fue cancelada. {razon}.')
    bloqueos.insert_one({"sala_id": sala["_id"], "fecha": f["fecha"], "hora_inicio": ini,
                         "hora_fin": fin, "motivo": motivo})
    flash("Horario bloqueado." + (f" Se cancelaron {len(afectadas)} reservación(es)." if afectadas else ""), "ok")
    return redirect(url_for("panel"))


@app.route("/bloqueo/<bid>/eliminar", methods=["POST"])
@login_required("encargado")
def del_bloqueo(bid):
    bloqueos.delete_one({"_id": oid(bid)})
    flash("Bloqueo eliminado.", "ok")
    return redirect(url_for("panel"))


@app.route("/sala/nueva", methods=["POST"])
@login_required("encargado")
def nueva_sala():
    nombre = request.form.get("nombre", "").strip()[:30]
    if not nombre:
        flash("Escribe el nombre de la sala.", "error")
    else:
        try:
            salas.insert_one({"nombre": nombre, "activa": True})
            flash("Sala creada.", "ok")
        except DuplicateKeyError:
            flash("Ya existe una sala con ese nombre.", "error")
    return redirect(url_for("panel"))


@app.route("/sala/<sid>", methods=["POST"])
@login_required("encargado")
def editar_sala(sid):
    sala = salas.find_one({"_id": oid(sid)})
    if not sala:
        abort(404)
    nombre = request.form.get("nombre", "").strip()[:30]
    activa = bool(request.form.get("activa"))
    if not nombre:
        flash("El nombre de la sala no puede estar vacío.", "error")
    elif not activa and not salas.find_one({"activa": True, "_id": {"$ne": sala["_id"]}}):
        flash("Debe quedar al menos una sala activa.", "error")
    else:
        try:
            salas.update_one({"_id": sala["_id"]}, {"$set": {"nombre": nombre, "activa": activa}})
            flash(f"Sala {nombre} actualizada.", "ok")
        except DuplicateKeyError:
            flash("Ya existe una sala con ese nombre.", "error")
    return redirect(url_for("panel"))


# ------------------------------------------------------------------ docentes (encargado)
def _validar_docente(nombre, correo, clave):
    if not nombre or len(nombre) > 100:
        return "Escribe el nombre completo (máx. 100 caracteres)."
    if not RE_CORREO.match(correo):
        return "Correo inválido."
    if not 8 <= len(clave) <= 128:
        return "La contraseña debe tener entre 8 y 128 caracteres."
    return None


@app.route("/docente/nuevo", methods=["POST"])
@login_required("encargado")
def nuevo_docente():
    f = request.form
    nombre, correo, clave = f.get("nombre", "").strip(), f.get("correo", "").strip().lower(), f.get("password", "")
    err = _validar_docente(nombre, correo, clave)
    if err:
        flash(err, "error")
    else:
        try:
            usuarios.insert_one({"nombre": nombre, "correo": correo,
                                 "password": generate_password_hash(clave), "rol": "docente"})
            flash(f"Docente {nombre} agregado. Entrégale su correo y contraseña temporal.", "ok")
        except DuplicateKeyError:
            flash("Ese correo ya está registrado.", "error")
    return redirect(url_for("panel"))


@app.route("/docente/<did>/clave", methods=["POST"])
@login_required("encargado")
def clave_docente(did):
    d = usuarios.find_one({"_id": oid(did), "rol": "docente"})
    if not d:
        abort(404)
    clave = request.form.get("password", "")
    if not 8 <= len(clave) <= 128:
        flash("La contraseña debe tener entre 8 y 128 caracteres.", "error")
    else:
        usuarios.update_one({"_id": d["_id"]}, {"$set": {"password": generate_password_hash(clave)}})
        flash(f"Contraseña de {d['nombre']} actualizada.", "ok")
    return redirect(url_for("panel"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)
