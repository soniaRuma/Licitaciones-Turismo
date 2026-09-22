-- =====================================================================
-- ESQUEMA DE BASE DE DATOS — Agente de licitaciones de Turismo (minube)
-- Pégalo entero en Supabase > SQL Editor > New query > Run
-- =====================================================================

create table if not exists licitaciones (
  id                  bigint generated always as identity primary key,
  fuente              text not null,               -- 'PLACSP', 'PLACSP_MENOR', 'CAT', 'EUS', 'GAL', 'MAD', 'AND', 'NAV', 'RIOJA', 'GVA', etc.
  capa                text not null,                -- 'capa1', 'capa2', 'capa3'
  expediente          text,                         -- número de expediente del organismo
  atom_entry_id       text unique,                  -- id único del <entry> del ATOM (evita duplicados)
  organismo           text,                         -- nombre del órgano de contratación
  titulo              text,                         -- objeto del contrato / título
  resumen             text,                         -- resumen generado (LLM) del objeto
  cpv                 text,                         -- códigos CPV, separados por coma
  importe              numeric,                      -- importe estimado / presupuesto base sin IVA
  moneda              text default 'EUR',
  fecha_publicacion   date,
  fecha_limite        date,                         -- fecha límite de presentación de ofertas
  criterios_adjudicacion text,                       -- extraído/resumido del pliego (LLM)
  requisitos          text,                         -- requisitos / certificados exigidos (LLM)
  enlace              text,                         -- link directo a la licitación
  estado              text default 'abierta',       -- 'abierta', 'cerrada', 'adjudicada'
  relevante_turismo   boolean default true,         -- resultado del filtro/clasificación
  creado_en           timestamptz default now(),
  actualizado_en      timestamptz default now()
);

-- Índices para que la web filtre rápido
create index if not exists idx_licitaciones_fecha_pub on licitaciones (fecha_publicacion desc);
create index if not exists idx_licitaciones_fecha_limite on licitaciones (fecha_limite);
create index if not exists idx_licitaciones_capa on licitaciones (capa);
create index if not exists idx_licitaciones_organismo on licitaciones (organismo);

-- Búsqueda de texto libre (título + organismo)
create index if not exists idx_licitaciones_busqueda
  on licitaciones using gin (to_tsvector('spanish', coalesce(titulo,'') || ' ' || coalesce(organismo,'')));

-- Row Level Security: la web solo necesita LEER, nunca escribir desde el navegador
alter table licitaciones enable row level security;

create policy "Lectura publica de licitaciones"
  on licitaciones for select
  using (true);

-- (No se crea policy de INSERT/UPDATE/DELETE a propósito:
--  solo el script del cron, usando la "service_role key" —nunca la pública—, podrá escribir)

-- Tabla auxiliar: registro de ejecuciones del cron, para depurar si algún día falla
create table if not exists ejecuciones_log (
  id             bigint generated always as identity primary key,
  ejecutado_en   timestamptz default now(),
  fuente         text,
  nuevas         int,
  actualizadas   int,
  errores        text
);

alter table ejecuciones_log enable row level security;
create policy "Lectura publica de logs"
  on ejecuciones_log for select
  using (true);
