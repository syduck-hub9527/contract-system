/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// Python 后端（contract_radar）只托管一个页面文件：contract_radar/assets/dashboard.html。
// 构建时把 JS 与 CSS 全部内联进这一个 HTML，Python 服务与安装包都不需要改动，正常运行也不需要 Node。
const OUT_DIR = path.resolve(import.meta.dirname, '../contract_radar/assets')
const BACKEND = process.env.RADAR_BACKEND ?? 'http://127.0.0.1:8765'

function emitAsDashboard(): Plugin {
  return {
    name: 'emit-as-dashboard',
    apply: 'build',
    enforce: 'post',
    generateBundle: {
      // 在 viteSingleFile 内联并删除 JS/CSS 产物之后再改名
      order: 'post',
      handler(_, bundle) {
        const page = bundle['index.html']
        if (!page) this.error('index.html missing from bundle')
        const leftovers = Object.keys(bundle).filter((name) => name !== 'index.html')
        if (leftovers.length) this.error(`未能内联的产物: ${leftovers.join(', ')}`)
        page.fileName = 'dashboard.html'
      },
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile(), emitAsDashboard()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  publicDir: false,
  build: {
    outDir: OUT_DIR,
    // 输出目录里还有 icon.png 与 Python 资源，只覆盖 dashboard.html，绝不清空
    emptyOutDir: false,
    target: 'es2022',
    reportCompressedSize: false,
  },
  server: {
    port: 5173,
    // 开发时把接口与图标转发给本机 Python 后端（python binance_live_dif.py）。
    // 保留浏览器的 Host（localhost:5173），后端的 Host 白名单允许 localhost。
    proxy: {
      '/dif-api': BACKEND,
      '/api': BACKEND,
      '/icon.png': BACKEND,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
