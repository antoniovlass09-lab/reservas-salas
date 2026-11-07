"""Crea (o promueve) un usuario con rol de encargado del Centro de Cómputo."""
import getpass

from werkzeug.security import generate_password_hash

import database as bd

try:
    bd.conectar()
except RuntimeError as e:
    print("ERROR:", e)
    raise SystemExit(1)
bd.inicializar()

correo = input("Correo del encargado: ").strip().lower()
existente = bd.usuarios.find_one({"correo": correo})
if existente:
    if input("Ese correo ya existe. ¿Convertirlo en encargado? (s/n): ").lower() == "s":
        bd.usuarios.update_one({"_id": existente["_id"]}, {"$set": {"rol": "encargado"}})
        print("Listo: ahora es encargado.")
    raise SystemExit(0)

nombre = input("Nombre completo: ").strip()
clave = getpass.getpass("Contraseña (mín. 8): ")
if not nombre or len(clave) < 8 or clave != getpass.getpass("Repite la contraseña: "):
    print("Datos inválidos o contraseñas distintas.")
    raise SystemExit(1)
bd.usuarios.insert_one({"nombre": nombre, "correo": correo,
                        "password": generate_password_hash(clave), "rol": "encargado"})
print("Encargado creado.")
