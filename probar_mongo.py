"""Comprueba la conexión con MongoDB y muestra el estado de la base de datos."""
import database as bd

try:
    bd.conectar()
except RuntimeError as e:
    print("ERROR:", e)
    raise SystemExit(1)

bd.inicializar()
print("CONEXION EXITOSA CON MONGODB")
print("Base de datos:", bd.db.name)
for nombre in ("usuarios", "salas", "reservaciones", "notificaciones", "bloqueos"):
    print(f"  {nombre}: {bd.db[nombre].count_documents({})} documento(s)")
print("Salas:")
for s in bd.salas.find({}).sort("nombre", 1):
    print(f"  {s['nombre']} · {'activa' if s.get('activa') else 'inactiva'}")
