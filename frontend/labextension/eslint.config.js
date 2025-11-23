import eslint from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import stylistic from '@stylistic/eslint-plugin';
import tseslint from 'typescript-eslint';

export default defineConfig([
  eslint.configs.recommended,
  tseslint.configs.recommended,
  globalIgnores([
      'node_modules',
      'dist',
      'lib',
      'coverage',
      '**/*.d.ts',
      'tests',
  ]),
  {
    plugins: {
      '@stylistic': stylistic,
    },
    rules: {
      // ref: https://stackoverflow.com/questions/62915344/eslint-error-when-adding-rule-typescript-eslint-interface-name-prefix
      '@typescript-eslint/naming-convention': [
        'error',
        {
          'selector': 'interface',
          'format': ['PascalCase'],
          'custom': {
            'regex': '^I[A-Z]',
            'match': true
          }
        }
      ],
      '@typescript-eslint/no-unused-vars': ['warn', { args: 'none' }],
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-namespace': 'off',
      '@typescript-eslint/no-use-before-define': 'off',
      '@stylistic/quotes': [
        'error',
        'single',
        { avoidEscape: true, allowTemplateLiterals: 'never' }
      ],
      curly: ['error', 'all'],
      eqeqeq: ['error', 'allow-null'],
      'prefer-arrow-callback': 'error'
    },
  },
]);
