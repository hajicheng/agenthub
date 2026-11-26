import type { NextConfig } from 'next'

const BACKEND_ORIGIN = process.env.AGENTHUB_BACKEND_URL ?? 'http://127.0.0.1:8000'

const nextConfig: NextConfig = {
  output: 'standalone',
  experimental: {
    optimizePackageImports: ['lucide-react', '@base-ui/react', '@radix-ui/react-tabs'],
  },

  // 将所有后端 API 与静态部署预览转发至 FastAPI
  async rewrites() {
    return {
      beforeFiles: [
        {
          source: '/api/:path*',
          destination: `${BACKEND_ORIGIN}/api/:path*`,
        },
        {
          source: '/deployments/:path*',
          destination: `${BACKEND_ORIGIN}/deployments/:path*`,
        },
      ],
      afterFiles: [],
      fallback: [],
    }
  },
}

export default nextConfig
