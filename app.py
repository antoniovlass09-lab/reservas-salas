import csv
import io
import os
import re
import secrets
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from markupsafe import Markup
from pymongo.errors import DuplicateKeyError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import database as bd
from database import (ObjectId, ahora_texto, bloqueos, materias, notificaciones,
                      oid, reservaciones, salas, usuarios)

# ------------------------------------------------------------------ configuración
app = Flask(__name__)

DEBUG = os.environ.get("FLASK_DEBUG") == "1"

_clave = os.environ.get("SECRET_KEY")
if not _clave:
    if not DEBUG:
        print("ERROR: define SECRET_KEY en tu archivo .env (cadena larga y aleatoria).")
        raise SystemExit(1)
    _clave = secrets.token_hex(32)
    print("AVISO: SECRET_KEY no definida; se usa una temporal (solo desarrollo).")
elif len(_clave) < 32:
    print("AVISO: tu SECRET_KEY es corta. Genera una con: "
          "python -c \"import secrets; print(secrets.token_hex(32))\"")
app.secret_key = _clave
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=64 * 1024,  # los formularios son pequeños; rechaza cargas grandes
)
if os.environ.get("TRUST_PROXY") == "1":  # solo si hay un proxy confiable delante (Render, Nginx...)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

LIMITE_POR_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "600"))  # por IP

DOMINIO = os.environ.get("DOMINIO", "").strip().lower()  # ej. "@miuniversidad.edu"
HORA_MIN, HORA_MAX = "07:00", "21:00"
SLOTS = [f"{m // 60:02d}:{m % 60:02d}" for m in range(7 * 60, 21 * 60, 60)]
RE_HORA = re.compile(r"^([01]\d|2[0-3]):(00|30)$")
RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NOMBRES_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

try:
    bd.conectar()
    bd.inicializar(sembrar_demo=os.environ.get("SEED_DEMO") == "1")
except RuntimeError as e:
    print(f"ERROR: {e}")
    raise SystemExit(1)


# ------------------------------------------------------------------ utilidades
LIMITES = {}  # clave -> [timestamps]; limitador en memoria (por proceso)
HASH_FALSO = generate_password_hash(secrets.token_hex(8))  # iguala tiempos si el usuario no existe


def _hist(clave, ventana):
    ahora = time.time()
    h = [t for t in LIMITES.get(clave, []) if ahora - t < ventana]
    if h:
        LIMITES[clave] = h
    else:
        LIMITES.pop(clave, None)
    return h


def excedido(clave, maximo, ventana):
    return len(_hist(clave, ventana)) >= maximo


def registrar(clave, ventana):
    LIMITES.setdefault(clave, []).append(time.time())
    if len(LIMITES) > 20000:  # evita que la memoria crezca sin límite
        for k in list(LIMITES):
            _hist(k, 3600)


def clave_segura(clave):
    """Devuelve un mensaje de error o None si la contraseña es aceptable."""
    if not 8 <= len(clave) <= 128:
        return "La contraseña debe tener entre 8 y 128 caracteres."
    if not (re.search(r"[A-Za-z]", clave) and re.search(r"\d", clave)):
        return "La contraseña debe incluir letras y números."
    return None


def materias_activas():
    """Nombres de materia que el docente puede elegir al reservar."""
    docs = materias.find({"activa": {"$ne": False}})
    return sorted({d.get("nombre") or d["abreviatura"] for d in docs}, key=str.lower)


def materias_permitidas():
    """(lista, restringido): materias que el usuario puede elegir al reservar.

    Un docente con materias asignadas solo ve esas; sin asignaciones (o si es
    encargado) ve todo el catálogo activo."""
    todas = materias_activas()
    if session.get("rol") != "docente":
        return todas, False
    u = usuarios.find_one({"_id": uid()}) or {}
    asignadas = u.get("materias") or []
    if not asignadas:
        return todas, False
    return [m for m in todas if m in asignadas], True


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
    if bloqueos.find_one({"sala_id": sala_id, "fecha": fecha,
                          "hora_inicio": {"$lt": fin},
                          "hora_fin": {"$gt": ini}}):
        return True
    # Clases del horario académico de la sala.
    sala = salas.find_one({"_id": sala_id})
    try:
        dia = NOMBRES_DIAS[datetime.strptime(fecha, "%Y-%m-%d").weekday()]
    except ValueError:
        return False
    return bool(sala and bd.db["horarios_academicos"].find_one({
        "sala": sala["nombre"], "dia": dia, "estado": "ocupada",
        "hora_inicio": {"$lt": fin}, "hora_fin": {"$gt": ini}}))


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


CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.before_request
def antes():
    ip = request.remote_addr or "?"
    if request.endpoint != "static":
        if excedido(("global", ip), LIMITE_POR_MIN, 60):
            abort(429)
        registrar(("global", ip), 60)
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
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if app.config["SESSION_COOKIE_SECURE"]:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint != "static" and "uid" in session:
        h["Cache-Control"] = "no-store"
    return resp


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
        ip = request.remote_addr or "?"
        correo = request.form.get("correo", "").strip().lower()[:200]
        k1, k2 = ("login", ip, correo), ("login-ip", ip)
        if excedido(k1, 5, 300) or excedido(k2, 30, 300):
            flash("Demasiados intentos fallidos. Espera 5 minutos.", "error")
            return render_template("login.html"), 429
        u = usuarios.find_one({"correo": correo})
        ok = check_password_hash(u["password"] if u else HASH_FALSO,
                                 request.form.get("password", ""))
        if u and ok:
            LIMITES.pop(k1, None)
            session.clear()  # evita fijación de sesión
            session.permanent = True
            session.update(uid=str(u["_id"]), nombre=u["nombre"], rol=u["rol"])
            return redirect(url_for("calendario"))
        registrar(k1, 300)
        registrar(k2, 300)
        flash("Correo o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if excedido(("registro", ip), 20, 3600):
            flash("Demasiados registros desde tu conexión. Intenta más tarde.", "error")
            return render_template("registro.html"), 429
        registrar(("registro", ip), 3600)
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        clave = request.form.get("password", "")
        if not nombre or len(nombre) > 100:
            flash("Escribe tu nombre completo (máx. 100 caracteres).", "error")
        elif not RE_CORREO.match(correo):
            flash("Correo inválido.", "error")
        elif DOMINIO and not correo.endswith(DOMINIO):
            flash(f"Usa tu cuenta institucional ({DOMINIO}).", "error")
        elif clave_segura(clave):
            flash(clave_segura(clave), "error")
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

    sala = next(
        (s for s in activas if s["id"] == request.args.get("sala")),
        activas[0]
    )

    try:
        ref = datetime.strptime(
            request.args.get("semana", ""), "%Y-%m-%d"
        ).date()
    except ValueError:
        ref = date.today()

    if abs((ref - date.today()).days) > 800:
        ref = date.today()

    lunes = ref - timedelta(days=ref.weekday())
    dias = [lunes + timedelta(days=i) for i in range(7)]

    rango = {
        "$gte": dias[0].isoformat(),
        "$lte": dias[-1].isoformat()
    }

    reservas = enriquecer(lista(reservaciones.find(
        {
            "sala_id": sala["_id"],
            "fecha": rango,
            "estado": {"$in": ["Aprobada", "Pendiente"]}
        }
    )))

    bloq = lista(bloqueos.find({
        "sala_id": sala["_id"],
        "fecha": rango
    }))

    celdas = {}

    def marcar(f, ini, fin, texto, clase):
        primero = True

        for s in SLOTS:
            if ini <= s < fin:
                celdas[(f, s)] = (texto, clase, primero)
                primero = False

    # Colores identificadores de cada materia.
    colores_materias = {
        "PROG": "materia-prog",
        "FUND. PROGRAMACIÓN.": "materia-fprog",
        "CEMP": "materia-cemp",
        "MATEM. DISCRETAS.": "materia-md",
        "ARQ. COMP.": "materia-arq",
        "LENG. INTERFAZ.": "materia-li",
        "ESTRUCTURA DE DATOS": "materia-edat",
        "FUND. TELECOM.": "materia-ftel",
        "INV OPERACIONES": "materia-inv",
        "LyA II": "materia-lya",
        "IO": "materia-io",
        "SO": "materia-so",
        "ING. SOFT": "materia-isw",
        "IA": "materia-ia",
        "CDAT": "materia-cdat",
        "TALLER BD": "materia-tbd",
    }

    nombres_dias = NOMBRES_DIAS

    dias_semana = [nombres_dias[d.weekday()] for d in dias]

    # Consultar los horarios académicos de la sala seleccionada.
    horarios = lista(bd.db["horarios_academicos"].find({
        "sala": sala["nombre"],
        "dia": {"$in": dias_semana},
        "estado": "ocupada"
    }))

    # Obtener los nombres oficiales de las materias.
    catalogo = lista(bd.db["materias"].find({}))

    nombres_materias = {
        m["abreviatura"]: m.get("nombre") or m["abreviatura"]
        for m in catalogo
    }
    grupos_materias = {m["abreviatura"]: m.get("grupo") for m in catalogo}

    # Dibujar primero las clases académicas.
    for h in horarios:
        dia = h["dia"]
        fecha = next(
            (d.isoformat() for d in dias
             if nombres_dias[d.weekday()] == dia),
            None
        )

        if not fecha:
            continue

        abreviatura = h.get("materia_abreviatura")
        nombre = nombres_materias.get(
            abreviatura,
            abreviatura or "Materia pendiente"
        )

        grupo = grupos_materias.get(abreviatura)
        if grupo:
            nombre = f"{nombre} · Gpo. {grupo}"

        clase_color = colores_materias.get(abreviatura) or (
            f"materia-x{zlib.crc32(abreviatura.encode()) % 8}"
            if abreviatura else "materia-default"
        )

        marcar(
            fecha,
            h["hora_inicio"],
            h["hora_fin"],
            nombre,
            clase_color
        )

    # Dibujar las reservaciones encima de las clases.
    for r in sorted(reservas, key=lambda r: r["estado"] == "Aprobada"):
        marcar(
            r["fecha"],
            r["hora_inicio"],
            r["hora_fin"],
            f'{r["materia"]} · {r["docente"]}',
            "aprobada" if r["estado"] == "Aprobada" else "pendiente"
        )

    # Dibujar los bloqueos.
    for b in bloq:
        marcar(
            b["fecha"],
            b["hora_inicio"],
            b["hora_fin"],
            "Mantenimiento: " + (b.get("motivo") or ""),
            "bloqueo"
        )

    mats, restringido = materias_permitidas()
    return render_template(
        "calendario.html",
        hoy=date.today().isoformat(),
        ahora=ahora_hhmm(),
        salas=activas,
        materias=mats,
        materias_restringido=restringido,
        sala_id=sala["id"],
        dias=dias,
        slots=SLOTS,
        celdas=celdas,
        prev=(lunes - timedelta(days=7)).isoformat(),
        nxt=(lunes + timedelta(days=7)).isoformat()
    )

# ------------------------------------------------------------------ docente

@app.route("/reservar", methods=["GET", "POST"])
@login_required()
def reservar():
    activas = lista(salas.find({"activa": True}).sort("nombre", 1))
    catalogo, restringido = materias_permitidas()

    def pagina(sala_sel, form):
        return render_template("reservar.html", salas=activas, materias=catalogo,
                               materias_restringido=restringido,
                               hoy=date.today(), sala_sel=sala_sel, form=form)

    if request.method == "POST":
        f = request.form
        sala = next((s for s in activas if s["id"] == f.get("sala")), None)

        ini = f.get("hora_inicio", "")
        fin = f.get("hora_fin", "")
        materia = f.get("materia", "").strip()
        otra_materia = materia == "__otra__"
        if otra_materia:
            materia = f.get("materia_otra", "").strip()

        tipo_motivo = f.get("motivo_tipo", "")
        if tipo_motivo == "Práctica":
            motivo = "Práctica"
        elif tipo_motivo == "Otro":
            motivo = f.get("motivo_otro", "").strip()
        else:
            motivo = ""

        dia, err = validar_horario(f.get("fecha"), ini, fin)

        if err is None:
            if not sala:
                err = "Selecciona una sala válida."

            elif restringido and not catalogo:
                err = "No tienes materias activas asignadas. Contacta al encargado."

            elif not materia or not motivo or len(materia) > 100 or len(motivo) > 500:
                err = "Materia (máx. 100) y motivo (máx. 500) son obligatorios. Si elegiste «Otra» u «Otro», escríbelos."

            elif catalogo and not otra_materia and materia not in catalogo:
                err = "Selecciona una materia de la lista."

            elif minutos(fin) - minutos(ini) != 60:
                err = "Cada reservación debe durar exactamente una hora."

            elif conflicto(sala["_id"], f["fecha"], ini, fin):
                err = "Ese horario ya está ocupado o bloqueado."

        if err:
            flash(err, "error")
            return pagina(f.get("sala"), f)

        reservaciones.insert_one({
            "usuario_id": uid(),
            "sala_id": sala["_id"],
            "fecha": f["fecha"],
            "hora_inicio": ini,
            "hora_fin": fin,
            "materia": materia,
            "motivo": motivo,
            "estado": "Pendiente",
            "motivo_rechazo": None,
            "creada": ahora_texto()
        })

        notificar_encargados(
            f'Nueva solicitud de {session["nombre"]} para el {f["fecha"]}.'
        )

        flash("Solicitud enviada. Espera la respuesta del encargado.", "ok")
        return redirect(url_for("mis_reservaciones"))

    return pagina(request.args.get("sala"), {})


@app.route("/mis-reservaciones")
@login_required()
def mis_reservaciones():
    mis = enriquecer(lista(
        reservaciones.find({"usuario_id": uid()}).sort("creada", -1)
    ))
    return render_template("mis.html", rs=mis, hoy=date.today().isoformat())


@app.route("/cancelar/<rid>", methods=["POST"])
@login_required()
def cancelar(rid):
    r = reservaciones.find_one({"_id": oid(rid), "usuario_id": uid(),
                                "estado": {"$in": ["Pendiente", "Aprobada"]}})
    if not r or r["fecha"] < date.today().isoformat():
        flash("No se puede cancelar esa reservación.", "error")
        return redirect(url_for("mis_reservaciones"))
    reservaciones.update_one({"_id": r["_id"]},
                             {"$set": {"estado": "Cancelada",
                                       "motivo_rechazo": "Cancelada por el docente"}})
    if r["estado"] == "Aprobada":
        notificar_encargados(f'{session["nombre"]} canceló su reservación del '
                             f'{r["fecha"]} ({r["hora_inicio"]}-{r["hora_fin"]}).')
    flash("Reservación cancelada.", "ok")
    return redirect(url_for("mis_reservaciones"))


@app.route("/notificaciones")
@login_required()
def notificaciones_view():
    ns = lista(notificaciones.find({"usuario_id": uid()})
               .sort("_id", -1).limit(100))
    notificaciones.update_many({"usuario_id": uid(), "leida": False},
                               {"$set": {"leida": True}})
    return render_template("notificaciones.html", ns=ns)


@app.route("/api/disponibilidad")
@login_required()
def api_disponibilidad():
    a = request.args
    _, err = validar_horario(a.get("fecha"), a.get("inicio", ""), a.get("fin", ""))
    if err:
        return {"libre": None, "msg": err}
    sala = salas.find_one({"_id": oid(a.get("sala")), "activa": True})
    if not sala:
        return {"libre": None, "msg": "Selecciona una sala válida."}
    if conflicto(sala["_id"], a["fecha"], a["inicio"], a["fin"]):
        return {"libre": False, "msg": "✖ Ese horario está ocupado o bloqueado."}
    return {"libre": True, "msg": "✔ Horario disponible."}


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
    catalogo = sorted(lista(materias.find({})),
                      key=lambda m: (m.get("nombre") or m["abreviatura"]).lower())
    return render_template("panel.html", pend=pend, apro=apro, salas=todas, catalogo=catalogo,
                           materias_lista=materias_activas(),
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


# ------------------------------------------------------------------ materias (encargado)
@app.route("/materia/nueva", methods=["POST"])
@login_required("encargado")
def nueva_materia():
    f = request.form
    nombre = f.get("nombre", "").strip()[:100]
    abrev = (f.get("abreviatura", "").strip() or nombre)[:40]
    clave = f.get("clave", "").strip()[:20] or None
    grupo = f.get("grupo", "").strip()[:20] or None
    if not nombre:
        flash("Escribe el nombre de la materia.", "error")
    elif materias.find_one({"$or": [{"abreviatura": abrev}, {"nombre": nombre}]}):
        flash("Ya existe una materia con ese nombre o abreviatura.", "error")
    else:
        try:
            materias.insert_one({"abreviatura": abrev, "nombre": nombre, "clave": clave,
                                 "grupo": grupo, "activa": True,
                                 "actualizado_en": datetime.now(timezone.utc)})
            flash("Materia agregada.", "ok")
        except DuplicateKeyError:
            flash("Ya existe una materia con esa abreviatura.", "error")
    return redirect(url_for("panel"))


@app.route("/materia/<mid>", methods=["POST"])
@login_required("encargado")
def editar_materia(mid):
    m = materias.find_one({"_id": oid(mid)})
    if not m:
        abort(404)
    nombre = request.form.get("nombre", "").strip()[:100] or None
    clave = request.form.get("clave", "").strip()[:20] or None
    grupo = request.form.get("grupo", "").strip()[:20] or None
    activa = bool(request.form.get("activa"))
    if nombre and materias.find_one({"nombre": nombre, "_id": {"$ne": m["_id"]}}):
        flash("Ya existe otra materia con ese nombre.", "error")
    else:
        viejo = m.get("nombre") or m["abreviatura"]
        nuevo = nombre or m["abreviatura"]
        if viejo != nuevo:  # mantiene al día las materias asignadas a los docentes
            for d in usuarios.find({"materias": viejo}):
                usuarios.update_one({"_id": d["_id"]},
                                    {"$set": {"materias": [nuevo if x == viejo else x
                                                           for x in d["materias"]]}})
        materias.update_one({"_id": m["_id"]},
                            {"$set": {"nombre": nombre, "clave": clave, "grupo": grupo, "activa": activa,
                                      "actualizado_en": datetime.now(timezone.utc)}})
        flash(f"Materia {m['abreviatura']} actualizada.", "ok")
    return redirect(url_for("panel"))


# ------------------------------------------------------------------ reportes (encargado)
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _csv_seguro(v):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@app.route("/reportes")
@login_required("encargado")
def reportes():
    try:
        ini = datetime.strptime((request.args.get("mes") or "") + "-01", "%Y-%m-%d").date()
    except ValueError:
        ini = date.today().replace(day=1)
    sig = (ini + timedelta(days=32)).replace(day=1)
    ant = (ini - timedelta(days=1)).replace(day=1)
    todas = lista(salas.find({}).sort("nombre", 1))
    sala_sel = request.args.get("sala", "todas")
    q = {"fecha": {"$gte": ini.isoformat(), "$lt": sig.isoformat()},
         "estado": {"$in": ["Aprobada", "Finalizada"]}}
    sid = oid(sala_sel) if sala_sel != "todas" else None
    if sid and any(x["_id"] == sid for x in todas):
        q["sala_id"] = sid
    else:
        sala_sel = "todas"
    rs = enriquecer(lista(reservaciones.find(q).sort([("fecha", 1), ("hora_inicio", 1)])))
    for r in rs:
        r["horas"] = (minutos(r["hora_fin"]) - minutos(r["hora_inicio"])) / 60

    if request.args.get("formato") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Fecha", "Sala", "Inicio", "Fin", "Materia", "Docente", "Estado"])
        for r in rs:
            w.writerow([r["fecha"], _csv_seguro(r["sala"]), r["hora_inicio"], r["hora_fin"],
                        _csv_seguro(r["materia"]), _csv_seguro(r["docente"]),
                        r["estado"]])
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="rescom_reporte_{ini:%Y-%m}.csv"'})

    por_sala = {}
    for r in rs:
        sl = por_sala.setdefault(r["sala"], {"nombre": r["sala"], "reservas": 0,
                                             "horas": 0, "detalle": {}})
        sl["reservas"] += 1
        sl["horas"] += r["horas"]
        d = sl["detalle"].setdefault((r["materia"], r["docente"]),
                                     {"materia": r["materia"], "docente": r["docente"],
                                      "reservas": 0, "horas": 0})
        d["reservas"] += 1
        d["horas"] += r["horas"]
    resumen = []
    for nombre in sorted(por_sala, key=str.lower):
        sl = por_sala[nombre]
        sl["detalle"] = sorted(sl["detalle"].values(),
                               key=lambda d: (d["materia"].lower(), d["docente"].lower()))
        resumen.append(sl)
    return render_template(
        "reportes.html", rs=rs, resumen=resumen, salas=todas, sala_sel=sala_sel,
        mes=f"{ini:%Y-%m}", mes_txt=f"{MESES[ini.month - 1].capitalize()} {ini.year}",
        prev=f"{ant:%Y-%m}", nxt=f"{sig:%Y-%m}",
        total_horas=sum(r["horas"] for r in rs),
        generado=datetime.now().strftime("%d/%m/%Y %H:%M"))


# ------------------------------------------------------------------ docentes (encargado)
def _validar_docente(nombre, correo, clave):
    if not nombre or len(nombre) > 100:
        return "Escribe el nombre completo (máx. 100 caracteres)."
    if not RE_CORREO.match(correo):
        return "Correo inválido."
    return clave_segura(clave)


def _materias_validas(elegidas):
    """Deja solo materias activas del catálogo, sin repetir."""
    validas = set(materias_activas())
    return [m for m in dict.fromkeys(elegidas) if m in validas]


@app.route("/docente/<did>/materias", methods=["POST"])
@login_required("encargado")
def materias_docente(did):
    d = usuarios.find_one({"_id": oid(did), "rol": "docente"})
    if not d:
        abort(404)
    asignadas = _materias_validas(request.form.getlist("materias"))
    usuarios.update_one({"_id": d["_id"]}, {"$set": {"materias": asignadas}})
    flash(f"Materias de {d['nombre']} actualizadas." if asignadas else
          f"{d['nombre']} quedó sin restricción: podrá elegir cualquier materia.", "ok")
    return redirect(url_for("panel"))


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
                                 "password": generate_password_hash(clave), "rol": "docente",
                                 "materias": _materias_validas(f.getlist("materias"))})
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
    err = clave_segura(clave)
    if err:
        flash(err, "error")
    else:
        usuarios.update_one({"_id": d["_id"]}, {"$set": {"password": generate_password_hash(clave)}})
        flash(f"Contraseña de {d['nombre']} actualizada.", "ok")
    return redirect(url_for("panel"))


MENSAJES_ERROR = {
    404: "La página que buscas no existe.",
    405: "Acción no permitida.",
    413: "La solicitud es demasiado grande.",
    429: "Demasiadas solicitudes. Espera un momento e intenta de nuevo.",
    500: "Ocurrió un error interno. Intenta de nuevo en unos minutos.",
}


def _pagina_error(e):
    codigo = getattr(e, "code", 500)
    msg = MENSAJES_ERROR.get(codigo, "Ocurrió un error.")
    try:
        return render_template("error.html", codigo=codigo, mensaje=msg), codigo
    except Exception:  # p. ej. si la base de datos no responde
        return f"{codigo} · {msg}", codigo


for _c in MENSAJES_ERROR:
    app.register_error_handler(_c, _pagina_error)


if __name__ == "__main__":
    app.run(debug=DEBUG, use_reloader=False)
