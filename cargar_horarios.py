from datetime import datetime, timezone
import database as bd

# Conexión existente del proyecto
bd.conectar()
db = bd.db

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
]

# Equivalencias entre las abreviaturas del horario
# y las abreviaturas oficiales del catálogo.
equivalencias = {
    "FPROG": "FUND. PROGRAMACIÓN.",
    "MD": "MATEM. DISCRETAS.",
    "Acomp": "ARQ. COMP.",
    "LI": "LENG. INTERFAZ.",
    "EDAT": "ESTRUCTURA DE DATOS",
    "FTEL": "FUND. TELECOM.",
    "INV OPER": "INV OPERACIONES",
    "ISW": "ING. SOFT",
    "TBD": "TALLER BD",
}

dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

horas = [
    ("07:00", "08:00"),
    ("08:00", "09:00"),
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:00", "12:00"),
    ("12:00", "13:00"),
    ("13:00", "14:00"),
    ("14:00", "15:00"),
    ("15:00", "16:00"),
    ("16:00", "17:00"),
    ("17:00", "18:00"),
]

# Cada fila representa una hora.
# Cada elemento representa lunes, martes, miércoles, jueves y viernes.
# None significa que la sala está libre.

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
}


def cargar_materias():
    coleccion = db["materias"]

    coleccion.create_index("abreviatura", unique=True)

    ids_materias = {}

    for materia in materias:
        datos = {
            "abreviatura": materia["abreviatura"],
            "nombre": materia["nombre"],
            "clave": materia["clave"],
            "actualizado_en": datetime.now(timezone.utc),
        }

        coleccion.update_one(
            {"abreviatura": materia["abreviatura"]},
            {"$set": datos},
            upsert=True
        )

        documento = coleccion.find_one(
            {"abreviatura": materia["abreviatura"]}
        )

        ids_materias[materia["abreviatura"]] = documento["_id"]

    print(f"Materias cargadas: {coleccion.count_documents({})}")

    return ids_materias


def cargar_horarios(ids_materias):
    coleccion = db["horarios_academicos"]

    coleccion.create_index(
        [
            ("sala", 1),
            ("dia", 1),
            ("hora_inicio", 1)
        ],
        unique=True
    )

    total = 0

    for sala, filas in horarios.items():
        for indice_hora, fila in enumerate(filas):

            hora_inicio, hora_fin = horas[indice_hora]

            for indice_dia, abreviatura_original in enumerate(fila):

                dia = dias[indice_dia]

                if abreviatura_original is None:
                    estado = "libre"
                    materia_original = None
                    materia_oficial = None
                    materia_id = None

                else:
                    estado = "ocupada"
                    materia_original = abreviatura_original

                    materia_oficial = equivalencias.get(
                        abreviatura_original,
                        abreviatura_original
                    )

                    materia_id = ids_materias[materia_oficial]

                documento = {
                    "sala": sala,
                    "dia": dia,
                    "hora_inicio": hora_inicio,
                    "hora_fin": hora_fin,
                    "estado": estado,
                    "materia_abreviatura_original": materia_original,
                    "materia_abreviatura": materia_oficial,
                    "materia_id": materia_id,
                    "actualizado_en": datetime.now(timezone.utc),
                }

                coleccion.update_one(
                    {
                        "sala": sala,
                        "dia": dia,
                        "hora_inicio": hora_inicio,
                    },
                    {"$set": documento},
                    upsert=True
                )

                total += 1

    print(f"Registros de horarios procesados: {total}")
    print(
        "Total de horarios guardados:",
        coleccion.count_documents({})
    )


if __name__ == "__main__":
    try:
        ids = cargar_materias()
        cargar_horarios(ids)

        print("\n¡Carga terminada correctamente!")

    except Exception as error:
        print("\nError durante la carga:")
        print(error)

    finally:
        bd.cliente.close()