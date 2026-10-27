import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.WATCHMATCH_URL || "http://watchmatch:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  reporter: "line",
});
