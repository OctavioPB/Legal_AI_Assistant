"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertBanner } from "./tools/ReformasView";

const TILES = ["conversar", "investigacion", "argumentar"] as const;
const TOOLS = ["leyes", "verificar", "asuntos", "reformas"] as const;
const STEPS = ["step1", "step2", "step3", "step4"] as const;

/** Start page (Brand.md 8.0): what each workspace is for, in plain words, and how an answer is
 *  built. No configuration here: every choice has a sensible default inside each workspace. */
export function Home() {
  const t = useTranslations("home");
  return (
    <div className="home">
      <section className="home-hero">
        <p className="eyebrow">{t("eyebrow")}</p>
        <h1>{t.rich("title", { em: (c) => <em>{c}</em> })}</h1>
        <p>{t("intro")}</p>
      </section>
      <AlertBanner />
      <section aria-labelledby="home-choose">
        <h2 id="home-choose">{t("choose")}</h2>
        <ul className="home-tiles">
          {TILES.map((k) => (
            <li key={k}>
              <Link className="home-tile" href={`/${k}`}>
                <span className="home-tile-title">{t(`${k}.title`)}</span>
                <span className="home-tile-text">{t(`${k}.text`)}</span>
                <span className="home-tile-when">{t(`${k}.when`)}</span>
                <span className="home-tile-go">{t(`${k}.go`)}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="home-tools">
        <h2 id="home-tools">{t("tools")}</h2>
        <ul className="home-tiles home-tools">
          {TOOLS.map((k) => (
            <li key={k}>
              <Link className="home-tile home-tool" href={`/${k}`}>
                <span className="home-tile-title">{t(`${k}.title`)}</span>
                <span className="home-tile-text">{t(`${k}.text`)}</span>
                <span className="home-tile-go">{t(`${k}.go`)}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section className="home-steps" aria-labelledby="home-how">
        <h2 id="home-how">{t("howTitle")}</h2>
        <ol>
          {STEPS.map((s) => (
            <li key={s}>{t(s)}</li>
          ))}
        </ol>
      </section>
    </div>
  );
}
