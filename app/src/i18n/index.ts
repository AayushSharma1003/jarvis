// All user-facing strings load through here (CONTRIBUTING.md rule). The
// backend sends machine codes; `errorText` is the single place they become
// human language.

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export function errorText(code: string): string {
  const key = `error.${code}`;
  return i18n.exists(key) ? i18n.t(key) : i18n.t("error.unknown", { code });
}

export default i18n;
