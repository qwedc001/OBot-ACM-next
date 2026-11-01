import { build } from 'esbuild';
import { execSync } from 'child_process';

// Generate DTS
execSync('pnpm exec tsc --declaration --emitDeclarationOnly --outDir dist', { stdio: 'inherit' });

// Bundle with esbuild
await build({
  entryPoints: ['src/index.ts', 'src/sdk.gen.ts', 'src/client.gen.ts', 'src/types.gen.ts'],
  outdir: 'dist',
  bundle: true,
  splitting: true,
  format: 'esm',
  packages: 'external',
  platform: 'neutral',
  target: 'es2022',
});

console.log('Build complete');
