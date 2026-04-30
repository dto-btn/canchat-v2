import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import istanbul from 'vite-plugin-istanbul';

// /** @type {import('vite').Plugin} */
// const viteServerConfig = {
// 	name: 'log-request-middleware',
// 	configureServer(server) {
// 		server.middlewares.use((req, res, next) => {
// 			res.setHeader('Access-Control-Allow-Origin', '*');
// 			res.setHeader('Access-Control-Allow-Methods', 'GET');
// 			res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
// 			res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
// 			next();
// 		});
// 	}
// };

export default defineConfig({
	plugins: [
		sveltekit(),
		istanbul({
			include: 'src/**/*', //all files at all levels
			exclude: [], //folder or files to be excluded
			extension: ['.js', '.ts', '.svelte'], //extension files to test
			requireEnv: true,
			forceBuildInstrument: true
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true
	},
	worker: {
		format: 'es'
	}
	// uncomment to test llama guard locally through docker backend
	// server: {
	// 	proxy: {
	// 		'/api': {
	// 			target: 'http://localhost:3000',
	// 			changeOrigin: true,
	// 			cookieDomainRewrite: 'localhost'
	// 		},
	// 		'/ollama': {
	// 			target: 'http://localhost:3000',
	// 			changeOrigin: true
	// 		},
	// 		'/ws': {
	// 			target: 'http://localhost:3000',
	// 			changeOrigin: true,
	// 			ws: true
	// 		}
	// 	}
	// }
});
