/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f0f0f",
        panel: "#212121",
        line: "#3f3f3f",
        muted: "#aaaaaa",
        accent: "#ff0033",
      },
    },
  },
  plugins: [],
};
