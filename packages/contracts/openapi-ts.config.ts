import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: './schema/openapi.json',
  output: {
    path: './src',
    format: 'prettier',
    lint: false,
  },
  plugins: [
    '@hey-api/client-axios',
  ],
});
