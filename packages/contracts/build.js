import { build } from 'esbuild';
import { execSync } from 'child_process';

// Generate DTS
execSync('pnpm exec tsc --declaration --emitDeclarationOnly --outDir dist', { stdio: 'inherit' });

// Bundle with esbuild
await build({
  entryPoints: ['src/index.ts'],
  outfile: 'dist/index.js',
  bundle: true,
  format: 'esm',
  packages: 'external',
  platform: 'neutral',
  target: 'es2022',
});

console.log('Build complete');
