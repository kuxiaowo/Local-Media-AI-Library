/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#172026',
        panel: '#f7f8fa',
        line: '#d9dee5',
        accent: '#1f7a68',
        signal: '#266dd3',
      },
    },
  },
  plugins: [],
};
