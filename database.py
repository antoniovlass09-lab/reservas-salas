"""Conexión a MongoDB y preparación inicial de la base de datos.

Importar este módulo NO falla si MongoDB está apagado (la conexión es perezosa).
La app llama a conectar() e inicializar() al arrancar y muestra un error claro.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime

"""Conexión a MongoDB y preparación inicial de la base de datos.

Importar este módulo NO falla si MongoDB está apagado (la conexión es perezosa).
La app llama a conectar() e inicializar() al arrancar y muestra un error claro.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from werkzeug.security import generate_password_hash

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "reservas_salas")

cliente = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = cliente[MONGO_DB]

usuarios = db["usuarios"]
salas = db["salas"]
reservaciones = db["reservaciones"]
notificaciones = db["notificaciones"]
bloqueos = db["bloqueos"]
materias = db["materias"]

# Salas oficiales (se pueden agregar o renombrar desde Administración > Salas).
SALAS_OFICIALES = ["SC1", "SC2", "SC3", "LR", "LIDA"]


def ahora_texto():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def oid(valor):
    """Convierte a ObjectId o devuelve None si no es válido."""
    try:
        return ObjectId(str(valor))
    except (InvalidId, TypeError):
        return None


def conectar():
    try:
        cliente.admin.command("ping")
    except ServerSelectionTimeoutError:
        raise RuntimeError(
            f"No se pudo conectar con MongoDB en {MONGO_URI}. "
            "Verifica que el servicio esté iniciado."
        )


def inicializar(sembrar_demo=False):
    """Crea índices, registra las salas oficiales y (opcional) usuarios demo."""
    usuarios.create_index("correo", unique=True)
    salas.create_index("nombre", unique=True)
    reservaciones.create_index([("sala_id", 1), ("fecha", 1)])
    reservaciones.create_index([("usuario_id", 1)])
    bloqueos.create_index([("sala_id", 1), ("fecha", 1)])
    notificaciones.create_index([("usuario_id", 1), ("leida", 1)])
    try:
        materias.create_index("abreviatura", unique=True)
    except Exception:
        pass  # si ya existe con otra configuración, no se interrumpe el arranque

    for nombre in SALAS_OFICIALES:
        if salas.find_one({"nombre": nombre}) is None:
            salas.insert_one({"nombre": nombre, "activa": True})

    if sembrar_demo and usuarios.count_documents({}) == 0:
        usuarios.insert_one({"nombre": "Encargado Centro de Cómputo",
                             "correo": "encargado@institucion.edu",
                             "password": generate_password_hash("admin123"),
                             "rol": "encargado"})
        usuarios.insert_one({"nombre": "Docente Demo",
                             "correo": "docente@institucion.edu",
                             "password": generate_password_hash("docente123"),
                             "rol": "docente"})
