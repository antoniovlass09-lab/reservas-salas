from datetime import datetime, timezone
import database as bd

db = bd.db
VERBOSO = True  # la app lo apaga al sincronizar en segundo plano


def _decir(*args):
    if VERBOSO:
        print(*args)

# Materias oficiales del catalogo: abreviatura, nombre completo y clave.
materias = [
    {"abreviatura": "PROG", "nombre": "PROGRAMACIÓN", "clave": None},
    {"abreviatura": "FUND. PROGRAMACIÓN.", "nombre": "FUNDAMENTOS DE PROGRAMACION", "clave": "AED1285"},
    {"abreviatura": "CEMP", "nombre": "CULTURA EMPRESARIAL", "clave": "SCC1005"},
    {"abreviatura": "MATEM. DISCRETAS.", "nombre": "MATEMATICAS DISCRETAS", "clave": "AEF1041"},
    {"abreviatura": "ARQ. COMP.", "nombre": "ARQUITECTURA DE COMPUTADORAS", "clave": "SCD1003"},
    {"abreviatura": "LENG. INTERFAZ.", "nombre": "LENGUAJE DE INTERFAZ", "clave": "SCC1014"},
    {"abreviatura": "ESTRUCTURA DE DATOS", "nombre": "ESTRUCTURA DE DATOS", "clave": "AED1026"},
    {"abreviatura": "FUND. TELECOM.", "nombre": "FUNDAMENTOS DE TELECOMUNICACIONES", "clave": "AEC1034"},
    {"abreviatura": "INV OPERACIONES", "nombre": "INVESTIGACIÓN DE OPERACIONES", "clave": "SCC1013"},
    {"abreviatura": "LyA II", "nombre": "LENGUAJE Y AUTOMATAS II", "clave": "SCD1016"},
    {"abreviatura": "IO", "nombre": None, "clave": None},
    {"abreviatura": "SO", "nombre": "SISTEMAS OPERATIVOS", "clave": "AEC1061"},
    {"abreviatura": "ING. SOFT", "nombre": "INGENIERIA EN SOFTWARE", "clave": "SCD1011"},
    {"abreviatura": "IA", "nombre": "INTELIGENCIA ARTIFICIAL", "clave": "SCC1012"},
    {"abreviatura": "CDAT", "nombre": "CIENCIA DE DATOS", "clave": "CDF2501"},
    {"abreviatura": "TALLER BD", "nombre": "TALLER BASE DE DATOS", "clave": "SCA1025"},
    # Materias nuevas de LR y SC2 (None = pendiente de completar)
    {"abreviatura": "FISW", "nombre": "FUNDAMENTOS DE INGENIERIA DE SOFTWARE", "clave": None},
    {"abreviatura": "BDNR", "nombre": None, "clave": None},
    {"abreviatura": "DWEB", "nombre": None, "clave": None},
    {"abreviatura": "RCOMI", "nombre": None, "clave": None},
    {"abreviatura": "IE", "nombre": None, "clave": None},
    {"abreviatura": "CERD", "nombre": "CERT", "clave": "SCD1004"},
    {"abreviatura": "INF", "nombre": None, "clave": None},
    {"abreviatura": "BDNSQL", "nombre": "BIGDATA & NSQL", "clave": "CDD2503"},
    {"abreviatura": "Ciber", "nombre": "CIBERSEGURIDAD", "clave": "CDD2505"},
]

# Equivalencias entre las siglas del horario y las abreviaturas del catalogo.
equivalencias = {
    "FPROG": "FUND. PROGRAMACIÓN.",
    "Prog": "PROG",
    "MD": "MATEM. DISCRETAS.",
    "Acomp": "ARQ. COMP.",
    "LI": "LENG. INTERFAZ.",
    "EDAT": "ESTRUCTURA DE DATOS",
    "ESTDAT": "ESTRUCTURA DE DATOS",
    "FTEL": "FUND. TELECOM.",
    "INV OPER": "INV OPERACIONES",
    "ISW": "ING. SOFT",
    "TBD": "TALLER BD",
    "LyAR": "LyA II",  # en el papel de SC2 aparece como LyAR
}

dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

horas = [("%02d:00" % h, "%02d:00" % (h + 1)) for h in range(7, 19)]  # 07 a 19

# Cada fila es una hora; las columnas son lunes a viernes. None = libre.
horarios = {
    "SC1": [
        ["PROG", "PROG", None, None, "PROG"],
        ["PROG", None, "CEMP", "CEMP", "PROG"],
        ["FPROG", "FPROG", None, "FPROG", "FPROG"],
        [None, "MD", None, "MD", "FPROG"],
        ["Acomp", "FPROG", None, "FPROG", "FPROG"],
        ["LI", "FPROG", "LI", "FPROG", "FPROG"],
        ["EDAT", "FTEL", "EDAT", "FTEL", "EDAT"],
        ["INV OPER", "MD", "INV OPER", "MD", None],
        ["PROG", "LyA II", None, "LyA II", "PROG"],
        ["PROG", None, None, None, "PROG"],
        ["LyA II", "LyA II", "LyA II", "LyA II", "LyA II"],
    ],

    "SC2": [
        ["Prog", "Prog", "Prog", "Prog", None],
        ["TBD", "TBD", "TBD", "TBD", None],
        ["SO", "FTEL", "SO", "FTEL", None],
        [None, "MD", "IO", "MD", None],
        ["INF", "INF", "INF", "INF", "EDAT"],
        ["INF", "INF", "INF", "INF", "ISW"],
        ["IA", "SO", "IA", "SO", "LyAR"],
        [None, "Acomp", None, "Acomp", None],
        ["Ciber", "BDNSQL", "Ciber", "BDNSQL", "BDNSQL"],
        [None, "BDNSQL", None, "BDNSQL", "BDNSQL"],
        ["ESTDAT", "ESTDAT", "ESTDAT", "ESTDAT", "ESTDAT"],
        ["CERD", "CERD", "CERD", "CERD", None],
    ],

    "SC3": [
        [None, None, None, None, None],
        [None, None, None, None, None],
        [None, None, "IO", None, None],
        ["SO", "SO", "SO", "SO", None],
        ["PROG", "PROG", "LI", None, "LI"],
        ["ISW", "ISW", "LI", "ISW", "LI"],
        ["SO", "IA", "FTEL", "IA", None],
        ["CDAT", "CDAT", "CDAT", "CDAT", "CDAT"],
        ["ISW", None, "ISW", None, "ISW"],
        ["CDAT", "CDAT", "CDAT", "CDAT", "CDAT"],
        ["TBD", "TBD", "TBD", "TBD", None],
    ],

    "LR": [
        ["ISW", "ISW", "ISW", "ISW", "ISW"],
        ["TBD", "TBD", "TBD", "TBD", "TBD"],
        ["FISW", "FISW", "FISW", "FISW", "FISW"],
        [None, "Acomp", None, "Acomp", "Acomp"],
        ["BDNR", "DWEB", "BDNR", "DWEB", None],
        ["RCOMI", "RCOMI", "RCOMI", "RCOMI", None],
        ["IE", "IE", "IE", "IE", "IE"],
        ["CERD", "CERD", "CERD", "CERD", "CERD"],
        [None, None, None, None, None],
        ["FTEL", None, "FTEL", None, None],
    ],
}

# Nombre completo de cada docente (por su nombre corto del horario).
nombres_docentes = {
    "Felipe": "Felipe de Jesus Hernandez Perez",
    "Oyosa": "Guadalupe Jimenez Oyosa",
    "Lalo": "Eduardo",  # pendiente: faltan apellidos
    "Sydney": "Sydney de la Cruz Martinez",
    "Victor": "Victor Alberto Reyes Villavicencio",
    "Jaiber": "Jaiber Godinez Nuñez",
    "Romay": "Alberto Romay Guillen",
    "Wendy": "Wendy Carranza Diaz",
    "Andres": "Andres Diaz Elizalde",
    "Sonia": "Sonia Guzman Dominguez",
    "Edgar": "Edgar Perdomo Capetillo Ortiz",
    "Arturo": "Arturo Ivan Grajales Vazquez",
    "Kevin": "Kevin David Molina Gomez",
    "Humb": "Luis Humberto Morales Hernandez",
    "Paco": "Pablo Francisco Vivas Torres",
    "Miguel": "Jorge Miguel Guerrero Ambrosio",
}

# Docente de cada clase: misma forma que `horarios` (fila = hora, columna = día).
# Las salas que no aparecen aquí (SC1, SC3) todavía no tienen docentes capturados.
docentes = {
    "SC2": [
        ["Wendy", "Wendy", "Wendy", "Wendy", None],
        ["Andres", "Andres", "Andres", "Andres", None],
        ["Andres", "Sonia", "Andres", "Sonia", None],
        [None, "Edgar", "Victor", "Edgar", None],
        ["Wendy", "Wendy", "Wendy", "Wendy", "Arturo"],
        ["Wendy", "Wendy", "Wendy", "Wendy", "Oyosa"],
        ["Sonia", "Andres", "Sonia", "Andres", "Kevin"],
        [None, "Humb", None, "Humb", None],
        ["Arturo", "Paco", "Arturo", "Paco", "Paco"],
        [None, "Paco", None, "Paco", "Paco"],
        ["Miguel", "Miguel", "Miguel", "Miguel", "Miguel"],
        ["Kevin", "Kevin", "Kevin", "Kevin", None],
    ],
    "LR": [
        ["Felipe", "Felipe", "Felipe", "Felipe", "Felipe"],
        ["Felipe", "Felipe", "Felipe", "Felipe", "Felipe"],
        ["Oyosa", "Oyosa", "Oyosa", "Oyosa", "Oyosa"],
        [None, "Oyosa", None, "Oyosa", "Oyosa"],
        ["Lalo", "Sydney", "Lalo", "Sydney", None],
        ["Victor", "Victor", "Victor", "Victor", None],
        ["Jaiber", "Jaiber", "Jaiber", "Jaiber", "Jaiber"],
        ["Romay", "Romay", "Romay", "Romay", "Romay"],
        [None, None, None, None, None],
        ["Romay", None, "Romay", None, None],
    ],
}


def cargar_materias():
    coleccion = db["materias"]
    ids = {}
    for m in materias:
        doc = coleccion.find_one({"abreviatura": m["abreviatura"]})
        if doc is None:
            coleccion.insert_one({"abreviatura": m["abreviatura"], "nombre": m["nombre"],
                                  "clave": m["clave"], "grupo": None, "activa": True,
                                  "actualizado_en": datetime.now(timezone.utc)})
            _decir("  + materia nueva:", m["abreviatura"])
        else:
            falt = {}
            if not doc.get("nombre") and m["nombre"]:
                falt["nombre"] = m["nombre"]
            if not doc.get("clave") and m["clave"]:
                falt["clave"] = m["clave"]
            if falt:
                falt["actualizado_en"] = datetime.now(timezone.utc)
                coleccion.update_one({"_id": doc["_id"]}, {"$set": falt})
        ids[m["abreviatura"]] = coleccion.find_one({"abreviatura": m["abreviatura"]})["_id"]
    _decir("Materias en el catalogo:", coleccion.count_documents({}))
    return ids


def cargar_horarios(ids, solo_faltantes=False):
    """Carga el horario de cada sala.

    solo_faltantes=False: reescribe todo con lo que dice este archivo (uso manual).
    solo_faltantes=True: solo agrega los bloques que no existen en la base y nunca
    pisa lo que ya hay (es lo que hace la app al arrancar)."""
    coleccion = db["horarios_academicos"]
    coleccion.create_index([("sala", 1), ("dia", 1), ("hora_inicio", 1)], unique=True)
    for sala, filas in horarios.items():
        if solo_faltantes and coleccion.count_documents({"sala": sala}) >= len(filas) * len(dias):
            continue  # esa sala ya tiene su horario completo en la base
        ocupadas = 0
        for i, fila in enumerate(filas):
            hora_inicio, hora_fin = horas[i]
            for j, original in enumerate(fila):
                if original is None:
                    doc = {"estado": "libre", "materia_abreviatura_original": None,
                           "materia_abreviatura": None, "materia_id": None,
                           "docente_corto": None, "docente": None}
                else:
                    oficial = equivalencias.get(original, original)
                    doc = {"estado": "ocupada", "materia_abreviatura_original": original,
                           "materia_abreviatura": oficial, "materia_id": ids[oficial]}
                    ocupadas += 1
                    # Docente de la clase (si ya se capturó para esta sala).
                    corto = docentes.get(sala, [[None] * 5] * len(filas))[i][j]
                    if corto:
                        doc["docente_corto"] = corto
                        doc["docente"] = nombres_docentes.get(corto, corto)
                doc.update({"sala": sala, "dia": dias[j], "hora_inicio": hora_inicio,
                            "hora_fin": hora_fin,
                            "actualizado_en": datetime.now(timezone.utc)})
                filtro = {"sala": sala, "dia": dias[j], "hora_inicio": hora_inicio}
                if solo_faltantes:
                    for k in filtro:
                        doc.pop(k, None)
                    coleccion.update_one(filtro, {"$setOnInsert": doc}, upsert=True)
                else:
                    coleccion.update_one(filtro, {"$set": doc}, upsert=True)
        _decir("  Sala", sala + ": clases cargadas =", ocupadas)


def sincronizar():
    """Agrega a la base lo que falte (materias y bloques de horario) sin tocar lo existente."""
    global VERBOSO
    anterior, VERBOSO = VERBOSO, False
    try:
        ids = cargar_materias()
        cargar_horarios(ids, solo_faltantes=True)
    finally:
        VERBOSO = anterior


if __name__ == "__main__":
    try:
        bd.conectar()
        ids = cargar_materias()
        cargar_horarios(ids)
        print("Carga terminada correctamente!")
    except Exception as error:
        print("Error durante la carga:")
        print(error)
    finally:
        bd.cliente.close()