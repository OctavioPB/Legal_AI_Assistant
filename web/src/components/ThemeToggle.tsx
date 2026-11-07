"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { THEMES, type Theme } from "@/lib/theme";

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  document.cookie = `theme=${theme}; path=/; max-age=31536000; samesite=strict`;
}

export function ThemeToggle({ initialTheme }: { initialTheme: Theme }) {
  const t = useTranslations();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  return (
    <div className="segmented" role="group" aria-label={t("nav.theme")}>
      {THEMES.map((value) => (
        <button
          key={value}
          type="button"
          aria-pressed={theme === value}
          onClick={() => {
            setTheme(value);
            apply(value);
          }}
        >
          {t(`theme.${value}`)}
        </button>
      ))}
    </div>
  );
}
