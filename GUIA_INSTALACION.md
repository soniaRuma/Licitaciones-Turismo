# Guía de instalación (sin necesidad de programar)

Vas a hacer 4 bloques de pasos. Tardarás unos 20-30 minutos la primera vez. Después, todo funciona solo.

---

## Bloque 1 — Crear la base de datos en Supabase

1. Ve a **https://supabase.com** y crea una cuenta gratuita (puedes entrar con tu cuenta de Google/GitHub).
2. Pulsa **"New project"**. Ponle un nombre (ej. `licitaciones-turismo`), una contraseña (guárdala) y elige una región cercana (ej. Europa/Frankfurt).
3. Espera 1-2 minutos a que se cree el proyecto.
4. En el menú de la izquierda, entra en **SQL Editor** → **New query**.
5. Abre el archivo `supabase_schema.sql` de esta carpeta, copia todo su contenido, pégalo ahí, y pulsa **Run**. Esto crea las tablas.
6. Ve a **Project Settings** (icono de engranaje) → **API**. Ahí verás:
   - **Project URL** (algo como `https://xxxxx.supabase.co`)
   - **anon public key** (una clave larga)
   - **service_role key** (otra clave larga — ⚠️ esta es secreta, no la compartas ni la pongas en la web)

   Guarda estos 3 valores, los necesitarás en los siguientes bloques.

---

## Bloque 2 — Subir el proyecto a GitHub

1. Ve a **https://github.com** y crea una cuenta gratuita si no tienes.
2. Pulsa el botón **"+"** arriba a la derecha → **New repository**.
3. Ponle un nombre (ej. `licitaciones-turismo`), márcalo como **Private** (privado) o Public, como prefieras, y pulsa **Create repository**.
4. En la página del repositorio recién creado, pulsa **"uploading an existing file"** (o el botón de subir archivos).
5. Arrastra **todos** los archivos y carpetas de este proyecto (manteniendo la estructura de carpetas: `.github/workflows/daily.yml`, `scripts/ingest_placsp.py`, `web/index.html`, `requirements.txt`, `supabase_schema.sql`).
6. Pulsa **Commit changes** para guardar.

---

## Bloque 3 — Conectar el cron diario con Supabase (Secrets)

1. En tu repositorio de GitHub, ve a **Settings** → **Secrets and variables** → **Actions**.
2. Pulsa **New repository secret** y crea estos dos:
   - Nombre: `SUPABASE_URL` → Valor: la Project URL que guardaste antes.
   - Nombre: `SUPABASE_SERVICE_KEY` → Valor: la **service_role key** que guardaste antes (la secreta, no la "anon").
3. Ve a la pestaña **Actions** de tu repositorio. Debería aparecer el flujo **"Actualización diaria de licitaciones de Turismo"**.
4. Para probar que funciona ya, sin esperar al día siguiente, pulsa sobre ese flujo → **Run workflow** → **Run workflow** (botón verde). Espera 1-2 minutos y revisa que termine en verde (✅). Si sale en rojo (❌), pulsa encima para ver el mensaje de error.
5. A partir de aquí, se ejecutará **solo, todos los días**, sin que tengas que hacer nada.

---

## Bloque 4 — Publicar la web de consulta (GitHub Pages)

1. Antes de subir la web, edita el archivo `web/index.html`: busca estas dos líneas cerca del final del archivo:
   ```
   const SUPABASE_URL = "PEGA_AQUI_TU_SUPABASE_URL";
   const SUPABASE_ANON_KEY = "PEGA_AQUI_TU_SUPABASE_ANON_KEY";
   ```
   Sustituye por tu Project URL y tu **anon public key** (la pública, no la secreta). Puedes editar el archivo directamente en GitHub: ábrelo, pulsa el lápiz (editar), cambia esas dos líneas, y pulsa **Commit changes**.
2. En tu repositorio, ve a **Settings** → **Pages**.
3. En "Source", elige la rama `main` y la carpeta `/web` (o `/root` si GitHub no te deja elegir `/web`, en cuyo caso mueve el contenido de `web/` a la raíz del repositorio).
4. Pulsa **Save**. En un par de minutos, GitHub te dará una URL tipo `https://tu-usuario.github.io/licitaciones-turismo/` — esa es tu web, ya online, para consultar en tiempo real.

---

## ¿Qué hacer si algo falla?

- **El workflow de Actions sale en rojo:** pulsa sobre la ejecución fallida para ver el log de error. Lo más probable es un fallo temporal de descarga del gobierno — vuelve a intentarlo con "Run workflow".
- **La web no muestra nada:** revisa que hayas puesto bien la `SUPABASE_URL` y la `anon key` en `web/index.html`, y que el workflow ya se haya ejecutado al menos una vez (Bloque 3, paso 4) para que haya datos que mostrar.
- **Quieres ampliar las palabras clave o los territorios:** todo eso está al principio del archivo `scripts/ingest_placsp.py`, en las listas `PALABRAS_CLAVE` y `CPV_RELEVANTES_PREFIJOS` — se puede pedir a Claude que las amplíe cuando quieras, sin tocar el resto del código.

---

## Qué falta para tener las 3 capas completas

Este primer paquete implementa la **Capa 1 (PLACSP)** de principio a fin, ya funcional. Las Capas 2 (plataformas autonómicas) y 3 (webs propias tipo Madrid Destino) requieren un conector distinto por cada plataforma, que se puede ir añadiendo al mismo proyecto con nuevos scripts en la carpeta `scripts/` — te los preparo en cuanto quieras seguir con ello.
