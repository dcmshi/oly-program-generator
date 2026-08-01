/** Tailwind config for oly-agent.
 *
 * The theme used to be applied as !important overrides on Tailwind's default
 * gray scale (FE-L2). That had three costs:
 *
 *  1. Every new gray utility had to be hand-added to the remap list.
 *  2. The remaps were context-blind. `text-gray-400` meant "faint metadata on a
 *     cream card" in most templates and "light link on the navy nav" in
 *     base.html; one value can't be both, and darkening it for the cards took
 *     the nav to 2.58:1.
 *  3. Only grays were remapped, so blue-50/green-100/etc. kept Tailwind's
 *     cool-white-based tints and read as a blue-gray wash on the warm card.
 *
 * So the palette is defined here by role instead: `paper` for surfaces, `line`
 * for borders, `ink` for text on those surfaces, `navy` for the nav and primary
 * buttons (including its own light text shades). Tailwind then generates every
 * utility in the right colour and nothing needs overriding — which is also why
 * the hand-written static/theme.css is gone and its two component classes moved
 * into input.css, where theme() can reach these values.
 *
 * Accent tints (50/100/200) are the family's own 600 mixed into the paper
 * colour — see build_palette.py, which also audits the contrast of every
 * fill/text pair the templates use. Shades 400-900 stay at Tailwind's values:
 * they carry text, their contrast is already tuned, and at that darkness the
 * mixing base isn't perceptible.
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
      colors: {
        // Page background.
        canvas: '#F4F0EA',

        // Surfaces, lightest to strongest.
        paper: {
          DEFAULT: '#FDFBF8',  // cards
          100: '#EDE8E0',      // sunken panels
          150: '#E8E3DB',      // hover on a card or panel
          200: '#E5DFD7',      // deeper fills, progress-bar tracks
          300: '#DDD8CF',      // strongest fill
          400: '#D0CAC1',      // hover on 300
        },

        // Borders and dividers.
        line: {
          DEFAULT: '#DDD8CF',
          faint: '#F2EEE7',    // table row dividers
          light: '#EAE5DD',
          dark: '#C9C4BB',     // input borders
          hover: '#B5AFA8',
        },

        // Text on paper. Every shade clears 4.5:1 on canvas, paper and paper-100
        // (the surfaces that carry text); `ghost` is for icon-only controls,
        // which need 3:1 as a non-text graphic rather than 4.5:1.
        ink: {
          DEFAULT: '#1C2128',
          800: '#2B2520',
          700: '#3D3830',
          sec: '#5C5750',
          muted: '#625D57',
          faint: '#6C6761',
          ghost: '#847F79',
        },

        // Nav and primary buttons, plus the text that sits on them.
        navy: {
          DEFAULT: '#1C2B3A',
          700: '#273D52',      // button hover
          800: '#253040',      // nav hover
          line: '#2A3646',     // nav divider
          100: '#CBD5E1',      // nav links            (9.7:1 on navy)
          200: '#9AA8B8',      // secondary nav text   (6.0:1 on navy)
        },

        // Accents — tints warmed, text shades kept. See build_palette.py.
        amber: {
          50: '#FBF3E9', 100: '#F8E9D6', 200: '#F4DBBE',
          500: '#f59e0b', 600: '#d97706', 700: '#b45309', 800: '#92400e',
        },
        blue: {
          50: '#F0F2F7', 100: '#DFE6F6', 200: '#C9D7F5',
          400: '#60a5fa', 500: '#3b82f6', 600: '#2563eb',
          700: '#1d4ed8', 800: '#1e40af', 900: '#1e3a8a',
        },
        green: {
          50: '#EFF6EE', 100: '#DDEFE0', 200: '#C6E6CE',
          500: '#22c55e', 600: '#16a34a', 700: '#15803d', 800: '#166534',
        },
        orange: { 100: '#FAE4D7', 800: '#9a3412' },
        purple: { 100: '#EEDFF6', 800: '#6b21a8' },
        red: {
          50: '#FBEEEB', 100: '#F8DDDB', 200: '#F5C8C6',
          400: '#f87171', 500: '#ef4444', 600: '#dc2626',
          700: '#b91c1c', 800: '#991b1b',
        },
        yellow: { 100: '#F6EBD6', 600: '#ca8a04', 800: '#854d0e' },
      },
      fontFamily: {
        sans: ['DM Sans', 'sans-serif'],
        display: ['Barlow Condensed', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
