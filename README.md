RESCOM ITM · Sistema de Reservaciones de Salas de Cómputo
Flask + MongoDB. Roles: docente y encargado. Salas: SC1, SC2, SC3, LR, LIDA.
Requisitos
Python 3.10+
MongoDB en ejecución (por defecto `mongodb://localhost:27017/`)
Ejecutar en VS Code
Abre esta carpeta (`File > Open Folder`) y, en la terminal:
```
   python -m venv venv
   venv\Scripts\activate          (Windows)   |   source venv/bin/activate (Mac/Linux)
   pip install -r requirements.txt
   python probar_mongo.py         (comprueba la conexión y prepara la base)
   ```
Crea el usuario encargado: `python crear_encargado.py`
Inicia la app: `python app.py` y abre http://127.0.0.1:5000
(o usa F5 en VS Code: el perfil "Flask (desarrollo)" activa el modo depuración
y crea usuarios demo `docente@institucion.edu / docente123` y
`encargado@institucion.edu / admin123` solo si la base está vacía).
Variables de entorno (ver `.env.example`)
Variable	Para qué sirve
`SECRET_KEY`	Clave de sesión. Obligatoria en producción.
`MONGO_URI`, `MONGO_DB`	Conexión y nombre de la base.
`DOMINIO`	Limita el registro a un correo institucional (ej. `@itminatitlan.edu.mx`).
`SEED_DEMO=1`	Crea usuarios demo si no hay usuarios (solo desarrollo).
`FLASK_DEBUG=1`	Modo depuración (solo desarrollo).
`COOKIE_SECURE=1`	Cookies solo por HTTPS (actívalo al publicar con HTTPS).
`TRUST_PROXY=1`	Solo si hay un proxy de confianza delante (Render, Nginx): usa la IP real del visitante.
`RATE_LIMIT_PER_MIN`	Peticiones por minuto por IP (600 por defecto).
Reglas del sistema
Horario de 07:00 a 21:00; cada reservación dura exactamente 1 hora.
Reservas hasta con 60 días de anticipación; no se aceptan horas que ya pasaron.
Sin traslapes con reservaciones aprobadas, bloqueos ni clases del horario académico (`python cargar_horarios.py` lo carga).
Al bloquear un horario con reservas, el encargado decide si las cancela (se notifica a los docentes).
Las aprobadas que ya pasaron se marcan Finalizada y las pendientes sin atender, Vencida.
Las materias que el docente elige al reservar se administran en Administración > Materias (`python cargar_horarios.py` carga el catálogo inicial).
Reportes (menú del encargado): reporte mensual por sala con materias, docentes, reservaciones y horas; se descarga en CSV (Excel) o se imprime/guarda como PDF con los logos.
Las salas (solo nombre) se renombran, activan o crean en Administración > Salas.
Los docentes se agregan en Administración > Docentes (con contraseña temporal) o se registran solos desde el login.
Seguridad incluida
Protección CSRF en todos los formularios y cierre de sesión por POST.
Límite de intentos de login (5 por correo y 30 por IP cada 5 minutos), límite de registros y límite general de peticiones por IP.
Contraseñas con hash, mínimo 8 caracteres con letras y números.
Cabeceras de seguridad: Content-Security-Policy estricta, X-Frame-Options, HSTS (con `COOKIE_SECURE=1`), nosniff y sin caché en páginas con sesión.
`SECRET_KEY` obligatoria (la app no arranca sin ella fuera de modo depuración), cookies HttpOnly/SameSite y tamaño máximo de formularios.
Errores genéricos (sin detalles internos) y exportación CSV protegida contra inyección de fórmulas.
En producción usa `gunicorn app:app` con HTTPS, nunca `FLASK_DEBUG=1`; en MongoDB Atlas restringe las IP permitidas y usa una contraseña única.
Logos
Los logos del TecNM y del ITM están en `static/img/` (`logo-tecnm.png`, `logo-itm.png`). Si no existen se muestran insignias de texto.