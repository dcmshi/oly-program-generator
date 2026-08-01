/** Tailwind config for oly-agent.
 *
 * The app used the Play CDN (cdn.tailwindcss.com), which is explicitly not for
 * production: it JIT-compiles in the browser on every page load, so there is no
 * caching win and the whole UI is unstyled if unpkg/jsdelivr is unreachable.
 *
 * This build deliberately keeps Tailwind's DEFAULT palette, so the compiled
 * output matches what the CDN was generating and static/theme.css keeps
 * retinting the gray scale. Defining a custom `gray` here would be better (see
 * FE-L2), but it can't be done without touching the templates: the theme maps
 * one token to two colours depending on the property — bg-gray-900 is navy
 * while text-gray-900 is near-black, and bg-gray-100 and border-gray-100 differ
 * too. Untangling that needs class renames across every template.
 */

module.exports = {
  // Every class the CSS must contain has to be reachable from here. Class names
  // are never assembled from fragments in this app — the conditional ones (e.g.
  // status_color/phase_color in web/app.py) return whole literal strings — but
  // those live in Python, hence the app.py entry.
  content: [
    '../templates/**/*.html',
    '../app.py',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'sans-serif'],
        display: ['Barlow Condensed', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
