"use strict";

// Minimal ESLint for static/app.js. evaluate.js is exercised by the golden
// gate instead. tests/test_export.py runs this config when eslint is installed.

module.exports = [
  {
    files: ["src/xycalc/static/app.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        document: "readonly",
        module: "writable",
        XY: "readonly",
        CSS: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        requestAnimationFrame: "readonly",
        localStorage: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", caughtErrors: "none" }],
      "no-var": "error",
    },
  },
];
