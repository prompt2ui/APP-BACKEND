import { defineConfig } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: path.join(__dirname, 'src/test/testing'),
  outputDir: path.join(__dirname, 'src/test/test-result'),
  testMatch: /.*\.(js|ts)/,
  preserveOutput: 'always',

  use: {
    headless: true,
    viewport: { width: 1280, height: 720 },

    trace: {
      mode: 'retain-on-failure',
    },

    video: {
      mode: 'on',
      size: { width: 1280, height: 720 },
    },

    launchOptions: {
      slowMo: 0,
      args: [
        '--font-render-hinting=none',
        '--disable-threaded-scrolling',
        '--disable-threaded-animation',
        '--disable-low-res-tiling',
        '--num-raster-threads=4',
      ],
    },
  },
});
