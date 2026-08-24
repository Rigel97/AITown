// ESLint 9 flat config（AGENTS.md 约定：前端 ESLint + Prettier，新代码零警告）
// 只查 src/（游戏代码）；scripts/ 是一次性地图转换工具、宽松处理
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "node_modules/"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.ts"],
    rules: {
      // 本项目红线（AGENTS.md 工程约束）：禁止 any——unknown + 类型守卫代替
      "@typescript-eslint/no-explicit-any": "error",
      // 运行时输入校验已由显式类型守卫覆盖（net/client.ts），no-unsafe-* 噪音大于收益
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },
  {
    // 一次性 Node 工具脚本：手写 Node 全局（免装 globals 包），只保留基础规则
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: {
        console: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        process: "readonly",
      },
    },
  },
);
