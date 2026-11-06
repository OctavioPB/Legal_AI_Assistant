# Brand.md — Fuente de verdad de UI/Frontend (placeholder v0)

Regla: TODO desarrollo de UI/Frontend obedece este archivo (ver PLAN.md, WS6 y WS0.T05).
Si un caso no está cubierto aquí, se extiende este archivo primero — no se improvisa en el componente.

Secciones a completar en WS0.T05:
1. Identidad: logotipo, paleta (primarios/semánticos), modo claro/oscuro.
2. Tipografía: familia UI, familia para texto legal citado (serif recomendada), escalas.
3. Componentes: chips de cita [cite:], bloque <insuficiente>, marcas [razonamiento estratégico],
   selectores de jurisdicción/persona, estados vacíos y de carga.
4. Tono y microcopy bilingüe (es-MX / en-US): formal profesional, sin promesas de asesoría legal.
5. Accesibilidad: contraste AA mínimo, foco visible, navegación por teclado.

Reglas técnicas que Brand.md debe respetar (lecciones de Library-RAG; ver `style.md`):
- **Sin recursos externos:** fuentes y assets autoalojados (con su licencia en un `VERSION.txt`);
  nunca enlazar Google Fonts/CDN/trackers — contexto privilegiado y fuga por referrer.
- **Tokens de color arriba, overrides de marca al final** de la hoja de estilos; una regla previa
  con el mismo selector pierde. El texto pequeño de acento (dorado u otro) sobre fondo claro usa
  el token "ink" de mayor contraste, no el acento puro.
- **Texto jurídico y salida del modelo se renderizan como texto** (nunca HTML crudo).
- **Tres marcas visualmente distintas y estables:** cita verificada `[cite:]`, razonamiento del
  modelo `[razonamiento estratégico]`, y contenido que el sistema no puede respaldar (refusal
  `<insuficiente>`). Lo que añade el modelo sin respaldo se muestra sombreado y rotulado, nunca
  con apariencia de cita.
- **El refusal es un componente de primera clase** (qué falta + acción sugerida), no un error.
- **Microcopy es un catálogo** (es-MX / en-US) con paridad verificada por test; sin literales en
  componentes.
