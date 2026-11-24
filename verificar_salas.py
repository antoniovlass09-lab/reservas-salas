import database as bd

bd.conectar()
for s in ["SC1", "SC2", "SC3", "LR", "LIDA"]:
    total = bd.db["horarios_academicos"].count_documents({"sala": s})
    ocupadas = bd.db["horarios_academicos"].count_documents({"sala": s, "estado": "ocupada"})
    print(s, "-> filas:", total, "| clases ocupadas:", ocupadas)
bd.cliente.close()
