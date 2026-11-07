// Plain module (no "use client") so server and client components can both import the values.
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

export function isTheme(value: string | undefined): value is Theme {
  return THEMES.includes(value as Theme);
}
