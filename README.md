# SER Historia — RSS histórico completo para Feedly

Genera un RSS histórico de **SER Historia** a partir de la web oficial de Nacho Ares:

https://nachoares.com/ser-historia/

## Qué hace

- descubre automáticamente el programa más reciente;
- en la primera ejecución recorre el archivo hacia atrás mediante **Programa anterior**;
- conserva el catálogo en `catalog.json`;
- extrae el número de programa, título, **fecha real**, descripción, imagen y el primer reproductor de iVoox de cada ficha;
- no descarga ni redistribuye los audios;
- cada entrada enlaza a la ficha oficial y al reproductor/iVoox;
- actualiza automáticamente cada 6 horas;
- expone 75 entradas recientes + 100 históricas durante 24 h para ayudar a Feedly a indexar progresivamente el archivo completo.

## Puesta en marcha

1. Crea en GitHub un repositorio público llamado, por ejemplo:

   `ser-historia-rss`

2. Sube todos los archivos de este paquete, incluida:

   `.github/workflows/update-feed.yml`

3. En GitHub abre:

   **Actions → Actualizar RSS SER Historia → Run workflow**

4. La primera ejecución será mucho más larga que las siguientes porque tiene que recorrer el archivo histórico.

5. Cuando termine aparecerán:

   - `feed.xml`
   - `catalog.json`
   - `backfill_state.json`

6. En Feedly añade:

   `https://raw.githubusercontent.com/TU_USUARIO/ser-historia-rss/main/feed.xml`

## Histórico en Feedly

Feedly no siempre ingiere de golpe cientos de entradas antiguas. Por eso el RSS mantiene:

- `RECENT_KEEP=75` recientes;
- `BACKFILL_BATCH=100` antiguas;
- cada lote durante `BACKFILL_HOLD_HOURS=24`.

Con más de 900 programas, el archivo completo irá entrando en Feedly por tandas durante varios días.

## Personalización opcional

En GitHub Actions pueden definirse variables de entorno:

- `RECENT_KEEP`
- `BACKFILL_BATCH`
- `BACKFILL_HOLD_HOURS`
- `REQUEST_DELAY`

## Nota

Este proyecto crea únicamente un índice RSS con metadatos y enlaces a las fuentes públicas. No descarga ni aloja los archivos de audio.
