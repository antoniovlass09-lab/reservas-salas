# Sistema Web de Reservación de Salas de Cómputo

Flask + MongoDB. Roles: **docente** y **encargado**. Salas: SC1, SC2, SC3, LR, LIDA.

## Requisitos
- Python 3.10+
- MongoDB en ejecución (por defecto `mongodb://localhost:27017/`)

## Ejecutar en VS Code
1. Abre esta carpeta (`File > Open Folder`) y, en la terminal:
   ```
   python -m venv venv
   venv\Scripts\activate          (Windows)   |   source venv/bin/activate (Mac/Linux)
   pip install -r requirements.txt
   python probar_mongo.py         (comprueba la conexión y prepara la base)
   ```
2. Crea el usuario encargado: `python crear_encargado.py`
3. Inicia la app: `python app.py` y abre http://127.0.0.1:5000
   (o usa F5 en VS Code: el perfil "Flask (desarrollo)" activa el modo depuración
   y crea usuarios demo `docente@institucion.edu / docente123` y
   `encargado@institucion.edu / admin123` solo si la base está vacía).

## Variables de entorno (ver `.env.example`)
| Variable | Para qué sirve |
|---|---|
| `SECRET_KEY` | Clave de sesión. **Obligatoria en producción.** |
| `MONGO_URI`, `MONGO_DB` | Conexión y nombre de la base. |
| `DOMINIO` | Limita el registro a un correo institucional (ej. `@itminatitlan.edu.mx`). |
| `SEED_DEMO=1` | Crea usuarios demo si no hay usuarios (solo desarrollo). |
| `FLASK_DEBUG=1` | Modo depuración (solo desarrollo). |
| `COOKIE_SECURE=1` | Cookies solo por HTTPS. |

## Reglas del sistema
- Horario de 07:00 a 21:00 en bloques de 30 minutos; máximo 4 horas por reservación.
- Reservas hasta con 60 días de anticipación; no se aceptan horas que ya pasaron.
- Sin traslapes con reservaciones aprobadas ni bloqueos.
- Al bloquear un horario con reservas, el encargado decide si las cancela (se notifica a los docentes).
- Las aprobadas que ya pasaron se marcan *Finalizada* y las pendientes sin atender, *Vencida*.
- Las salas (solo nombre) se renombran, activan o crean en **Administración > Salas**.
- Los docentes se agregan en **Administración > Docentes** (con contraseña temporal) o se registran solos desde el login.

## Seguridad incluida
Protección CSRF en todos los formularios, cierre de sesión por POST, límite de 5 intentos de
login fallidos cada 5 minutos, contraseñas con hash (mín. 8 caracteres) y cookies HttpOnly/SameSite.

## Logos
Copia los logos a `static/img/` (`logo-tecnm.png`, `logo-itm.png`). Si no existen se muestran insignias de texto.
