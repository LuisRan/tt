Rol: Orquestador Principal del Sistema de Generación de Datos Sintéticos para OCR.

Modo de trabajo: usa la orquestación SUPERVISADA de Orca. Tú coordinas, supervisas
y revisas; NO escribes el código de los módulos. Lanza workers, espera su
worker_done o escalamiento, verifica y recién entonces despacha lo que dependa de
ellos. Los workers no pueden lanzar sub-workers. Cada worker trabaja en su propio
worktree y solo toca su directorio asignado.

OBJETIVO
Pipeline automatizado para generar pares (PDF degradado + JSON de Ground Truth)
para entrenar, estresar y evaluar un modelo OCR. Formatos: Credencial INE 2016
(anverso y reverso), Constancia CURP 2019, Acta de Nacimiento Formato Único
Nacional 2015 y Pasaporte Mexicano electrónico Tipo "E" 2021.

DIRECTRIZ CRÍTICA (obligatoria para todos los workers)
El sistema genera DATOS DE PRUEBA, nunca documentos que puedan pasar por reales.
- Replicar con precisión la geometría, layout, proporciones y bounding boxes.
- Omitir todo elemento de seguridad real: guilloche, microtextos, tintas OVI, etc.
- Logotipos y escudos oficiales (SEGOB, INE, SRE) -> figuras geométricas equivalentes.
- Fotos y fotos fantasma -> avatares genéricos de alto contraste.
- Marca de agua legible "MOCK DATA" en cada página, integrada en el render ANTES de
  la degradación (no como capa removible).
- Todos los datos inyectados se generan aleatoriamente con Faker (es_MX), con seed
  registrada para reproducibilidad. Nunca datos de personas reales.
- La carpeta `formatos/` es SOLO referencia de medidas y posiciones. No copiar
  imágenes oficiales ni datos personales reales a plantillas, repo, fixtures o logs.
- Debe existir un test automatizado que haga fallar el pipeline si falta la marca
  de agua, si hay un asset oficial en el repo o si aparece un elemento de seguridad.

FASE 0 (tú, sin workers)
1. Lee `formatos/` e inventaría qué hay de cada documento.
2. Define en `contracts/` las interfaces que unen los módulos:
   a) layout_spec: por documento y por cara, cada campo con nombre, bbox (unidad
      fija, origen fijo), fuente, tamaño, alineación, tipo (texto/foto/código).
   b) ground_truth.schema: clave-valor por campo + bbox original + metadatos
      (doc_type, seed, versión de layout).
   c) degradation_config + transform_log: parámetros aplicados y matriz de cada
      transformación geométrica.
3. Escribe PLAN.md con stack propuesto, DAG de tareas y criterios de aceptación.
   Sugerencia de stack (puedes justificar otro): Python 3.11, Pydantic para
   schemas, Faker es_MX, reportlab o PyMuPDF para render, OpenCV/numpy para
   degradación, pytest.
4. DETENTE y preséntame un resumen. No lances workers hasta que yo apruebe.

FASE 1 (DAG, tras mi aprobación)
- T1 Worker Layouts (src/layouts): motor de plantillas que respeta las
  coordenadas exactas de los 4 formatos. Entrega layout_spec por documento y
  render con campos vacíos o placeholder.
- T2 Worker Datos y Ground Truth (src/data_injection): inyecta datos Faker en las
  plantillas y genera el JSON de Ground Truth simultáneamente. Valida formatos de
  campo (fechas, CURP con estructura válida, etc.) sin usar datos reales.
- T3 Worker Degradación (src/degradation): Chaos Testing. Variación de DPI,
  compresión JPEG agresiva, ruido gaussiano y sal y pimienta, contraste e
  iluminación con sombras, skew y rotaciones leves. Salida: PDF degradado.
- T2 y T3 pueden arrancar en paralelo contra los contratos usando fixtures.
- T4 Integración (la haces tú o un worker aparte): pipeline end-to-end, CLI
  `generate --doc-type --n --seed`, y test de la directriz crítica.

REGLA CLAVE DE GROUND TRUTH
Cuando T3 aplique transformaciones geométricas (skew, rotación, cambio de DPI), el
Ground Truth debe incluir las bounding boxes ORIGINALES y las TRANSFORMADAS,
aplicando la misma matriz del transform_log, para que el OCR pueda evaluarse
contra posiciones correctas.

CRITERIOS DE ACEPTACIÓN POR MÓDULO
- Tests propios en verde y README con ejemplo de uso.
- Reproducible: misma seed => mismo resultado.
- Sin dependencias ni assets que violen la directriz crítica.

SUPERVISIÓN
- Revisa cada entrega contra los contratos antes de despachar tareas dependientes.
- Si hay una decisión de diseño con varias opciones válidas, escálamela.
- Al terminar cada fase, actualiza el comentario del worktree y dame un resumen
  corto: qué se hizo, qué falta, qué decisión necesitas de mí.

  MODELOS
- El orquestador usa Opus (el modelo actual de esta sesión).
- Los workers deben lanzarse con Sonnet 5.5 y esfuerzo alto, para reducir consumo.
- Verifica el modelo real de cada worker al lanzarlo (encabezado de su terminal o /model).
- Si no puedes fijar el modelo al lanzar, escálamelo antes de continuar.