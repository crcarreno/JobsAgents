# AutonomousAgent

Agente autónomo para descubrir, evaluar, ejecutar y entregar trabajos remunerados, administrado desde un panel Flask y protegido por Apache 2.4 + HTTPS.

El proyecto está implementado de extremo a extremo: panel web, autenticación, MySQL con migraciones, proveedor de IA OpenCode Go, Worker independiente, adaptador real de GitHub, aislamiento Docker, verificación, entrega, control de presupuesto y scripts de despliegue en Ubuntu.

---

## 1. Qué funciona y qué no

### Implementado y probado

- Panel Flask con login, sesiones seguras, CSRF, bloqueo por intentos fallidos y hash scrypt.
- Dashboard con estado del agente y del Worker, tareas por estado, ingresos, gasto de IA, presupuesto, rentabilidad por plataforma y últimos errores.
- Administración de plataformas: alta, edición, activación, intervalo de escaneo, permiso de automatización y prueba de conexión.
- Configuración de IA desde el panel: proveedor, modelo/perfil, límites, presupuesto y API Key cifrada.
- Integración real con **OpenCode Go** mediante su API oficial de inferencia (`https://opencode.ai/inference/go/openai/v1`), con cabecera de sesión `x-opencode-session`, User-Agent propio, timeout, reintentos y coste real reportado por la API.
- Adaptador real **GitHub Bounties** usando la API REST oficial de GitHub: búsqueda de issues, detalle, comprobación de permiso de automatización, comentario de claim opcional, apertura de Pull Request y consulta de estado.
- Motor de rentabilidad en código convencional (no delegado a la IA).
- Evaluación con IA y heurística segura cuando no hay API Key.
- Estados auditables con historial de transiciones e idempotencia en claim y entrega.
- Worker independiente con bloqueo de instancia, heartbeat y estado consultable.
- Ejecución aislada en Docker sin red por defecto, usuario no privilegiado, límites de memoria/CPU/PIDs, `cap-drop=ALL`, `no-new-privileges`, filesystem de solo lectura y workspace propio.
- Verificación determinista (artefactos, secretos, inyección, comandos fallidos) más verificación con IA.
- Migración Alembic completa y esquema MySQL.
- Apache 2.4 (HTTP + HTTPS), Gunicorn, systemd, scripts de instalación, despliegue, backup y healthcheck.
- 45 pruebas automatizadas.

### Limitaciones reales y deliberadas

- **No hay adaptadores ficticios.** Solo se implementó la plataforma para la que existe una vía oficial y verificable de extremo a extremo.
- **La mayoría de plataformas de microtareas y freelance prohíben bots o no ofrecen API para trabajadores.** Upwork, Fiverr, Clickworker, Remotasks/Outlier, Microworkers, Toloka (lado trabajador), Appen y similares no fueron implementados. Se documenta el motivo en la sección 2.
- **Algora no se usa mediante su API.** Su página de términos prohíbe el acceso automatizado al servicio. El adaptador de GitHub descubre bounties con la API oficial de GitHub y solo opera en repositorios que el operador autorice explícitamente. No se automatiza el acceso a `console.algora.io`.
- **El pago no se puede verificar desde GitHub.** El adaptador devuelve `unverifiable`; el ingreso debe confirmarse y registrarse manualmente en el panel. Esto es intencional: no se inventan estados de pago.
- **El objetivo de USD 1–2/día no está garantizado.** Depende de bounties reales disponibles, de que el repositorio permita contribuciones automatizadas y de que el trabajo se fusione. El sistema no fabrica tareas ni ingresos.
- **La ejecución de comandos exige Docker.** Si Docker no está disponible, la tarea pasa a revisión manual; nunca se ejecuta código en el host.
- **No se evaden CAPTCHA ni controles.** No hay simulación de comportamiento humano.
- **Validación de la integración GitHub.** El adaptador está cubierto por pruebas automatizadas con respuestas simuladas de la API oficial (respx). No se abrió un Pull Request real en este entorno porque no hay token de GitHub; la primera entrega real debe probarse en modo manual contra un repositorio de prueba.
- **Validación de OpenCode Go.** El proveedor se probó contra el endpoint oficial real: conexión correcta, modelo `deepseek-v4.1-flash`, `billing_source=go`, coste reportado y consumo registrado en `ai_usage`.

---

## 2. Investigación previa

### 2.1 OpenCode Go

Fuentes oficiales consultadas: `https://opencode.ai/docs/console/go` y `https://opencode.ai/docs/console/api/inference`.

Hallazgos que condicionan el diseño:

1. OpenCode Go es una suscripción (Go / Go Plus / Go Teams) que expone modelos a través de una API de inferencia compatible con OpenAI Chat Completions, OpenAI Responses, Anthropic Messages y Gemini.
2. La autenticación es `Authorization: Bearer <token>` con una API Key personal o de service account del Console.
3. El cliente debe identificarse con un User-Agent propio y enviar un identificador de sesión estable en `x-opencode-session`.
4. Los modelos gratuitos solo funcionan dentro de OpenCode; otros clientes reciben HTTP 403. Por eso el proyecto usa modelos pagados/Go.
5. Cada respuesta informa el coste en `usage.cost` y, para Go, `usage.opencode.allowance`, además de `billing_source`. Cuando `cost` es 0 por estar dentro del plan Go, el presupuesto contabiliza `allowance`.
6. No existe un modelo llamado `mixed`. `mixed` es un **perfil virtual** de este proyecto que se resuelve a IDs reales definidos en `config.yaml`:
   - análisis → `deepseek-v4.1-flash`
   - ejecución → `kimi-k2.7-code`
   - verificación → `deepseek-v4.1-flash`
   Puede reemplazarse por cualquier ID real devuelto por `GET https://opencode.ai/inference/v1/models`.
7. El sistema no depende de la CLI de OpenCode: usa la API HTTP oficial, lo que permite ejecutarse en un VPS sin interfaz interactiva.

### 2.2 Plataformas evaluadas

| Plataforma | API para trabajador | Automatización permitida | Ecuador | Decisión |
|---|---|---|---|---|
| GitHub + bounties (Algora, sponsors) | Sí, API REST oficial de GitHub | GitHub permite API; el repositorio/funder puede imponer reglas | Sí (pago vía Stripe del funder, p. ej. Algora cubre Ecuador) | **Implementada** |
| Algora Console API | Pública para listar bounties | Su ToS prohíbe acceso automatizado al servicio | Pago por Stripe, Ecuador soportado | No se automatiza su API; se descubre desde GitHub |
| Toloka | API disponible para *requesters*; el lado *toloker* no tiene API oficial de trabajador | No confirmado para performers | No confirmado | Rechazada |
| Upwork | API limitada y con aprobación | Bidding automatizado prohibido | Sí | Rechazada |
| Fiverr | Sin API pública para vendedores | Prohibido | Sí | Rechazada |
| Freelancer.com | API con OAuth | Reglas restrictivas sobre automatización de bids | Sí | Rechazada por riesgo |
| Clickworker / Remotasks / Outlier / Appen / Microworkers | Sin API pública de trabajador | Bots prohibidos | Variable | Rechazadas |
| HackerOne / Bugcrowd | APIs parciales | Testing automatizado permitido dentro de scope | Pago variable | Fuera del alcance inicial |

Conclusión: la primera integración viable y honesta es GitHub Bounties. El sistema puede añadir más adaptadores cuando exista una autorización verificable; la interfaz `BaseAdapter` ya está preparada.

---

## 3. Arquitectura

```
Usuario -> HTTPS -> Apache 2.4 (mod_proxy, mod_ssl) -> Gunicorn -> Flask
                                                             |
                                                     MySQL 8 (127.0.0.1)
                                                             |
Worker independiente (systemd) -> Servicios -> Adaptadores -> GitHub API
                                      |
                              OpenCode Go (HTTPS)
                                      |
                              Docker sandbox (sin red)
```

- Apache es el único punto público. Gunicorn escucha en `127.0.0.1:8000`. MySQL escucha en `127.0.0.1:3306`.
- El Worker corre en un proceso y servicio systemd distintos del panel.
- La IA nunca decide la economía ni los permisos: esas reglas están en Python.
- El contenido de la tarea es dato no confiable; se envuelve con delimitadores y se escanea para detectar inyección de prompts.

### Estructura

```
app/
  routes/        auth, dashboard, tasks, platforms, settings, earnings
  services/      discovery, evaluator, execution, verification, delivery,
                 profitability, budget, settings, artifacts, logging
  providers/     base + opencode_provider
  adapters/      base_adapter, github_adapter, registry
  models/        users, platforms, tasks, executions, earnings, logs, ai_usage...
  templates/     panel responsive sin dependencias externas
  static/        CSS y JS propios
worker/          main, scheduler, task_processor
core/            config, database, security, logger, exceptions, timeutil
execution/       docker_executor, workspace_manager
migrations/      Alembic (0001_initial)
deployment/      apache, systemd, gunicorn, docker
scripts/         install, deploy, start, stop, restart, backup, healthcheck
tests/           45 pruebas
```

---

## 4. Requisitos

- Ubuntu Server 22.04/24.04 (producción).
- Python 3.11+ (probado con 3.13).
- MySQL 8.
- Apache 2.4 con `mod_proxy`, `mod_proxy_http`, `mod_ssl`, `mod_headers`, `mod_rewrite`.
- Docker (para ejecución aislada).
- Cuenta OpenCode Go y API Key.
- Cuenta GitHub y token con `public_repo` o `repo`.

---

## 5. Instalación local (desarrollo)

Requisito: MySQL 8 accesible en `127.0.0.1:3306`.

```sql
CREATE DATABASE db_jobagents CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE db_jobagents_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # DATABASE_URL, APP_SECRET_KEY, AI_API_KEY, etc.
# Para desarrollo local sobre HTTP:
#   APP_ENVIRONMENT=development
#   DATABASE_URL=mysql+pymysql://usuario:clave@127.0.0.1:3306/db_jobagents?charset=utf8mb4
alembic upgrade head
flask --app wsgi seed
flask --app wsgi create-admin --username admin
flask --app wsgi run --port 8000
```

En producción, `APP_ENVIRONMENT=production` activa cookies `Secure`; no uses `development` en el VPS.

El modo inicial es `observation`: descubre y evalúa, pero no reclama ni ejecuta.

---

## 6. Configuración

### Precedencia

1. `system_settings` en MySQL (lo que se edita en el panel).
2. Variables de entorno (`.env`).
3. `config.yaml`.
4. Valores por defecto del código.

Las credenciales reales viven en variables de entorno o cifradas con Fernet en la base de datos. Nunca se muestran completas ni se escriben en logs. `APP_SECRET_KEY` es obligatoria en producción y debe ser estable: cambiarla invalida los secretos cifrados.

### Variables principales

Ver `.env.example`. Las más importantes:

- `DATABASE_URL`
- `APP_SECRET_KEY`, `FLASK_SECRET_KEY`
- `AI_API_KEY`, `AI_MODEL`
- `GITHUB_TOKEN`
- `JOBAGENTS_AGENT_MODE` (`observation`, `semiautomatic`, `autonomous`)

### Perfiles de IA

El valor configurado actualmente es `deepseek-v4.1-flash`. Si se usa `ai.model: mixed`, el perfil se resuelve con este mapa de `config.yaml`:

```yaml
ai:
  profiles:
    mixed:
      analysis: deepseek-v4.1-flash
      execution: kimi-k2.7-code
      verification: deepseek-v4.1-flash
```

También se puede fijar un ID real directamente, por ejemplo `AI_MODEL=deepseek-v4.1-flash`.

---

## 7. Base de datos

Tablas: `users`, `platform_config`, `tasks`, `task_executions`, `earnings`, `system_logs`, `system_settings`, `ai_usage`, `task_status_history`, `worker_state`.

Migraciones:

```bash
alembic upgrade head
alembic current
alembic downgrade -1
```

`flask --app wsgi init-db` existe solo como atajo de desarrollo; en producción use Alembic.

---

## 8. Modos de operación

| Modo | Descubre | Evalúa | Reclama | Ejecuta | Entrega |
|---|---|---|---|---|---|
| `observation` | Sí | Sí | No | No | No |
| `semiautomatic` | Sí | Sí | Manual | Sí | Manual |
| `autonomous` | Sí | Sí | Automático si está permitido | Sí | Automático si está permitido |

`autonomous` solo se puede activar si existe al menos una plataforma habilitada con `automation_permission = allowed` y hay API Key de IA. La validación se hace en el panel y en el Worker.

### Estados

`pending`, `evaluating`, `approved`, `executing`, `completed`, `ready_to_submit`, `submitted`, `accepted`, `rejected`, `failed`, `expired`, `manual_review`.

Cada transición queda en `task_status_history`. `claim` y `submit` usan actualizaciones condicionales para evitar duplicados.

---

## 9. Adaptador GitHub Bounties

Configuración por defecto (plataforma deshabilitada hasta que el operador la active):

```json
{
  "search_queries": ["is:issue is:open label:bounty", "is:issue is:open label:\"💎 Bounty\""],
  "allowed_repos": [],
  "max_results": 30,
  "min_reward_usd": 0.5,
  "claim_comment": "",
  "automation_allowed_repos": [],
  "automation_denied_repos": [],
  "pr_labels": [],
  "submit_as_draft": true,
  "fork_wait_seconds": 30
}
```

Flujo:

1. `search_tasks()` consulta `GET /search/issues` con las queries configuradas.
2. Extrae la recompensa de labels/título/cuerpo (`$50`, `USD 50`, etc.). Sin recompensa verificable, la tarea se descarta.
3. `get_task_details()` obtiene el issue, comentarios y metadatos del repositorio.
4. `check_automation_permission()`:
   - `denied` si el issue dice explícitamente que no se permiten bots/IA o el repo está en `automation_denied_repos`.
   - `allowed` si el repo está en `automation_allowed_repos`.
   - `unknown` en cualquier otro caso; la tarea queda en `manual_review` y no se ejecuta.
5. `claim_task()` publica `claim_comment` solo si está configurado. No se inventa un mecanismo de claim.
6. `submit_task()` crea un fork si hace falta, sube blobs, crea un commit y abre un Pull Request **draft** con divulgación explícita de que fue preparado por un agente.
7. `check_task_status()` consulta el issue y el PR (merged/closed).
8. `get_payment_status()` devuelve `unverifiable`; el pago se registra manualmente.

### Puesta en marcha segura

1. Crea una plataforma con adapter `github_bounty`.
2. Déjala deshabilitada y con `automation_permission = unknown`.
3. Configura `search_queries` y `allowed_repos`.
4. Ejecuta “Test” y “Scan now”.
5. Revisa las tareas en `manual_review`.
6. Solo tras verificar las reglas del repositorio, añade el repo a `automation_allowed_repos` y marca la plataforma como `allowed`, documentando la autorización.
7. Ejecuta una tarea manualmente y revisa el PR antes de habilitar `autonomous`.

---

## 10. Seguridad

- Contraseñas con scrypt (`werkzeug`).
- CSRF en todos los formularios (`Flask-WTF`).
- Cookies `HttpOnly`, `SameSite=Lax`, `Secure` en producción.
- `ProxyFix` para confiar solo en el proxy local de Apache.
- Bloqueo temporal de cuenta tras intentos fallidos.
- Secretos cifrados con Fernet derivado de `APP_SECRET_KEY`; nunca se registran.
- Filtro de logs que redacta Bearer tokens, API keys, contraseñas y tokens de GitHub/OpenCode.
- Contenido externo tratado como no confiable y envuelto en delimitadores.
- Detección de patrones de inyección de prompts y de secretos en artefactos.
- Docker: sin red por defecto, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--read-only`, `tmpfs` para `/tmp`, usuario no root, límites de memoria/CPU/PIDs y timeout.
- Nunca se ejecuta código de terceros en el host.
- Nunca se realizan pagos ni transferencias automáticamente.
- El agente no puede modificar su propia configuración desde el contenido de una tarea.

### Antes de habilitar autonomía

- Revisar los términos del repositorio y del funder del bounty.
- Confirmar que el PR automático está permitido.
- Mantener `require_automation_permission = true`.
- Empezar en `observation` y pasar a `semiautomatic` antes de `autonomous`.

---

## 11. Worker

```bash
python -m worker.main
```

- Usa `filelock` para impedir dos instancias.
- Publica heartbeat y estado en `worker_state`.
- Escanea plataformas según su intervalo.
- Evalúa tareas pendientes.
- En modos no-observación ejecuta tareas aprobadas (máximo diario configurable).
- En autónomo reclama y entrega cuando está permitido.
- Refresca el estado de tareas enviadas.
- Expira tareas antiguas.
- Se detiene limpiamente con SIGTERM/SIGINT.

## 12. Despliegue en Ubuntu

### Rápido

```bash
sudo DOMAIN=agent.example.com bash scripts/install.sh
```

El script instala dependencias, crea usuario y base de datos, genera `.env`, aplica migraciones, crea el admin, instala systemd y Apache, construye la imagen sandbox y arranca los servicios.

Si `DOMAIN` está definido, el script instala primero un VirtualHost HTTP mínimo, obtiene el certificado con Certbot y solo entonces sustituye la configuración por la versión HTTPS completa. Si Certbot falla, conserva HTTP y lo advierte.

Después:

```bash
sudo certbot certificates
sudo /opt/autonomous-agent/scripts/healthcheck.sh
```

`docker-compose.yml` es para desarrollo local. En ese entorno el Worker no monta el socket de Docker y `JOBAGENTS_EXECUTION_DOCKER_ENABLED=false`, por lo que las tareas que requieren comandos quedan en revisión manual. La ejecución en sandbox está soportada en el despliegue nativo con systemd.

### Manual

```bash
sudo apt install python3 python3-venv mysql-server apache2 certbot python3-certbot-apache docker.io
sudo a2enmod proxy proxy_http ssl headers rewrite
```

Configura `.env`, ejecuta `alembic upgrade head`, instala los archivos de `deployment/systemd/` en `/etc/systemd/system/`, los de `deployment/apache/` en `/etc/apache2/sites-available/`, y activa los servicios.

### Operación

```bash
sudo scripts/start.sh
sudo scripts/stop.sh
sudo scripts/restart.sh
sudo scripts/healthcheck.sh
sudo scripts/backup.sh
sudo scripts/deploy.sh          # actualiza dependencias, migra y reinicia
```

Logs:

```bash
journalctl -u autonomous-agent-web -f
journalctl -u autonomous-agent-worker -f
tail -f /opt/autonomous-agent/var/log/jobagents.log
tail -f /var/log/apache2/autonomous-agent-error.log
```

### Backup y recuperación

`scripts/backup.sh` genera `mysqldump` comprimido y un tar de `var/artifacts` con retención de 14 días.

Recuperación:

```bash
gunzip < /var/backups/jobagents/db-YYYYMMDD.sql.gz | mysql -u jobagents -p jobagents
tar -xzf /var/backups/jobagents/artifacts-YYYYMMDD.tar.gz -C /opt/autonomous-agent
sudo systemctl restart autonomous-agent-web autonomous-agent-worker
```

---

## 13. Panel

- `/` Dashboard.
- `/tasks/` listado y filtros.
- `/tasks/<id>` detalle, evaluación, aprobación, claim, ejecución, entrega, refresh y rechazo.
- `/platforms/` alta, edición, test y escaneo.
- `/earnings/` registro manual de ingresos y conciliación.
- `/settings/` IA, modos, presupuestos, rentabilidad, ejecución y seguridad.
- `/healthz` healthcheck sin autenticación (no expone secretos).

---

## 14. Pruebas

```bash
pip install -r requirements-dev.txt
pytest -q
```

Las pruebas usan MySQL real contra la base `db_jobagents_test` (se crea automáticamente). No se usa SQLite.

Cobertura actual: seguridad, configuración, rentabilidad, adaptador GitHub (con respx), proveedor OpenCode Go (con respx), panel Flask, transiciones, evaluación heurística, idempotencia de entrega y Worker.

---

## 15. Añadir una plataforma

1. Verifica que exista una API oficial para trabajadores y que la automatización esté permitida por escrito.
2. Implementa `BaseAdapter` en `app/adapters/`.
3. Regístralo en `app/adapters/registry.py`.
4. Documenta plataforma, países, autenticación, API, restricciones, pagos, comisiones y límites en `platform_documentation`.
5. Añade pruebas con `respx`.
6. No lo marques como funcional si la autorización no está confirmada.

---

## 16. Diagnóstico

```bash
flask --app wsgi doctor
```

Comprueba base de datos, API Key de IA, token de GitHub y Docker. Si falta algo:

- **403 de OpenCode Go**: estás usando un modelo gratuito desde fuera de OpenCode. Usa un modelo pagado/Go.
- **Sin Docker**: las tareas con comandos quedan en `manual_review`.
- **Permiso de automatización desconocido**: es el comportamiento correcto. Confirma el repositorio y autorízalo explícitamente.
- **Pago no verificado**: regístralo manualmente en `/earnings/`.
