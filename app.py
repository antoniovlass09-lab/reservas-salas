import csv
import io
import os
import re
import secrets
import itertools
import time
import unicodedata
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

horarios_academicos = bd.db["horarios_academicos"]
clases_canceladas = bd.db["clases_canceladas"]  # clases del horario canceladas por día
asistencias = bd.db["asistencias"]  # ¿el docente estuvo en la sala? (punto verde / rojo)

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
MIN_ANTICIPACION = 10  # minutos: el docente reserva o cancela el mismo día hasta 10 min antes
VENTANA_ASISTENCIA = 30  # minutos antes del inicio desde los que el docente puede marcar que ya está

try:
    bd.conectar()
    bd.inicializar(sembrar_demo=os.environ.get("SEED_DEMO") == "1")
except RuntimeError as e:
    print(f"ERROR: {e}")
    raise SystemExit(1)

try:
    clases_canceladas.create_index([("horario_id", 1), ("fecha", 1)], unique=True)
except Exception as e:  # no impide arrancar; la cancelación igual valida duplicados
    print(f"AVISO: no se pudo crear el índice de clases canceladas: {e}")


try:
    asistencias.create_index([("ref", 1), ("fecha", 1)], unique=True)
except Exception as e:
    print(f"AVISO: no se pudo crear el índice de asistencias: {e}")

# Si a alguna sala le faltan bloques de horario en la base (por ejemplo LR no se cargó en
# Atlas), se agregan al arrancar. Nunca modifica lo que ya existe.
try:
    import cargar_horarios
    cargar_horarios.sincronizar()
except Exception as e:  # no impide arrancar
    print(f"AVISO: no se pudo revisar el horario académico: {e}")


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


def ahora_dt():
    """Fecha y hora actuales del plantel (no la del servidor, que suele estar en UTC)."""
    return bd.ahora()


def fecha_hoy():
    return ahora_dt().date()


def ahora_hhmm():
    return ahora_dt().strftime("%H:%M")


def minutos(h):
    return int(h[:2]) * 60 + int(h[3:])


def _n(d):
    """Agrega 'id' (texto) a un documento de Mongo para usarlo en plantillas."""
    if d is not None:
        d["id"] = str(d["_id"])
    return d


def grupo_clase(h, m=None):
    """Grupo de una clase del horario. Si la clase no tiene uno propio, usa el que tuviera la materia."""
    g = h.get("grupo")
    return g if g is not None else ((m or {}).get("grupo") or "")


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
    if not sala:
        return False
    clases = list(horarios_academicos.find({
        "sala": sala["nombre"], "dia": dia, "estado": "ocupada",
        "hora_inicio": {"$lt": fin}, "hora_fin": {"$gt": ini}}))
    if not clases:
        return False
    # Una clase cancelada ese día deja la hora disponible.
    canceladas = {c["horario_id"] for c in clases_canceladas.find(
        {"fecha": fecha, "horario_id": {"$in": [h["_id"] for h in clases]}})}
    return any(h["_id"] not in canceladas for h in clases)


def validar_horario(fecha_txt, ini, fin, anticipacion=60, rol="docente"):
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
    hoy = fecha_hoy()
    if dia < hoy:
        return None, "No puedes usar una fecha pasada."
    if anticipacion and dia > hoy + timedelta(days=anticipacion):
        return None, f"Reserva con hasta {anticipacion} días de anticipación."
    if dia == hoy:
        ahora_m = minutos(ahora_hhmm())
        if rol == "encargado":
            # El encargado no tiene límite de hora: puede usar lo que quede de la hora.
            if minutos(fin) <= ahora_m:
                return None, "Esa hora ya terminó."
        elif minutos(ini) - ahora_m < MIN_ANTICIPACION:
            return None, (f"Para reservar el mismo día debes hacerlo al menos "
                          f"{MIN_ANTICIPACION} minutos antes de la hora de inicio.")
    return dia, None


_ultima_limpieza = 0.0


def finalizar_vencidas():
    """Marca como Finalizada/Vencida lo que ya pasó (máx. una vez por minuto)."""
    global _ultima_limpieza
    if time.time() - _ultima_limpieza < 60:
        return
    _ultima_limpieza = time.time()
    hoy, ahora = fecha_hoy().isoformat(), ahora_hhmm()
    paso = {"$or": [{"fecha": {"$lt": hoy}},
                    {"fecha": hoy, "hora_fin": {"$lte": ahora}}]}
    reservaciones.update_many({"estado": "Aprobada", **paso},
                              {"$set": {"estado": "Finalizada"}})
    reservaciones.update_many({"estado": "Pendiente", **paso},
                              {"$set": {"estado": "Vencida",
                                        "motivo_rechazo": "No fue atendida a tiempo"}})


# ------------------------------------------------------------------ cancelaciones y permisos
def _tokens(texto):
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return set(re.findall(r"[a-z0-9]+", t))


def mismo_docente(a, b):
    """True si dos nombres parecen ser la misma persona (sin acentos ni orden)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    corto, largo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(corto) >= 2 and corto <= largo


def es_su_clase(h):
    """¿La clase del horario es del docente que tiene la sesión abierta?"""
    if h.get("docente_usuario_id") and h["docente_usuario_id"] == uid():
        return True
    return mismo_docente(session.get("nombre"), h.get("docente"))


def usuario_de_clase(h):
    """Cuenta de docente que corresponde al docente de una clase (si existe)."""
    if h.get("docente_usuario_id"):
        u = usuarios.find_one({"_id": h["docente_usuario_id"]})
        if u:
            return u
    for u in usuarios.find({"rol": "docente"}):
        if mismo_docente(u.get("nombre"), h.get("docente")):
            return u
    return None


def inicio_permitido_hoy(slot):
    """¿Se puede reservar hoy un bloque que inicia a la hora `slot`?"""
    ahora_m = minutos(ahora_hhmm())
    if session.get("rol") == "encargado":
        return minutos(slot) + 60 > ahora_m  # puede usar lo que quede de la hora
    return minutos(slot) - ahora_m >= MIN_ANTICIPACION


def puede_cancelar_clase(h, fecha):
    """(permitido, aviso) para cancelar una clase del horario en `fecha`."""
    hoy_iso = fecha_hoy().isoformat()
    if fecha < hoy_iso:
        return False, "Los días anteriores ya no se pueden modificar."
    if session.get("rol") == "encargado":
        return True, ""
    if not es_su_clase(h):
        return False, ""
    if fecha != hoy_iso:
        return False, "Solo puedes cancelar tu clase el mismo día en que se imparte."
    if minutos(h["hora_inicio"]) - minutos(ahora_hhmm()) < MIN_ANTICIPACION:
        return False, (f"Ya no puedes cancelar: el plazo termina {MIN_ANTICIPACION} "
                       "minutos antes de la clase.")
    return True, ""


def puede_cancelar_reserva(r):
    """(permitido, aviso) para cancelar una reservación."""
    hoy_iso = fecha_hoy().isoformat()
    if r["fecha"] < hoy_iso:
        return False, "Los días anteriores ya no se pueden modificar."
    if session.get("rol") == "encargado":
        return True, ""
    if r["usuario_id"] != uid():
        return False, ""
    if r["fecha"] == hoy_iso and minutos(r["hora_inicio"]) - minutos(ahora_hhmm()) < MIN_ANTICIPACION:
        return False, (f"Ya no puedes cancelar: el plazo termina {MIN_ANTICIPACION} "
                       "minutos antes de la hora de inicio.")
    return True, ""


def puede_marcar_asistencia(tipo, doc, fecha):
    """(permitido, aviso) para registrar si el docente está en la sala.

    Solo el mismo día. El encargado puede registrar a cualquiera durante todo el día; el
    docente solo lo suyo, desde 30 min antes de que inicie y hasta que termina."""
    if fecha != fecha_hoy().isoformat():
        return False, ""
    es_enc = session.get("rol") == "encargado"
    propio = es_su_clase(doc) if tipo == "clase" else doc["usuario_id"] == uid()
    if not (es_enc or propio):
        return False, ""
    if not es_enc:
        ahora_m = minutos(ahora_hhmm())
        if ahora_m < minutos(doc["hora_inicio"]) - VENTANA_ASISTENCIA:
            return False, (f"Podrás marcar que estás en la sala desde {VENTANA_ASISTENCIA} "
                           "minutos antes de que inicie.")
        if ahora_m >= minutos(doc["hora_fin"]):
            return False, "Ya terminó; solo el encargado puede cambiar el registro."
    return True, ""


def datos_asistencia(a, permitido, aviso):
    """Campos de asistencia que el calendario usa para pintar el punto y los botones."""
    modo = ""
    if permitido:
        modo = "encargado" if session.get("rol") == "encargado" else "docente"
    return {"asist": a["estado"] if a else "",
            "asist_por": a.get("marcado_por", "") if a else "",
            "asist_hora": a.get("hora", "") if a else "",
            "marcar": bool(permitido), "modo": modo, "aviso_marcar": aviso}


def _destino(defecto):
    """Página a la que volver tras una acción (solo rutas internas del sitio)."""
    n = request.form.get("next", "")
    return n if n.startswith("/") and not n.startswith("//") else defecto


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
        ref = fecha_hoy()

    if abs((ref - fecha_hoy()).days) > 800:
        ref = fecha_hoy()

    hoy_iso = fecha_hoy().isoformat()
    es_enc = session.get("rol") == "encargado"
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

    def marcar(f, ini, fin, texto, clase, detalle=None):
        primero = True

        for s in SLOTS:
            # El bloque se pinta si la reservación o clase se traslapa con él.
            if minutos(ini) < minutos(s) + 60 and minutos(s) < minutos(fin):
                celdas[(f, s)] = (texto, clase, primero, detalle)
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
    horarios = lista(horarios_academicos.find({
        "sala": sala["nombre"],
        "dia": {"$in": dias_semana},
        "estado": "ocupada"
    }))

    # Clases canceladas esta semana en la sala.
    canceladas = {(c["horario_id"], c["fecha"]): c for c in clases_canceladas.find(
        {"sala": sala["nombre"], "fecha": rango})}

    # Asistencia registrada esta semana en la sala (punto verde / rojo).
    asist = {(a["ref"], a["fecha"]): a for a in asistencias.find(
        {"sala": sala["nombre"], "fecha": rango})}

    # Obtener los nombres oficiales de las materias.
    catalogo = lista(bd.db["materias"].find({}))

    nombres_materias = {
        m["abreviatura"]: m.get("nombre") or m["abreviatura"]
        for m in catalogo
    }
    grupos_materias = {m["abreviatura"]: m.get("grupo") for m in catalogo}
    claves_materias = {m["abreviatura"]: m.get("clave") for m in catalogo}

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

        nombre_base = nombre  # en la celda solo se ve la materia
        grupo = grupo_clase(h, {"grupo": grupos_materias.get(abreviatura)})

        # Docente asignado: no se muestra en la celda, solo al hacer clic (detalle).
        docente_clase = h.get("docente") or h.get("docente_corto")

        clase_color = colores_materias.get(abreviatura) or (
            f"materia-x{zlib.crc32(abreviatura.encode()) % 8}"
            if abreviatura else "materia-default"
        )

        canc = canceladas.get((h["_id"], fecha))
        puede, aviso = puede_cancelar_clase(h, fecha)
        reservable = fecha > hoy_iso or (fecha == hoy_iso and inicio_permitido_hoy(h["hora_inicio"]))

        # Datos que se muestran al tocar la clase en el calendario.
        detalle = {
            "tipo": "clase",
            "materia": nombre_base,
            "docente": docente_clase or "",
            "clave": claves_materias.get(abreviatura) or "",
            "grupo": grupo or "",
            "horario": f'{dia} {fecha[8:10]}/{fecha[5:7]}/{fecha[:4]} · {h["hora_inicio"]} a {h["hora_fin"]}',
            "fecha": fecha,
            "hora": h["hora_inicio"],
            "hid": h["id"],
            "estado": "Cancelada" if canc else "",
            "cancelada": bool(canc),
            "motivo": canc["motivo"] if canc else "",
            "por": canc.get("cancelada_por", "") if canc else "",
            "cid": str(canc["_id"]) if canc else "",
            "cancelar": bool(puede and not canc),
            "aviso": "" if canc else aviso,
            "restablecer": bool(canc and es_enc and fecha >= hoy_iso),
            "reservable": bool(canc and reservable),
        }
        ok_m, av_m = (False, "") if canc else puede_marcar_asistencia("clase", h, fecha)
        detalle.update(datos_asistencia(
            None if canc else asist.get((f"c:{h['_id']}", fecha)), ok_m, av_m))

        if canc:
            nombre = f"Cancelada · {nombre}"
            clase_color = "cancelada"

        marcar(
            fecha,
            h["hora_inicio"],
            h["hora_fin"],
            nombre,
            clase_color,
            detalle
        )

    # Dibujar las reservaciones encima de las clases.
    for r in sorted(reservas, key=lambda r: r["estado"] == "Aprobada"):
        puede, aviso = puede_cancelar_reserva(r)
        dia_r = nombres_dias[datetime.strptime(r["fecha"], "%Y-%m-%d").weekday()]
        ok_m, av_m = (False, "")
        if r["estado"] == "Aprobada":
            ok_m, av_m = puede_marcar_asistencia("reserva", r, r["fecha"])
        det_r = {
            "tipo": "reserva",
            "materia": r["materia"],
            "docente": r["docente"],
            "clave": "",
            "grupo": "",
            "horario": f'{dia_r} {r["fecha"][8:10]}/{r["fecha"][5:7]}/{r["fecha"][:4]} · {r["hora_inicio"]} a {r["hora_fin"]}',
            "fecha": r["fecha"],
            "hora": r["hora_inicio"],
            "rid": r["id"],
            "estado": "Aprobada" if r["estado"] == "Aprobada" else "En espera",
            "cancelada": False,
            "motivo": r.get("motivo") or "",
            "por": "",
            "cancelar": puede,
            "aviso": aviso,
            "restablecer": False,
            "reservable": False,
        }
        det_r.update(datos_asistencia(asist.get((f"r:{r['_id']}", r["fecha"])), ok_m, av_m))
        marcar(
            r["fecha"],
            r["hora_inicio"],
            r["hora_fin"],
            r["materia"],
            "aprobada" if r["estado"] == "Aprobada" else "pendiente",
            det_r
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
        hoy=hoy_iso,
        ahora=ahora_hhmm(),
        slots_hoy=[s for s in SLOTS if inicio_permitido_hoy(s)],
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
                               hoy=fecha_hoy(), sala_sel=sala_sel, form=form)

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

        es_enc = session.get("rol") == "encargado"
        dia, err = validar_horario(f.get("fecha"), ini, fin, rol=session.get("rol"))

        # Encargado, mismo día: si la hora ya empezó, la reservación se ajusta a lo
        # que queda de la hora (empieza ahora y termina a la hora indicada).
        ajustada = False
        if err is None and es_enc and dia == fecha_hoy() and ini < ahora_hhmm():
            ini = ahora_hhmm()
            ajustada = True

        if err is None:
            if not sala:
                err = "Selecciona una sala válida."

            elif restringido and not catalogo:
                err = "No tienes materias activas asignadas. Contacta al encargado."

            elif not materia or not motivo or len(materia) > 100 or len(motivo) > 500:
                err = "Materia (máx. 100) y motivo (máx. 500) son obligatorios. Si elegiste «Otra» u «Otro», escríbelos."

            elif catalogo and not otra_materia and materia not in catalogo:
                err = "Selecciona una materia de la lista."

            elif not ajustada and minutos(fin) - minutos(ini) != 60:
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
            # Las reservaciones del encargado no necesitan aprobación.
            "estado": "Aprobada" if es_enc else "Pendiente",
            "motivo_rechazo": None,
            "creada": ahora_texto()
        })

        if es_enc:
            flash(f"Reservación creada y aprobada ({ini}–{fin}).", "ok")
            return redirect(url_for("calendario", sala=f.get("sala")))

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
    return render_template("mis.html", rs=mis, hoy=fecha_hoy().isoformat())


@app.route("/cancelar/<rid>", methods=["POST"])
@login_required()
def cancelar(rid):
    es_enc = session.get("rol") == "encargado"
    destino = _destino(url_for("panel") if es_enc else url_for("mis_reservaciones"))
    q = {"_id": oid(rid), "estado": {"$in": ["Pendiente", "Aprobada"]}}
    if not es_enc:
        q["usuario_id"] = uid()
    r = reservaciones.find_one(q)
    if not r:
        flash("No se puede cancelar esa reservación.", "error")
        return redirect(destino)
    ok, aviso = puede_cancelar_reserva(r)
    if not ok:
        flash(aviso or "No se puede cancelar esa reservación.", "error")
        return redirect(destino)
    motivo = request.form.get("motivo", "").strip()[:200]
    if es_enc and not motivo:
        flash("Escribe el motivo de la cancelación.", "error")
        return redirect(destino)
    motivo = motivo or "Cancelada por el docente"
    reservaciones.update_one({"_id": r["_id"]},
                             {"$set": {"estado": "Cancelada", "motivo_rechazo": motivo,
                                       "cancelada_por": session["nombre"],
                                       "cancelada_en": ahora_texto()}})
    cuando = f'{r["fecha"]} ({r["hora_inicio"]}-{r["hora_fin"]})'
    if es_enc:
        if r["usuario_id"] != uid():
            notificar(r["usuario_id"], f"⚠️ El encargado canceló tu reservación del {cuando}. Motivo: {motivo}")
    elif r["estado"] == "Aprobada":
        notificar_encargados(f'{session["nombre"]} canceló su reservación del {cuando}.')
    flash("Reservación cancelada.", "ok")
    return redirect(destino)


@app.route("/clase/cancelar", methods=["POST"])
@login_required()
def cancelar_clase():
    """Cancela una clase del horario académico solo para un día concreto."""
    f = request.form
    destino = _destino(url_for("calendario"))
    fecha = f.get("fecha", "")
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        flash("Fecha inválida.", "error")
        return redirect(destino)
    h = horarios_academicos.find_one({"_id": oid(f.get("horario_id")), "estado": "ocupada"})
    if not h or h["dia"] != NOMBRES_DIAS[d.weekday()]:
        flash("No se encontró esa clase.", "error")
        return redirect(destino)
    ok, aviso = puede_cancelar_clase(h, fecha)
    if not ok:
        flash(aviso or "No tienes permiso para cancelar esa clase.", "error")
        return redirect(destino)
    motivo = f.get("motivo", "").strip()[:200]
    if not motivo:
        flash("Escribe el motivo de la cancelación.", "error")
        return redirect(destino)

    m = materias.find_one({"abreviatura": h.get("materia_abreviatura")}) or {}
    materia = m.get("nombre") or h.get("materia_abreviatura") or "Materia pendiente"
    try:
        clases_canceladas.insert_one({
            "horario_id": h["_id"], "sala": h["sala"], "fecha": fecha, "dia": h["dia"],
            "hora_inicio": h["hora_inicio"], "hora_fin": h["hora_fin"],
            "materia": materia, "materia_abreviatura": h.get("materia_abreviatura"),
            "docente": h.get("docente") or h.get("docente_corto") or "",
            "motivo": motivo, "cancelada_por": session["nombre"],
            "cancelada_por_rol": session.get("rol"), "creada": ahora_texto()})
    except DuplicateKeyError:
        flash("Esa clase ya estaba cancelada.", "error")
        return redirect(destino)

    cuando = f'{h["sala"]}, {fecha} ({h["hora_inicio"]}-{h["hora_fin"]})'
    if session.get("rol") == "encargado":
        u = usuario_de_clase(h)
        if u:
            notificar(u["_id"], f"⚠️ El encargado canceló tu clase de {materia} · {cuando}. Motivo: {motivo}")
    else:
        notificar_encargados(f'{session["nombre"]} canceló su clase de {materia} · {cuando}. Motivo: {motivo}')
    flash("Clase cancelada. Ese horario quedó disponible para reservar solo por ese día.", "ok")
    return redirect(destino)


@app.route("/clase/restablecer", methods=["POST"])
@login_required("encargado")
def restablecer_clase():
    """Deshace la cancelación de una clase (por si se canceló por error)."""
    destino = _destino(url_for("calendario"))
    c = clases_canceladas.find_one({"_id": oid(request.form.get("cancelacion_id"))})
    if not c:
        flash("No se encontró esa cancelación.", "error")
        return redirect(destino)
    if c["fecha"] < fecha_hoy().isoformat():
        flash("Los días anteriores ya no se pueden modificar.", "error")
        return redirect(destino)
    sala = salas.find_one({"nombre": c["sala"]})
    if sala and (reservaciones.find_one({
            "sala_id": sala["_id"], "fecha": c["fecha"],
            "estado": {"$in": ["Aprobada", "Pendiente"]},
            "hora_inicio": {"$lt": c["hora_fin"]}, "hora_fin": {"$gt": c["hora_inicio"]}})):
        flash("Ya hay una reservación en ese horario. Cancélala primero para restablecer la clase.", "error")
        return redirect(destino)
    clases_canceladas.delete_one({"_id": c["_id"]})
    flash("Clase restablecida.", "ok")
    return redirect(destino)


# ------------------------------------------------------------------ asistencia y estado en vivo
@app.route("/asistencia", methods=["POST"])
@login_required()
def registrar_asistencia():
    """Registra si el docente está (punto verde) o no está (punto rojo) en la sala."""
    f = request.form
    destino = _destino(url_for("calendario"))
    tipo, fecha, estado = f.get("tipo", ""), f.get("fecha", ""), f.get("estado", "")
    if estado not in ("presente", "ausente", "sin_registro"):
        flash("Opción inválida.", "error")
        return redirect(destino)
    if estado == "sin_registro" and session.get("rol") != "encargado":
        flash("Solo el encargado puede quitar un registro.", "error")
        return redirect(destino)

    if tipo == "clase":
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError:
            flash("Fecha inválida.", "error")
            return redirect(destino)
        doc = horarios_academicos.find_one({"_id": oid(f.get("id")), "estado": "ocupada"})
        if not doc or doc["dia"] != NOMBRES_DIAS[d.weekday()]:
            flash("No se encontró esa clase.", "error")
            return redirect(destino)
        if clases_canceladas.find_one({"horario_id": doc["_id"], "fecha": fecha}):
            flash("Esa clase está cancelada; no se puede registrar asistencia.", "error")
            return redirect(destino)
        m = materias.find_one({"abreviatura": doc.get("materia_abreviatura")}) or {}
        sala_nombre = doc["sala"]
        materia = m.get("nombre") or doc.get("materia_abreviatura") or "Materia pendiente"
        docente = doc.get("docente") or doc.get("docente_corto") or ""
        ref = f"c:{doc['_id']}"
    elif tipo == "reserva":
        doc = reservaciones.find_one({"_id": oid(f.get("id")),
                                      "estado": {"$in": ["Aprobada", "Finalizada"]}})
        if not doc or doc["fecha"] != fecha:
            flash("No se encontró esa reservación.", "error")
            return redirect(destino)
        s = salas.find_one({"_id": doc["sala_id"]}) or {}
        u = usuarios.find_one({"_id": doc["usuario_id"]}) or {}
        sala_nombre = s.get("nombre", "")
        materia, docente = doc["materia"], u.get("nombre", "")
        ref = f"r:{doc['_id']}"
    else:
        flash("No se encontró lo que quieres registrar.", "error")
        return redirect(destino)

    ok, aviso = puede_marcar_asistencia(tipo, doc, fecha)
    if not ok:
        flash(aviso or "No tienes permiso para registrar esto.", "error")
        return redirect(destino)

    if estado == "sin_registro":
        asistencias.delete_one({"ref": ref, "fecha": fecha})
        flash("Registro quitado.", "ok")
        return redirect(destino)

    asistencias.update_one({"ref": ref, "fecha": fecha}, {"$set": {
        "ref": ref, "fecha": fecha, "tipo": tipo, "sala": sala_nombre,
        "hora_inicio": doc["hora_inicio"], "hora_fin": doc["hora_fin"],
        "materia": materia, "docente": docente, "estado": estado,
        "marcado_por": session["nombre"], "marcado_por_rol": session.get("rol"),
        "marcado_en": ahora_texto(), "hora": ahora_hhmm()}}, upsert=True)
    flash("🟢 Registrado: está en la sala." if estado == "presente"
          else "🔴 Registrado: no está en la sala.", "ok")
    return redirect(destino)


def estado_salas_ahora():
    """Qué pasa ahora mismo en cada sala activa: clase o reservación en curso, quién
    está (o no) en la sala y qué sigue. Alimenta la pantalla «En vivo»."""
    hoy = fecha_hoy()
    hoy_iso, ahora_ = hoy.isoformat(), ahora_hhmm()
    ahora_m = minutos(ahora_)
    dia = NOMBRES_DIAS[hoy.weekday()]
    activas = lista(salas.find({"activa": True}).sort("nombre", 1))
    ids = [s["_id"] for s in activas]
    nombres_sala = [s["nombre"] for s in activas]
    catalogo = {m["abreviatura"]: m for m in materias.find({})}
    canc = {c["horario_id"]: c for c in clases_canceladas.find({"fecha": hoy_iso})}
    asist = {a["ref"]: a for a in asistencias.find({"fecha": hoy_iso})}
    por_sala = {n: [] for n in nombres_sala}

    def agregar(sala, tipo, doc, materia, grupo, docente, cancelada, motivo=""):
        ref = f"{'c' if tipo == 'clase' else 'r'}:{doc['_id']}"
        ok, aviso = (False, "") if cancelada else puede_marcar_asistencia(tipo, doc, hoy_iso)
        a = asist.get(ref)
        por_sala[sala].append({
            "tipo": tipo, "id": str(doc["_id"]), "materia": materia, "grupo": grupo or "",
            "docente": docente, "inicio": doc["hora_inicio"], "fin": doc["hora_fin"],
            "cancelada": cancelada, "motivo": motivo,
            "asist": a["estado"] if a else "",
            "asist_por": a.get("marcado_por", "") if a else "",
            "asist_hora": a.get("hora", "") if a else "",
            "marcar": ok, "aviso": aviso,
            "modo": ("encargado" if session.get("rol") == "encargado" else "docente") if ok else ""})

    for h in horarios_academicos.find({"sala": {"$in": nombres_sala}, "dia": dia,
                                       "estado": "ocupada"}):
        m = catalogo.get(h.get("materia_abreviatura")) or {}
        c = canc.get(h["_id"])
        agregar(h["sala"], "clase", h,
                m.get("nombre") or h.get("materia_abreviatura") or "Materia pendiente",
                grupo_clase(h, m), h.get("docente") or h.get("docente_corto") or "Sin asignar",
                bool(c), c.get("motivo", "") if c else "")
    nombre_de = {s["_id"]: s["nombre"] for s in activas}
    for r in enriquecer(lista(reservaciones.find({
            "sala_id": {"$in": ids}, "fecha": hoy_iso,
            "estado": {"$in": ["Aprobada", "Finalizada"]}}))):
        agregar(nombre_de[r["sala_id"]], "reserva", r, r["materia"], "", r["docente"], False)
    bloq = list(bloqueos.find({"sala_id": {"$in": ids}, "fecha": hoy_iso}))

    vivas = []
    tot = {"ocupadas": 0, "libres": 0, "mantenimiento": 0, "presentes": 0,
           "ausentes": 0, "sin_registro": 0}
    for s in activas:
        items = sorted(por_sala[s["nombre"]], key=lambda i: (i["inicio"], i["fin"]))
        for i in items:
            i["fase"] = ("En curso" if i["inicio"] <= ahora_ < i["fin"] else
                         "Por comenzar" if 0 < minutos(i["inicio"]) - ahora_m <= VENTANA_ASISTENCIA
                         else "")
        mostrar = [i for i in items if i["fase"]]
        en_curso = [i for i in mostrar if i["fase"] == "En curso" and not i["cancelada"]]
        siguiente = next((i for i in items if not i["fase"] and not i["cancelada"]
                          and i["inicio"] > ahora_), None)
        mant = next((b for b in bloq if b["sala_id"] == s["_id"]
                     and b["hora_inicio"] <= ahora_ < b["hora_fin"]), None)
        if mant:
            clase, etiqueta = "mant", "Mantenimiento"
            tot["mantenimiento"] += 1
        elif en_curso:
            clase, etiqueta = "ocupada", "Ocupada"
            tot["ocupadas"] += 1
        else:
            clase, etiqueta = "libre", "Libre"
            tot["libres"] += 1
        for i in en_curso:
            tot["presentes" if i["asist"] == "presente" else
                "ausentes" if i["asist"] == "ausente" else "sin_registro"] += 1
        vivas.append({"nombre": s["nombre"], "clase": clase, "etiqueta": etiqueta,
                      "motivo_mant": (mant.get("motivo") or "") if mant else "",
                      "actuales": mostrar, "siguiente": siguiente})
    return {"salas_vivas": vivas, "tot": tot, "ahora": ahora_dt().strftime("%H:%M:%S"),
            "hoy_iso": hoy_iso, "hoy_txt": f"{dia} {hoy:%d/%m/%Y}"}


@app.route("/en-vivo")
@login_required()
def en_vivo():
    datos = estado_salas_ahora()
    if request.args.get("parcial") == "1":
        return render_template("_vivo.html", **datos)
    return render_template("en_vivo.html", **datos)


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
    _, err = validar_horario(a.get("fecha"), a.get("inicio", ""), a.get("fin", ""),
                             rol=session.get("rol"))
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
    hoy = fecha_hoy().isoformat()
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
    # Pestaña Horarios: cuadrícula editable de la sala elegida.
    sala_h = next((s for s in todas if s["id"] == request.args.get("sala_h")),
                  todas[0] if todas else None)
    grid = {}
    if sala_h:
        por_abrev = {m["abreviatura"]: m for m in catalogo}
        for h in horarios_academicos.find({"sala": sala_h["nombre"], "estado": "ocupada"}):
            grid[(h["dia"], h["hora_inicio"])] = (
                h.get("materia_abreviatura") or "",
                h.get("docente") or h.get("docente_corto") or "",
                grupo_clase(h, por_abrev.get(h.get("materia_abreviatura"))))
    return render_template("panel.html", pend=pend, apro=apro, salas=todas, catalogo=catalogo,
                           sala_h=sala_h, grid=grid, dias_h=NOMBRES_DIAS[:5], slots_h=SLOTS,
                           materias_lista=materias_activas(),
                           activas=activas, bloq=bloq, uso=uso, docentes=docentes,
                           hoy=fecha_hoy())


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
    _, err = validar_horario(f.get("fecha"), ini, fin, anticipacion=None, rol="encargado")
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
    return redirect(url_for("panel", _anchor="t5"))


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
    return redirect(url_for("panel", _anchor="t5"))


# ------------------------------------------------------------------ materias (encargado)
@app.route("/materia/nueva", methods=["POST"])
@login_required("encargado")
def nueva_materia():
    f = request.form
    nombre = f.get("nombre", "").strip()[:100]
    abrev = (f.get("abreviatura", "").strip() or nombre)[:40]
    clave = f.get("clave", "").strip()[:20] or None
    if not nombre:
        flash("Escribe el nombre de la materia.", "error")
    elif materias.find_one({"$or": [{"abreviatura": abrev}, {"nombre": nombre}]}):
        flash("Ya existe una materia con ese nombre o abreviatura.", "error")
    else:
        try:
            materias.insert_one({"abreviatura": abrev, "nombre": nombre, "clave": clave,
                                 "activa": True,
                                 "actualizado_en": datetime.now(timezone.utc)})
            flash("Materia agregada.", "ok")
        except DuplicateKeyError:
            flash("Ya existe una materia con esa abreviatura.", "error")
    return redirect(url_for("panel", _anchor="t7"))


@app.route("/materia/<mid>", methods=["POST"])
@login_required("encargado")
def editar_materia(mid):
    m = materias.find_one({"_id": oid(mid)})
    if not m:
        abort(404)
    nombre = request.form.get("nombre", "").strip()[:100] or None
    clave = request.form.get("clave", "").strip()[:20] or None
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
                            {"$set": {"nombre": nombre, "clave": clave, "activa": activa,
                                      "actualizado_en": datetime.now(timezone.utc)}})
        flash(f"Materia {m['abreviatura']} actualizada.", "ok")
    return redirect(url_for("panel", _anchor="t7"))


# ------------------------------------------------------------------ reportes (encargado)
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


TEXTO_ASIS = {"presente": "Presente", "ausente": "Ausente", "sin_registro": "Sin registro"}


def _csv_seguro(v):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def eventos_mes(ini, sig, salas_sel):
    """Todo lo que pasó (o está programado) en el mes: clases del horario, clases
    canceladas, reservaciones (aprobadas, realizadas y canceladas) y bloqueos."""
    hoy_iso, ahora_ = fecha_hoy().isoformat(), ahora_hhmm()
    nombre_sala = {s["_id"]: s["nombre"] for s in salas_sel}
    nombres_salas = list(nombre_sala.values())
    desde, hasta = ini.isoformat(), sig.isoformat()
    catalogo = {m["abreviatura"]: m for m in materias.find({})}
    asist_mes = {(a["ref"], a["fecha"]): a for a in asistencias.find(
        {"fecha": {"$gte": desde, "$lt": hasta}})}
    # «Sin registro» solo cuenta desde el primer día en que se usó el registro de asistencia;
    # los días anteriores no se marcan como faltas de registro (aún no existía).
    primero = next(iter(asistencias.find({}).sort("fecha", 1).limit(1)), None)
    desde_asist = primero["fecha"] if primero else None
    ev = []

    def evento(fecha, ini_h, fin_h, sala, tipo, materia, grupo, docente, estado,
               detalle="", por="", horas=0.0, ref=None):
        # Asistencia: presente / ausente (lo que se marcó), sin_registro (ya pasó y nadie
        # marcó nada) o vacío (cancelada, todavía no ocurre o no aplica).
        a = asist_mes.get((ref, fecha)) if ref else None
        if estado == "Cancelada" or tipo == "Mantenimiento":
            asis = ""
        elif a:
            asis = a["estado"]
        elif estado in ("Impartida", "Realizada") and desde_asist and fecha >= desde_asist:
            asis = "sin_registro"
        else:
            asis = ""
        ev.append({"fecha": fecha, "dia": NOMBRES_DIAS[datetime.strptime(fecha, "%Y-%m-%d").weekday()],
                   "inicio": ini_h, "fin": fin_h, "sala": sala, "tipo": tipo,
                   "materia": materia, "grupo": grupo, "docente": docente, "estado": estado,
                   "detalle": detalle, "por": por, "horas": horas, "asis": asis,
                   "asis_por": (a.get("marcado_por", "") if a and asis else ""),
                   "asis_hora": (a.get("hora", "") if a and asis else "")})

    # Clases del horario académico, día por día.
    horarios = list(horarios_academicos.find({"sala": {"$in": nombres_salas}, "estado": "ocupada"}))
    canc = {(c["horario_id"], c["fecha"]): c for c in clases_canceladas.find(
        {"fecha": {"$gte": desde, "$lt": hasta}})}
    d = ini
    while d < sig:
        iso, dia = d.isoformat(), NOMBRES_DIAS[d.weekday()]
        for h in horarios:
            if h["dia"] != dia:
                continue
            m = catalogo.get(h.get("materia_abreviatura")) or {}
            nombre = m.get("nombre") or h.get("materia_abreviatura") or "Materia pendiente"
            docente = h.get("docente") or h.get("docente_corto") or "Sin asignar"
            c = canc.get((h["_id"], iso))
            if c:
                estado, detalle, por = "Cancelada", c.get("motivo", ""), c.get("cancelada_por", "")
            elif iso < hoy_iso or (iso == hoy_iso and h["hora_fin"] <= ahora_):
                estado, detalle, por = "Impartida", "", ""
            else:
                estado, detalle, por = "Programada", "", ""
            evento(iso, h["hora_inicio"], h["hora_fin"], h["sala"], "Clase", nombre,
                   grupo_clase(h, m), docente, estado, detalle, por,
                   ref=f"c:{h['_id']}")
        d += timedelta(days=1)

    # Reservaciones de docentes (aprobadas, realizadas y canceladas).
    rs = enriquecer(lista(reservaciones.find({
        "sala_id": {"$in": list(nombre_sala)},
        "fecha": {"$gte": desde, "$lt": hasta},
        "estado": {"$in": ["Aprobada", "Finalizada", "Cancelada"]}})))
    for r in rs:
        horas = (minutos(r["hora_fin"]) - minutos(r["hora_inicio"])) / 60
        if r["estado"] == "Cancelada":
            estado, detalle, por = "Cancelada", r.get("motivo_rechazo") or "", r.get("cancelada_por", "")
        else:
            estado = "Realizada" if r["estado"] == "Finalizada" else "Aprobada"
            detalle, por = r.get("motivo") or "", ""
        evento(r["fecha"], r["hora_inicio"], r["hora_fin"], r["sala"], "Reservación",
               r["materia"], "", r["docente"], estado, detalle, por,
               0.0 if estado == "Cancelada" else horas, ref=f"r:{r['_id']}")

    # Bloqueos por mantenimiento.
    for b in bloqueos.find({"sala_id": {"$in": list(nombre_sala)},
                            "fecha": {"$gte": desde, "$lt": hasta}}):
        evento(b["fecha"], b["hora_inicio"], b["hora_fin"], nombre_sala[b["sala_id"]],
               "Mantenimiento", "—", "", "—", "Bloqueada", b.get("motivo") or "")

    ev.sort(key=lambda e: (e["fecha"], e["inicio"], e["sala"].lower()))
    return ev


def resumen_mes(eventos):
    """Totales por sala y por materia/docente a partir de los eventos del mes."""
    por_sala = {}
    for e in eventos:
        if e["tipo"] == "Mantenimiento":
            continue
        sl = por_sala.setdefault(e["sala"], {
            "nombre": e["sala"], "programadas": 0, "impartidas": 0, "canceladas": 0,
            "reservas": 0, "reservas_canc": 0, "horas": 0.0, "presentes": 0,
            "ausentes": 0, "sin_registro": 0, "detalle": {}})
        dt = sl["detalle"].setdefault((e["materia"], e["grupo"], e["docente"]), {
            "materia": e["materia"], "grupo": e["grupo"], "docente": e["docente"], "programadas": 0,
            "impartidas": 0, "canceladas": 0, "reservas": 0, "presentes": 0, "ausentes": 0})
        if e["tipo"] == "Clase":
            sl["programadas"] += 1
            dt["programadas"] += 1
            if e["estado"] == "Impartida":
                sl["impartidas"] += 1
                dt["impartidas"] += 1
            elif e["estado"] == "Cancelada":
                sl["canceladas"] += 1
                dt["canceladas"] += 1
        elif e["estado"] == "Cancelada":
            sl["reservas_canc"] += 1
        else:
            sl["reservas"] += 1
            sl["horas"] += e["horas"]
            dt["reservas"] += 1
        if e["asis"] == "presente":
            sl["presentes"] += 1
            dt["presentes"] += 1
        elif e["asis"] == "ausente":
            sl["ausentes"] += 1
            dt["ausentes"] += 1
        elif e["asis"] == "sin_registro":
            sl["sin_registro"] += 1
    resumen = []
    for nombre in sorted(por_sala, key=str.lower):
        sl = por_sala[nombre]
        sl["detalle"] = sorted(sl["detalle"].values(),
                               key=lambda x: (x["materia"].lower(), x["grupo"].lower(), x["docente"].lower()))
        resumen.append(sl)
    return resumen


@app.route("/reportes")
@login_required("encargado")
def reportes():
    try:
        ini = datetime.strptime((request.args.get("mes") or "") + "-01", "%Y-%m-%d").date()
    except ValueError:
        ini = fecha_hoy().replace(day=1)
    sig = (ini + timedelta(days=32)).replace(day=1)
    ant = (ini - timedelta(days=1)).replace(day=1)
    todas = lista(salas.find({}).sort("nombre", 1))
    sala_sel = request.args.get("sala", "todas")
    sid = oid(sala_sel) if sala_sel != "todas" else None
    if sid and any(x["_id"] == sid for x in todas):
        elegidas = [x for x in todas if x["_id"] == sid]
    else:
        sala_sel, elegidas = "todas", todas

    eventos = eventos_mes(ini, sig, elegidas)

    if request.args.get("formato") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Fecha", "Día", "Sala", "Inicio", "Fin", "Tipo", "Materia", "Grupo",
                    "Docente", "Estado", "Asistencia", "Registrada por", "Hora de registro",
                    "Motivo / detalle", "Cancelada por"])
        for e in eventos:
            w.writerow([e["fecha"], e["dia"], _csv_seguro(e["sala"]), e["inicio"], e["fin"],
                        e["tipo"], _csv_seguro(e["materia"]), _csv_seguro(e["grupo"]),
                        _csv_seguro(e["docente"]), e["estado"],
                        TEXTO_ASIS.get(e["asis"], ""), _csv_seguro(e["asis_por"]), e["asis_hora"],
                        _csv_seguro(e["detalle"]), _csv_seguro(e["por"])])
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="rescom_reporte_{ini:%Y-%m}.csv"'})

    orient = "vertical" if request.args.get("orient") == "vertical" else "horizontal"
    resumen = resumen_mes(eventos)
    dias_rep = []
    for fecha, grupo in itertools.groupby(eventos, key=lambda e: e["fecha"]):
        filas = list(grupo)
        dias_rep.append({"fecha": fecha, "titulo": f'{filas[0]["dia"]} {fecha[8:10]}/{fecha[5:7]}/{fecha[:4]}',
                         "filas": filas})
    return render_template(
        "reportes.html", dias_rep=dias_rep, resumen=resumen, salas=todas, sala_sel=sala_sel,
        orient=orient,
        mes=f"{ini:%Y-%m}", mes_txt=f"{MESES[ini.month - 1].capitalize()} {ini.year}",
        prev=f"{ant:%Y-%m}", nxt=f"{sig:%Y-%m}",
        total_prog=sum(x["programadas"] for x in resumen),
        total_imp=sum(x["impartidas"] for x in resumen),
        total_canc=sum(x["canceladas"] for x in resumen),
        total_res=sum(x["reservas"] for x in resumen),
        total_res_canc=sum(x["reservas_canc"] for x in resumen),
        total_horas=sum(x["horas"] for x in resumen),
        total_pres=sum(x["presentes"] for x in resumen),
        total_aus=sum(x["ausentes"] for x in resumen),
        total_sinreg=sum(x["sin_registro"] for x in resumen),
        periodo_txt=f"1 al {(sig - timedelta(days=1)).day} de {MESES[ini.month - 1]} de {ini.year}",
        sala_txt=elegidas[0]["nombre"] if len(elegidas) == 1 else "Todas las salas",
        generado_por=session.get("nombre", ""),
        generado=ahora_dt().strftime("%d/%m/%Y %H:%M"))


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
    return redirect(url_for("panel", _anchor="t6"))


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
    return redirect(url_for("panel", _anchor="t6"))


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
    return redirect(url_for("panel", _anchor="t6"))


@app.route("/docente/<did>/editar", methods=["POST"])
@login_required("encargado")
def editar_docente(did):
    d = usuarios.find_one({"_id": oid(did), "rol": "docente"})
    if not d:
        abort(404)
    nombre = request.form.get("nombre", "").strip()
    correo = request.form.get("correo", "").strip().lower()
    if not nombre or len(nombre) > 100:
        flash("Escribe el nombre completo (máx. 100 caracteres).", "error")
    elif not RE_CORREO.match(correo):
        flash("Correo inválido.", "error")
    else:
        try:
            usuarios.update_one({"_id": d["_id"]}, {"$set": {"nombre": nombre, "correo": correo}})
        except DuplicateKeyError:
            flash("Ese correo ya está registrado.", "error")
        else:
            if nombre != d["nombre"]:  # mantiene el nombre en las clases que ya tenía asignadas
                horarios_academicos.update_many(
                    {"docente": d["nombre"]}, {"$set": {"docente": nombre, "docente_corto": None}})
            flash(f"Datos de {nombre} actualizados.", "ok")
    return redirect(url_for("panel", _anchor="t6"))


@app.route("/docente/<did>/eliminar", methods=["POST"])
@login_required("encargado")
def eliminar_docente(did):
    d = usuarios.find_one({"_id": oid(did), "rol": "docente"})
    if not d:
        abort(404)
    vigentes = reservaciones.count_documents({
        "usuario_id": d["_id"], "estado": {"$in": ["Aprobada", "Pendiente"]},
        "fecha": {"$gte": fecha_hoy().isoformat()}})
    if vigentes:
        flash(f"{d['nombre']} tiene {vigentes} reservación(es) vigente(s). Cancélalas antes de eliminar la cuenta.",
              "error")
    else:
        usuarios.delete_one({"_id": d["_id"]})
        notificaciones.delete_many({"usuario_id": d["_id"]})
        flash(f"Cuenta de {d['nombre']} eliminada. Sus clases del horario se conservan.", "ok")
    return redirect(url_for("panel", _anchor="t6"))


# ------------------------------------------------------------------ horarios (encargado)
@app.route("/horarios/<sid>", methods=["POST"])
@login_required("encargado")
def guardar_horarios(sid):
    """Guarda la cuadrícula de una sala: materia y docente de cada hora, sin tocar el código."""
    sala = salas.find_one({"_id": oid(sid)})
    if not sala:
        abort(404)
    catalogo = {m["abreviatura"]: m for m in materias.find({})}
    cambios = 0
    for dia in NOMBRES_DIAS[:5]:
        for ini in SLOTS:
            abrev = request.form.get(f"m__{dia}__{ini}", "").strip()
            docente = request.form.get(f"d__{dia}__{ini}", "").strip()[:100]
            grupo = request.form.get(f"g__{dia}__{ini}", "").strip()[:20]
            if abrev and abrev not in catalogo:
                continue
            filtro = {"sala": sala["nombre"], "dia": dia, "hora_inicio": ini}
            actual = horarios_academicos.find_one(filtro)
            ocupada = bool(actual and actual.get("estado") == "ocupada")
            ant_m = (actual.get("materia_abreviatura") or "") if ocupada else ""
            ant_d = (actual.get("docente") or actual.get("docente_corto") or "") if ocupada else ""
            ant_g = grupo_clase(actual, catalogo.get(ant_m)) if ocupada else ""
            if abrev == ant_m and (not abrev or (docente == ant_d and grupo == ant_g)):
                continue
            ahora_utc = datetime.now(timezone.utc)
            if not abrev:  # hora libre
                horarios_academicos.update_one(filtro, {"$set": {
                    "estado": "libre", "materia_abreviatura": None, "materia_abreviatura_original": None,
                    "materia_id": None, "docente": None, "docente_corto": None, "grupo": None,
                    "actualizado_en": ahora_utc}})
            else:
                fin = f"{(minutos(ini) + 60) // 60:02d}:00"
                horarios_academicos.update_one(filtro, {"$set": {
                    "estado": "ocupada", "materia_abreviatura": abrev,
                    "materia_abreviatura_original": abrev, "materia_id": catalogo[abrev]["_id"],
                    "docente": docente or None, "docente_corto": None, "grupo": grupo, "hora_fin": fin,
                    "actualizado_en": ahora_utc}}, upsert=True)
            cambios += 1
    flash(f"Horario de {sala['nombre']} guardado ({cambios} cambio(s))." if cambios
          else "No hubo cambios en el horario.", "ok")
    return redirect(url_for("panel", sala_h=sala["id"] if "id" in sala else str(sala["_id"]), _anchor="t8"))


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