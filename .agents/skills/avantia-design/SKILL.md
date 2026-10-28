---
name: avantia-design
description: Mantiene una interfaz operativa de hospitalidad clara, accesible y coherente con la identidad de AvantIA.
---

# Diseño de AvantIA

Aplica esta guía al crear o modificar pantallas y componentes del frontend de AvantIA.

## Identidad

- Diseña para equipos de alojamientos que revisan conversaciones, disponibilidad, reservas e importaciones durante su jornada. La interfaz debe sentirse clara, humana, fiable y tranquila.
- Usa el tema oscuro de AvantIA: fondo `#0A192F`, superficies `#172A45`, acento y foco `#64FFDA`, texto secundario `#8892B0` y texto principal `#E6F1FF`.
- Mantén Source Sans 3 como única familia tipográfica, servida desde `frontend/src/assets/fonts`. Construye jerarquías con tamaños y pesos moderados; evita serifas, tracking amplio, estilos display y tipografías remotas.
- Mantén los WOFF2 por debajo de 80 KB en conjunto, conserva el aviso OFL y limita el subconjunto a los glifos necesarios para español.
- Usa iconos Lucide importados individualmente y con etiquetas o contexto accesible. No uses emojis ni números ornamentales como iconografía.

## Componentes y estilos

- Sigue [shadcn/ui](https://ui.shadcn.com/) para escoger componentes accesibles y apropiados. AvantIA usa React, Tailwind CSS v4 y componentes fuente en `frontend/src/components/ui`; reutilízalos y amplíalos allí cuando proceda. No instales otra librería visual ni copies componentes que no se utilicen.
- Usa utilidades Tailwind en JSX para el layout, espaciado, responsive y apariencia de cada vista. Mantén `frontend/src/styles.css` reducido a Tailwind, tokens semánticos del tema, fuentes locales, reset y estilos base compartidos.
- Define los componentes sobre tokens semánticos de shadcn (fondo, texto, superficie, borde, primario, foco y estados) en vez de repetir valores hexadecimales. Los cinco colores de marca son la base visual; conserva colores diferenciados y accesibles para éxito, aviso, error e información.
- No añadas reglas CSS específicas por vista ni selectores de clases personalizados. Excepción: estilos mínimos encapsulados que necesite el contenido de correo presentado dentro de su iframe; el correo no debe heredar ni modificar los estilos de la aplicación.
- Importa solo los iconos y componentes que se usen. Mantén animaciones discretas, respeta `prefers-reduced-motion` y evita gradientes, blur, partículas y efectos que resten claridad o añadan peso.
- Usa tarjetas para resúmenes y grupos funcionales; tablas y listas cuando haya que comparar elementos. El color nunca debe ser la única señal de estado.
- No cambies flujos, permisos, contenido de negocio ni contratos de API en una tarea visual.

## Responsive y accesibilidad

- Conserva acciones prioritarias visibles en móvil y adapta columnas y listas según prioridad funcional.
- Usa elementos semánticos, etiquetas de formulario, botones reales, foco visible y contraste legible.
- Conserva cuerpos de datos largos, tablas y planning navegables sin cortar información.

## Revisión antes de entregar

- Revisa al menos escritorio y móvil en las vistas modificadas, incluidos estados de carga, error, vacío y permisos según corresponda.
- Comprueba que Source Sans 3 se usa en toda la interfaz, se carga desde assets locales y que el peso total de WOFF2 no supera 80 KB.
- Ejecuta `npm run build` desde `frontend` y corrige errores introducidos.
