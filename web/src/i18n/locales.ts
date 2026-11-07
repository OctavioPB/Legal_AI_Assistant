import esMX from "./messages/es-MX.json";
import enUS from "./messages/en-US.json";

export const LOCALES = ["es-MX", "en-US"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "es-MX";

export const CATALOGS: Record<Locale, typeof esMX> = {
  "es-MX": esMX,
  "en-US": enUS,
};

export function isLocale(value: string | undefined): value is Locale {
  return LOCALES.includes(value as Locale);
}
