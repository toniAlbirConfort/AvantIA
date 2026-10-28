---
name: avantia-design
description: Mantiene la identidad visual clara y funcional Costa gráfica de AvantIA en su panel operativo de hospitalidad.
---

# Diseño de AvantIA

Aplica esta guía al crear o modificar pantallas y componentes del frontend de AvantIA.

## Identidad

- Diseña para equipos de alojamientos que revisan conversaciones, disponibilidad, reservas e importaciones durante su jornada.
- La interfaz debe sentirse como una herramienta de hospitalidad cuidada: clara, humana, fiable y tranquila. Mantén un tono funcional, sin una estética editorial marcada ni recursos tipográficos decorativos.
- Usa la paleta Costa gráfica definida en `frontend/src/styles.css`: tinta `#172C3B`, azul mar `#28677C`, azul profundo `#1F4D60`, coral `#E9785D`, blanco salino `#F5F3ED`, superficie `#FFFEFA`, texto secundario `#5B6D76` y bordes `#D8E0DF`.
- Usa Source Sans 3 como única familia tipográfica de la interfaz, servida desde `frontend/src/assets/fonts`. Construye la jerarquía con tamaños y pesos moderados (400 para lectura, 600–700 para títulos y acciones); evita serifas, tracking amplio, estilos de display y tipografías remotas.
- Mantén los WOFF2 de fuentes por debajo de 80 KB en conjunto. Conserva el aviso OFL junto al archivo y limita el subconjunto a los glifos necesarios para español.
- Usa iconos Lucide con etiquetas o contexto accesible; no uses emojis ni números ornamentales como iconografía.

## Sistema de interfaz

- Usa los tokens y componentes fuente en `frontend/src/components/ui` como base común. Personaliza sobre ellos; no mezcles Mantine o Chakra.
- Importa solo iconos Lucide que se usen. Mantén las animaciones discretas, y evita gradientes, blur, partículas y efectos que resten claridad o añadan peso.
- Usa tarjetas para resúmenes o grupos funcionales; usa listas y tablas cuando haga falta comparar varios elementos.
- Mantén estados, alertas, controles y vacíos claros. Usa los tokens semánticos de `styles.css` para éxito, aviso, error, información y foco; el color nunca debe ser la única señal de estado.
- No cambies los flujos, permisos, contenido de negocio o contratos de API como parte de una tarea visual.

## Responsive y accesibilidad

- Conserva acciones prioritarias visibles en móvil y adapta columnas y listas por prioridad funcional.
- Usa elementos semánticos, etiquetas de formulario, botones reales, foco visible y contraste legible.
- Respeta `prefers-reduced-motion`. No añadas animación necesaria para entender un estado.
- Conserva cuerpos de datos largos, tablas y planning navegables sin cortar información.

## Revisión antes de entregar

- Revisa al menos escritorio y móvil en las vistas modificadas.
- Comprueba estados de carga, error, vacío y permisos según corresponda.
- Comprueba que Source Sans 3 se usa en toda la interfaz, se carga desde assets locales y que el peso total de WOFF2 no supera 80 KB.
- Ejecuta `npm run build` desde `frontend` y corrige errores introducidos.
