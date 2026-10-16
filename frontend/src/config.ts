import { createContext, useContext } from "react";

import type { AppConfig } from "./api";

export const FALLBACK_CONFIG: AppConfig = {
  auth_mode: "none",
  retention_hours: 24,
  default_tax_year: new Date().getFullYear() - 1,
  max_upload_mb: 25,
  max_pages: 100,
  llm_model: "",
  source_code_url: null,
};

export const ConfigContext = createContext<AppConfig>(FALLBACK_CONFIG);
export const useConfig = () => useContext(ConfigContext);
