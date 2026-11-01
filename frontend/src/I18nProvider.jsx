import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { localeFor, readLanguage, translate, writeLanguage } from "./i18n.js";

const I18nContext = createContext(null);
function storage() {
  try { return window.localStorage; } catch { return null; }
}

export function I18nProvider({ children }) {
  const [language, setLanguage] = useState(() => readLanguage(storage()));
  const t = useCallback((key, values) => translate(key, language, values), [language]);
  useEffect(() => {
    writeLanguage(storage(), language);
    document.documentElement.lang = language;
    document.title = translate("ITRTG Event Optimizer", language);
  }, [language]);
  return <I18nContext.Provider value={{ language, setLanguage, t, locale: localeFor(language) }}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
