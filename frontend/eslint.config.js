import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default tseslint.config(
  // Global ignores
  { ignores: ["dist/", "vite.config.ts", "eslint.config.js"] },

  // Base JS recommended rules
  js.configs.recommended,

  // TypeScript strict rules (spreads 3 config objects: base, eslintRecommended overrides, strict rules)
  ...tseslint.configs.strict,

  // React Hooks plugin (flat config preset from v5)
  reactHooks.configs["recommended-latest"],

  // Project-wide settings
  {
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // TODO: Fix non-null assertions with proper type narrowing
      "@typescript-eslint/no-non-null-assertion": "warn",
      // TODO: Replace `any` with proper types
      "@typescript-eslint/no-explicit-any": "warn",
      // TODO: Refactor void type usage in API service generics
      "@typescript-eslint/no-invalid-void-type": "warn",
    },
  },
);
